# -*- coding: utf-8 -*-
"""资产库 ↔ 项目角色/场景 —— 只读匹配报告（零成本，不写任何东西）

用法（不传 `--project` 就读 `current_canvas.json`；给了 `--canvas` 则以画布反查为准）：
    python -X utf8 asset_match.py
    python -X utf8 asset_match.py --canvas 14286          # 自动从画布反查项目
    python -X utf8 asset_match.py --keyword 温霖
    python -X utf8 asset_match.py --json report.json

匹配口径：
    资产名 `苏晚意-方案1`  →  去后缀 `-方案N` / `（N）`  →  `苏晚意`  →  与角色名精确比
    命中角色 ⇒ 可绑定到该角色的「定妆照」；命中场景 ⇒ 可绑定到该场景的「场景图」
    两边都不命中 ⇒ 列进「孤儿资产」，需要人工确认
"""
import argparse
import json
import pathlib
import re
import sys

BASE = pathlib.Path(__file__).resolve().parent.parent.parent  # scripts/（本文件在 scripts/core/asset）
sys.path.insert(0, str(BASE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径

import mc  # noqa

# 资产名后缀：-方案1 / -方案2（2）/ （1） 等
SUFFIX_RE = re.compile(r"(?:[-_\s]*方案\s*\d+)+(?:\s*[（(]\d+[）)])?\s*$")
PAREN_RE = re.compile(r"\s*[（(]\d+[）)]\s*$")


def strip_suffix(name: str) -> str:
    s = name.strip()
    s = PAREN_RE.sub("", s)
    s = SUFFIX_RE.sub("", s)
    s = PAREN_RE.sub("", s)
    return s.strip()


def page_all(m, path: str, body: dict, key: str = "list") -> list:
    """把分页接口翻完"""
    out = []
    page = 1
    while True:
        b = dict(body)
        b["pageIndex"] = page
        r = m.post(path, b, raw=True)
        d = r.get("data") or {}
        lst = d.get(key) or []
        if not isinstance(lst, list) or not lst:
            break
        out.extend(lst)
        total = d.get("totalCount")
        if not total or len(out) >= int(total):
            break
        page += 1
        if page > 60:
            break
    return out


def resolve_project(m, canvas_id=None, canvas_name=None):
    """从画布反查项目"""
    r = m.post("/api/Project/GetPageList", {"pageIndex": 1, "pageSize": 100}, raw=True)
    projects = (r.get("data") or {}).get("list") or []
    for p in projects:
        pid = str(p.get("id"))
        try:
            c = m.read_canvas_list(pid, episode_id="0", shot_id="0", is_episode_mode=False)
        except Exception:
            continue
        if isinstance(c, dict):
            canvases = None
            for k in ("list", "items", "records"):
                if isinstance(c.get(k), list):
                    canvases = c[k]
                    break
            canvases = canvases or []
        elif isinstance(c, list):
            canvases = c
        else:
            canvases = []
        for cc in canvases:
            if not isinstance(cc, dict):
                continue
            cid, cname = str(cc.get("id")), str(cc.get("name") or cc.get("canvasName") or "")
            if (canvas_id and cid == str(canvas_id)) or (canvas_name and cname == canvas_name):
                return p, cc
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="项目 id；不传读工作区 current_canvas.json")
    ap.add_argument("--canvas", help="画布 id（自动反查项目）")
    ap.add_argument("--canvas-name", help="画布名（自动反查项目）")
    ap.add_argument("--keyword", default=None, help="额外关键字检索（角色/场景/资产名）")
    ap.add_argument("--json", default=None, help="把报告落一份 json")
    args = ap.parse_args()

    m = mc.Mobo()
    pid = mc.require_project_id(args.project)   # 指针缺了就停手（不回退旧项目）
    canvas = None
    if args.canvas or args.canvas_name:
        proj, canvas = resolve_project(m, args.canvas, args.canvas_name)
        if not proj:
            print("!! 没找到画布所属项目")
            return 3
        pid = str(proj.get("id"))
        print(">>> 画布 %s(%s) → 项目 %s (%s)" % (
            canvas.get("id"), canvas.get("name"), pid, proj.get("projectName")))

    r = m.post("/api/Project/GetPageList", {"pageIndex": 1, "pageSize": 100}, raw=True)
    pinfo = next((p for p in ((r.get("data") or {}).get("list") or [])
                  if str(p.get("id")) == str(pid)), {})
    code = pinfo.get("projectName") or "?"

    print("=" * 78)
    print("项目 %s   代码/名称 %s   题材 %s   集数 %s" % (
        pid, code, pinfo.get("theme") or "-", pinfo.get("episodeCount") or "-"))
    print("=" * 78)

    roles = page_all(m, "/api/SeedanceScreenPlay/GetProjectActor",
                     {"projectId": pid, "pageSize": 9999})
    scenes = page_all(m, "/api/SeedanceScreenPlay/GetSeedanceSceneList",
                      {"projectId": pid, "pageSize": 9999})
    assets = page_all(m, "/api/ProjectSeedanceAsset/GetListV2",
                      {"projectId": pid, "pageSize": 200})
    print("角色 %d / 场景 %d / 资产 %d" % (len(roles), len(scenes), len(assets)))
    print()

    role_by_name = {str(x.get("name") or "").strip(): x for x in roles}
    scene_by_name = {str(x.get("name") or "").strip(): x for x in scenes}

    # 资产按 base 名分组
    groups = {}
    for a in assets:
        nm = str(a.get("assetName") or "").strip()
        if not nm:
            continue
        groups.setdefault(strip_suffix(nm), []).append(a)

    hit_role, hit_scene, orphan = {}, {}, {}
    for base, items in groups.items():
        if base in role_by_name:
            hit_role[base] = items
        elif base in scene_by_name:
            hit_scene[base] = items
        else:
            orphan[base] = items
        # 模糊：资产名里含角色名（如「苏晚意（办公室）」）
        if base not in role_by_name and base not in scene_by_name:
            for rn in role_by_name:
                if rn and len(rn) >= 2 and rn in base:
                    hit_role.setdefault(rn, []).extend(items)
                    orphan.pop(base, None)
                    break
            else:
                for sn in scene_by_name:
                    if sn and len(sn) >= 3 and sn in base:
                        hit_scene.setdefault(sn, []).extend(items)
                        orphan.pop(base, None)
                        break

    def dump(title, mapping, note=""):
        print("-" * 78)
        print("%s —— %d 组 %s" % (title, len(mapping), note))
        print("-" * 78)
        for k in sorted(mapping):
            names = ", ".join(sorted({str(x.get("assetName")) for x in mapping[k]}))
            print("   %-24s ← %s" % (k, names[:110]))
        print()

    dump("① 能直接对上【角色】的资产", hit_role, "（可绑到角色定妆照）")
    dump("② 能直接对上【场景】的资产", hit_scene, "（可绑到场景图）")

    print("-" * 78)
    print("③ 孤儿资产（既不是角色名、也不是场景名）—— %d 组" % len(orphan))
    print("-" * 78)
    for k in sorted(orphan):
        names = ", ".join(sorted({str(x.get("assetName")) for x in orphan[k]}))
        print("   %-28s ← %s" % (k, names[:110]))
    print()

    print("-" * 78)
    print("④ 项目里有、但资产库没有对应资产的【角色】—— %d 个" % len(
        [r for r in role_by_name if r not in hit_role]))
    print("-" * 78)
    miss = [r for r in role_by_name if r not in hit_role]
    print("   " + " / ".join(sorted(miss)))
    print()

    print("-" * 78)
    print("⑤ 项目里有、但资产库没有对应资产的【场景】—— %d 个" % len(
        [s for s in scene_by_name if s not in hit_scene]))
    print("-" * 78)
    miss2 = [s for s in scene_by_name if s not in hit_scene]
    print("   " + " / ".join(sorted(miss2)))
    print()

    if args.keyword:
        kw = args.keyword
        print("=" * 78)
        print("关键字检索：%s" % kw)
        print("=" * 78)
        print("  角色命中:", [x.get("name") for x in roles if kw in str(x.get("name"))] or "无")
        print("  场景命中:", [x.get("name") for x in scenes if kw in str(x.get("name"))] or "无")
        print("  资产命中:", sorted({str(a.get("assetName")) for a in assets if kw in str(a.get("assetName"))}) or "无")
        print()

    if args.json:
        rep = {
            "projectId": pid, "projectCode": code,
            "counts": {"roles": len(roles), "scenes": len(scenes), "assets": len(assets)},
            "roleBound": {k: [str(x.get("assetName")) for x in v] for k, v in hit_role.items()},
            "sceneBound": {k: [str(x.get("assetName")) for x in v] for k, v in hit_scene.items()},
            "orphan": {k: [str(x.get("assetName")) for x in v] for k, v in orphan.items()},
            "roleWithoutAsset": sorted(miss),
            "sceneWithoutAsset": sorted(miss2),
        }
        pathlib.Path(args.json).write_text(
            json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print("报告已落盘:", args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
