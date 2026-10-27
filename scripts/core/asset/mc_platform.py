# -*- coding: utf-8 -*-
"""mc_platform.py —— ★ 只读「平台状态体检」（原 inspect.py，因与标准库 inspect 撞名而改）（零扣费、不写任何东西）

一次收束原先 7 个一次性探测脚本：
  _approve_probe / _status_probe / _review_probe / _human_review_probe /
  _fanda_doors / _e21_actor_images / _e15_recon2|3

━━ 判据（2026-10-09 从前端 chunk 挖出的映射表，别再猜）━━━━━━━━━━━━━━━━━━━━
  素材条目 imageList[] 里两个独立维度：
    · humanReviewStatus ：0=通过（界面绿标签 mc_pass #6EC660）｜1=待审核｜2=不通过
    · assetStatus       ：3=审核通过(approved)｜2=审核中｜1=待处理｜0=初始化｜-1=未定稿｜4=处理失败
    · makeupStatus      ：0=定稿区｜2=变体区｜1=其他区（**与"通过与否"无关**）
    · appearanceEpisode ：角色图属于哪几集
  ⇒ 判「通过」= humanReviewStatus==0 且 assetStatus==3
  ⇒ 判「该用哪张」= makeupStatus==0（角色再叠加 appearanceEpisode）
  ⇒ 下载图片只认 httpImageUrl（带 ?sign= 签名），**别自己拼域名**

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PY="python"
  cd "<asset-gen>"

  "$PY" -X utf8 core/asset/inspect.py --passed               # 通过清单（反打/换装第一步）
  "$PY" -X utf8 core/asset/inspect.py --scenes               # 场景库明细
  "$PY" -X utf8 core/asset/inspect.py --actors [--name Elena]# 角色库明细
  "$PY" -X utf8 core/asset/inspect.py --refs "旧仓库"          # 某场景/角色的可用参考图 + 签名URL
  "$PY" -X utf8 core/asset/inspect.py --refs "旧仓库" --download --out "<目录>"
  "$PY" -X utf8 core/asset/inspect.py --canvases             # 分镜画布清单（名/canvasId/episodeId）
  "$PY" -X utf8 core/asset/inspect.py --nodes 14248          # 某画布节点清单（只读）
  [--project 123456] [--json] [--out-dir <目录>]
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
import mc  # noqa: E402

PASSED = "通过"      # humanReviewStatus==0
DEFAULT_OUT = mc.CFG.asset_root   # --download 的默认落点（剧名子目录由调用方给）


# ---------------------------------------------------------------- 基础
def page_all(m, path, body):
    """翻完分页"""
    out = []
    page = 1
    while True:
        b = dict(body)
        b["pageIndex"] = page
        d = (m.post(path, b, raw=True).get("data") or {})
        lst = d.get("list") or []
        if not isinstance(lst, list) or not lst:
            break
        out.extend(lst)
        total = d.get("totalCount")
        if not total or len(out) >= int(total) or page > 60:
            break
        page += 1
    return out


def scene_name(x):
    return str(x.get("name") or x.get("sceneName") or "")


def actor_name(x):
    return str(x.get("actorName") or x.get("name") or "")


def imgs(x):
    return x.get("imageList") or []


def is_passed(im):
    return im.get("humanReviewStatus") == 0 and im.get("assetStatus") == 3


def pick_final(x):
    """该场景/角色「该用的那张」：定稿区优先，否则第一个通过的"""
    lst = imgs(x)
    fin = [i for i in lst if i.get("makeupStatus") == 0]
    if fin:
        return fin[0]
    ok = [i for i in lst if is_passed(i)]
    return ok[0] if ok else (lst[0] if lst else None)


def short(x, n=64):
    return (x or "")[:n]


# ---------------------------------------------------------------- 取数据
def get_scenes(m, pid):
    return page_all(m, "/api/SeedanceScreenPlay/GetSeedanceSceneList",
                    {"projectId": pid, "pageSize": 9999})


def get_actors(m, pid):
    return page_all(m, "/api/SeedanceScreenPlay/GetProjectActor",
                    {"projectId": pid, "pageSize": 9999})


# ---------------------------------------------------------------- 各视图
def view_passed(m, pid, as_json):
    rows = []
    for kind, items, nmf in (("场景", get_scenes(m, pid), scene_name),
                             ("角色", get_actors(m, pid), actor_name)):
        for it in items:
            ok = [i for i in imgs(it) if is_passed(i)]
            if not ok:
                continue
            final = next((i for i in ok if i.get("makeupStatus") == 0), ok[0])
            rows.append({
                "kind": kind, "name": nmf(it),
                "assetId": final.get("id"), "assetName": final.get("assetName"),
                "makeupStatus": final.get("makeupStatus"),
                "appearanceEpisode": final.get("appearanceEpisode"),
                "httpImageUrl": final.get("httpImageUrl"),
                "otherPassedCount": len(ok) - 1,
            })
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("★ 通过清单：humanReviewStatus==0 且 assetStatus==3 —— 共 %d 项" % len(rows))
    print("=" * 96)
    for r in rows:
        ep = ("  ep=%s" % r["appearanceEpisode"]) if r["appearanceEpisode"] else ""
        print("[%s] %-22s assetId=%-9s 定稿=%s  另有通过 %d 张%s" % (
            r["kind"], r["name"], r["assetId"], r["assetName"],
            r["otherPassedCount"], ep))
    return 0


def view_scenes(m, pid, as_json):
    sc = get_scenes(m, pid)
    if as_json:
        print(json.dumps([{"name": scene_name(s), "images": [
            {k: i.get(k) for k in ("id", "assetName", "humanReviewStatus",
                                   "assetStatus", "makeupStatus")} for i in imgs(s)]}
            for s in sc], ensure_ascii=False, indent=1))
        return 0
    print("场景 %d 个（hr=humanReviewStatus 0通过/1待审/2不通过；mk=makeupStatus 0定稿/2变体/1其他）" % len(sc))
    print("=" * 96)
    for s in sc:
        print("【%s】" % scene_name(s))
        if not imgs(s):
            print("    （无图）")
        for i in imgs(s):
            print("    %-34s id=%-9s hr=%s st=%s mk=%s%s" % (
                short(i.get("assetName"), 34), i.get("id"), i.get("humanReviewStatus"),
                i.get("assetStatus"), i.get("makeupStatus"),
                "  ←通过" if is_passed(i) else ""))
    return 0


def view_actors(m, pid, name, as_json):
    rows = []
    for a in get_actors(m, pid):
        nm = actor_name(a)
        if name and name not in nm:
            continue
        rows.append({
            "name": nm, "id": a.get("id"),
            "images": [{"assetId": i.get("id"), "assetName": i.get("assetName"),
                        "ep": i.get("appearanceEpisode"), "hr": i.get("humanReviewStatus"),
                        "assetStatus": i.get("assetStatus"), "mk": i.get("makeupStatus"),
                        "passed": is_passed(i), "httpImageUrl": i.get("httpImageUrl")}
                       for i in imgs(a)],
        })
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("角色 %d 个%s" % (len(rows), ("（筛选：%s）" % name) if name else ""))
    print("=" * 96)
    for r in rows:
        print("【%s】id=%s  共 %d 张" % (r["name"], r["id"], len(r["images"])))
        for i in r["images"]:
            print("    id=%-9s ep=%-22s hr=%s st=%s mk=%s %s %s" % (
                i["assetId"], i["ep"], i["hr"], i["assetStatus"], i["mk"],
                "←通过" if i["passed"] else "     ", short(i["assetName"], 30)))
    return 0


def view_refs(m, pid, want, download, out_dir, as_json):
    """某场景/角色名的可用参考图（含 httpImageUrl）；--download 则落盘"""
    hits = []
    for kind, items, nmf in (("场景", get_scenes(m, pid), scene_name),
                             ("角色", get_actors(m, pid), actor_name)):
        for it in items:
            if nmf(it) != want:
                continue
            for i in imgs(it):
                hits.append({"kind": kind, "owner": nmf(it),
                             "assetId": i.get("id"), "assetName": i.get("assetName"),
                             "ep": i.get("appearanceEpisode"),
                             "hr": i.get("humanReviewStatus"), "assetStatus": i.get("assetStatus"),
                             "mk": i.get("makeupStatus"), "passed": is_passed(i),
                             "url": i.get("httpImageUrl")})
    if not hits:
        print("!! 没找到名为「%s」的场景或角色" % want)
        return 2
    if as_json:
        print(json.dumps(hits, ensure_ascii=False, indent=1))
    else:
        print("「%s」的图 %d 张（只认 httpImageUrl 下载）" % (want, len(hits)))
        print("=" * 96)
        for h in hits:
            print("  id=%-9s %-26s ep=%-20s hr=%s st=%s mk=%s %s" % (
                h["assetId"], short(h["assetName"], 26), h["ep"], h["hr"],
                h["assetStatus"], h["mk"], "←通过" if h["passed"] else ""))
    if download:
        target = pathlib.Path(out_dir) if out_dir else DEFAULT_OUT
        target.mkdir(parents=True, exist_ok=True)
        print()
        for h in hits:
            if not h["passed"]:
                continue
            url = h["url"]
            if not url:
                print("  !! %s 无 httpImageUrl，跳过" % h["assetId"])
                continue
            ext = ".jpg" if url.split("?")[0].lower().endswith((".jpg", ".jpeg")) else ".png"
            dest = target / ("%s_%s%s" % (h["assetName"] or h["owner"], h["assetId"], ext))
            try:
                r = m.s.get(url, timeout=120)
                r.raise_for_status()
                dest.write_bytes(r.content)
                print("  ↓ %s  (%d KB)" % (dest.name, len(r.content) // 1024))
            except Exception as e:
                print("  !! 下载失败 %s：%s" % (h["assetId"], e))
    return 0


def view_canvases(m, pid, as_json):
    r = m.read_canvas_list(pid, episode_id="0", shot_id="0", is_episode_mode=True)
    items = []
    if isinstance(r, dict):
        for k in ("list", "items", "records"):
            if isinstance(r.get(k), list):
                items = r[k]
                break
    elif isinstance(r, list):
        items = r
    rows = [{"name": str(c.get("name") or ""), "canvasId": str(c.get("id")),
             "episodeId": str(c.get("episodeId") or ""), "revision": c.get("revision")}
            for c in items if isinstance(c, dict)]
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("分镜画布 %d 张" % len(rows))
    print("=" * 96)
    for r_ in rows:
        print("  canvasId=%-8s epId=%-9s rev=%-6s %s" % (
            r_["canvasId"], r_["episodeId"], r_["revision"], r_["name"]))
    return 0


def view_nodes(m, pid, canvas_id, as_json, episode_id="0", episode_mode=False):
    # ★ 分镜画布必须 isEpisodeMode=true + episodeId（否则读到的是资产模式下的空/旧内容）
    d = m.read_canvas_detail(pid, canvas_id, episode_id=str(episode_id), shot_id="0",
                             is_episode_mode=bool(episode_mode))
    nodes = (d or {}).get("nodes") or []
    rows = []
    for n in nodes:
        _d = n.get("data")
        _p = n.get("position") or {}
        rows.append({"id": str(n.get("id")), "type": n.get("type"), "name": n.get("name"),
                     "status": n.get("status"),
                     "hasImage": bool(str(n.get("resultContent") or "").strip()),
                     "x": _p.get("x"), "y": _p.get("y"),
                     "action": (_d.get("action") if isinstance(_d, dict) else None)})
    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    print("画布 %s 节点 %d 个" % (canvas_id, len(rows)))
    print("=" * 96)
    for r_ in rows:
        print("  id=%-9s type=%-3s status=%-3s 有图=%-5s %-22s %s" % (
            r_["id"], r_["type"], r_["status"], r_["hasImage"],
            short(r_["action"], 22), short(r_["name"], 40)))
    return 0


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="只读平台状态体检（零扣费）")
    ap.add_argument("--project", default=None, help="项目 id；不传读工作区指针")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--passed", action="store_true", help="通过清单（hr==0 且 assetStatus==3）")
    g.add_argument("--scenes", action="store_true", help="场景库明细")
    g.add_argument("--actors", action="store_true", help="角色库明细")
    g.add_argument("--refs", metavar="NAME", help="某场景/角色的可用参考图")
    g.add_argument("--canvases", action="store_true", help="分镜画布清单")
    g.add_argument("--nodes", metavar="CANVAS_ID", help="某画布的节点清单")
    ap.add_argument("--episode-id", default="0", help="配合 --nodes：分镜画布要传该集 episodeId")
    ap.add_argument("--episode-mode", action="store_true",
                    help="配合 --nodes：分镜画布必须加（否则读到的是资产模式下的旧/空内容）")
    ap.add_argument("--name", default=None, help="配合 --actors 做角色名筛选")
    ap.add_argument("--download", action="store_true", help="配合 --refs：下载通过的图")
    ap.add_argument("--out-dir", default=None, help="--download 的落点目录")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    m = mc.Mobo()
    pid = mc.require_project_id(a.project)

    if a.passed:
        return view_passed(m, pid, a.json)
    if a.scenes:
        return view_scenes(m, pid, a.json)
    if a.actors:
        return view_actors(m, pid, a.name, a.json)
    if a.refs:
        return view_refs(m, pid, a.refs, a.download, a.out_dir, a.json)
    if a.canvases:
        return view_canvases(m, pid, a.json)
    if a.nodes:
        return view_nodes(m, pid, a.nodes, a.json,
                          episode_id=a.episode_id, episode_mode=a.episode_mode)
    return 0


if __name__ == "__main__":
    sys.exit(main())
