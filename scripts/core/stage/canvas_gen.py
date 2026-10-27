# -*- coding: utf-8 -*-
"""canvas_gen.py —— ★ 画布内「图片生成」通用引擎（毕业自 _e15_gen_stage.py）

一条命令管完：上传参考图 → 建节点 → 提交出图 → 轮询下载 → 验收。
内建 preflight 断言（files / sst / @图片N / measured / 估价），默认全程 dry-run。

━━ plan.json（唯一输入）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{
  "project": "123456",
  "canvas": "14248",  "episode_id": "742273",  "is_episode_mode": true,
  "base_x": 100,  "measured": {"width": 625, "height": 350},
  "img_model": 26,  "aspect_ratio": 1,  "resolution": "2K",  "quality": 3,
  "items": [
    {
      "name": "示例E21_3D站位图①_门口与皮椅",
      "y": 7170,
      "prompt": "@ 图片参考绑定（严格按序号对应）:\\n@图片1 作为 Elena Vitiello 的形象参考\\n…",
      "refs": [
        {"assetId": "1430123", "sst": 4, "label": "Elena Vitiello（第21集形象）", "nodeKey": ""},
        {"assetId": "1396615", "sst": 5, "label": "旧仓库（3）", "nodeKey": ""}
      ]
    }
  ]
}
> refs 顺序 = `@图片N` 顺序；`nodeKey` 可留空（提交用的是 prompt 里的 @图片N）。
> `measured` 必须与参考图里**生成的图**的真实比例一致（横 16:9 → 625×350）。

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PY="python"
  cd "<asset-gen>"

  # 0) 上传一张本地图当参考（普通上传 sst=0）→ 打印 assetId，填回 plan
  "$PY" -X utf8 core/stage/canvas_gen.py --upload "<素材目录>\示例E21_3D站位图①.png" --name "示例E21_3D站位图①"

  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/E21.json --check        # 只读校验
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/E21.json --nodes        # 建/刷节点（免费）
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/E21.json --commit --yes # 🔴 提交出图
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/E21.json --wait         # 轮询 + 下载
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/E21.json --verify       # 验收（比例）
"""
import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
sys.path.insert(0, str(HERE / "core" / "asset"))
sys.path.insert(0, str(HERE / "core" / "ops"))

import mc  # noqa: E402
import mc_upload  # noqa: E402
from canvas_fetch import wait_and_download  # noqa: E402
from preflight import (PreflightError, check_generation_task, check_measured,  # noqa: E402
                       estimate, image_size)

DEFAULT_MEAS = {"width": 625.0, "height": 350.0}
SST_UPLOAD = 0


def p(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 上传参考图
def sst_of(name):
    """按名字猜 sst（普通上传=0）；显式传 --sst 可覆盖"""
    return SST_UPLOAD


def cmd_upload(m, pid, path, name, sst, project_override=None):
    src = pathlib.Path(path)
    if not src.exists():
        p("!! 文件不存在：%s" % src)
        return 2
    asset_name = name or src.stem
    p("上传 %s → 资产名「%s」(sst=%s)" % (src.name, asset_name, sst))
    info = mc_upload.upload_file(str(src), name=asset_name)
    p("  COS 直传完成：key=%s" % str(info.get("url"))[:80])
    payload = {"assetName": asset_name, "projectId": pid,
               "cosUrl": info.get("url") or info.get("httpPath"),
               "duplicateMode": 1, "seedanceAssetType": sst}
    r = m.post("/api/ProjectSeedanceAsset/SaveCosUpload", payload, raw=True)
    p("  SaveCosUpload → %s" % json.dumps(r, ensure_ascii=False)[:220])

    def dig(o):
        if isinstance(o, dict):
            v = o.get("data")
            if isinstance(v, (str, int)) and str(v).strip() not in ("", "0"):
                return str(v).strip()
            for k in ("assetId", "id", "seedanceId"):
                v = o.get(k)
                if v not in (None, "", 0, "0"):
                    return str(v)
            for v in o.values():
                got = dig(v)
                if got:
                    return got
        elif isinstance(o, list):
            for v in o:
                got = dig(v)
                if got:
                    return got
        return ""
    aid = dig(r)
    if aid:
        p("\n★ assetId = **%s** ← 填进 plan 的 refs" % aid)
    else:
        p("\n!! 没从返回里认出 assetId，请手工核对上面回显")
    return 0


# ---------------------------------------------------------------- plan
def load_plan(path):
    d = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    d.setdefault("base_x", 100.0)
    d.setdefault("measured", dict(DEFAULT_MEAS))
    d.setdefault("img_model", 26)
    d.setdefault("aspect_ratio", 1)
    d.setdefault("resolution", "2K")
    d.setdefault("quality", 3)
    d.setdefault("is_episode_mode", False)
    d.setdefault("episode_id", "0")
    d.setdefault("shot_id", "0")
    return d


def refs_of(item):
    """→ [{"assetId","sourceSeedanceType"}, ...]（给 preflight 用）"""
    return [{"assetId": str(r.get("assetId")), "sourceSeedanceType": r.get("sst")}
            for r in item.get("refs") or []]


def files_of(item):
    return [{"assetId": str(r.get("assetId")), "sourceSeedanceType": r.get("sst"), "_nodeKey": r.get("nodeKey") or ""}
            for r in item.get("refs") or []]


def materials_of(item):
    return [{"seedanceId": str(r.get("assetId")), "seq": i + 1}
            for i, r in enumerate(item.get("refs") or [])]


def build_params(plan, item):
    rl = item.get("refs") or []
    image_list = [{"nodeId": r.get("nodeKey") or "", "mediaType": "image",
                   "label": r.get("label") or "", "url": "",
                   "assetId": str(r.get("assetId")),
                   "sourceSeedanceType": r.get("sst"),
                   "width": 100, "height": 56} for r in rl]
    prompt = item["prompt"]
    return {
        "prompt": prompt, "imagePrompt": prompt, "screenPrompt": prompt,
        "model": "", "imgModel": plan["img_model"], "imageEditType": 0, "enhance": False,
        "count": 1, "lightingEffects": [],
        "settings": {"resolution": plan["resolution"], "ratio": plan["aspect_ratio"],
                     "quality": plan["quality"]},
        "advancedSettings": {},
        "cameraControl": {"enabled": False, "camera": "panavision_dxl2",
                          "lens": "arri_signature_prime", "focal": "35mm", "aperture": "f/4"},
        "textList": [], "imageList": image_list, "videoList": [], "audioList": [],
        "mixedList": list(image_list), "modeType": "text2image", "files": [],
    }


def node_create_item(plan, item):
    data = {"url": [], "sourceSeedanceType": 0, "alt": item["name"],
            "action": "image_generate", "generatorType": "default",
            "params": build_params(plan, item), "isStale": False}
    return {"nodeKey": mc.new_node_key("image"), "name": item["name"], "type": 2,
            "isResultNode": 0, "status": 1, "isUploaded": 0,
            "position": {"x": plan["base_x"], "y": item.get("y", 0)}, "measured": dict(plan["measured"]),
            "parentNodeKey": None, "zIndex": 2, "data": json.dumps(data, ensure_ascii=False)}


# ---------------------------------------------------------------- 校验 / 建 / 交
def cmd_check(plan):
    bad = 0
    for i, it in enumerate(plan["items"], 1):
        try:
            est = check_generation_task(refs_of(it), files_of(it), it["prompt"], count=1)
            p("  ✓ %-34s refs=%d  %s" % (it["name"], len(it.get("refs") or []), est))
        except PreflightError as e:
            bad += 1
            p("  ✗ %-34s %s" % (it["name"], str(e).split("\n")[0]))
    p()
    p("估价：GPT Image 2 ¥0.12 × %d = **¥%.2f**" % (len(plan["items"]), 0.12 * len(plan["items"])))
    if bad:
        p("!! %d 项未过校验，**别提交**" % bad)
        return 3
    p("→ %d 项全部通过。可以 --nodes 建节点。" % len(plan["items"]))
    return 0


def cmd_nodes(m, plan, only):
    pid, cid = plan["project"], plan["canvas"]
    d = m.read_canvas_detail(pid, cid, episode_id=plan["episode_id"], shot_id=plan["shot_id"],
                             is_episode_mode=plan["is_episode_mode"])
    rev, nodes = d.get("revision"), d.get("nodes") or []
    existing = {str(n.get("name")): n for n in nodes}
    p("画布 %s revision=%s 节点 %d" % (cid, rev, len(nodes)))
    creates, refreshes = [], []
    for i, it in enumerate(plan["items"], 1):
        if only and i != only:
            continue
        try:
            check_generation_task(refs_of(it), files_of(it), it["prompt"])
        except PreflightError as e:
            p("!! %s 未过校验，跳过：%s" % (it["name"], str(e).split("\n")[0]))
            continue
        if it["name"] in existing:
            n = existing[it["name"]]
            nd = json.loads(n.get("data") or "{}")
            nd["params"] = build_params(plan, it)
            nd["isStale"] = False
            refreshes.append({"nodeKey": n.get("nodeKey"), "name": it["name"], "type": n.get("type"),
                              "isResultNode": n.get("isResultNode", 0), "status": n.get("status", 1),
                              "isUploaded": n.get("isUploaded", 0), "position": n.get("position"),
                              "measured": n.get("measured"), "parentNodeKey": n.get("parentNodeKey"),
                              "zIndex": n.get("zIndex"), "data": json.dumps(nd, ensure_ascii=False),
                              "resultContent": n.get("resultContent")})
            p("  ↻ %s 已存在 → 刷新参数" % it["name"])
        else:
            creates.append(node_create_item(plan, it))
            p("  + %s (y=%s, refs=%d)" % (it["name"], it.get("y"), len(it.get("refs") or [])))
    if not creates and not refreshes:
        p("没有要写的节点。")
        return 0
    r = m.wr_update_nodes(pid, cid, rev, create=creates or None, update=refreshes or None,
                          episode_id=plan["episode_id"], shot_id=plan["shot_id"])
    p(">>> UpdateNode（返回 %s）" % json.dumps(r, ensure_ascii=False)[:160])
    d2 = m.read_canvas_detail(pid, cid, episode_id=plan["episode_id"], shot_id=plan["shot_id"],
                              is_episode_mode=plan["is_episode_mode"])
    p("复核 revision=%s 节点 %d" % (d2.get("revision"), len(d2.get("nodes") or [])))
    m.dump_audit()
    return 0


def cmd_commit(m, plan, only, yes):
    items = [it for i, it in enumerate(plan["items"], 1) if not only or i == only]
    if not yes:
        p("!! 这是**扣费**动作：%d 张 × ¥0.12 = **¥%.2f**" % (len(items), 0.12 * len(items)))
        p("   确认后加 `--yes` 重跑。")
        return 2
    pid, cid = plan["project"], plan["canvas"]
    d = m.read_canvas_detail(pid, cid, episode_id=plan["episode_id"], shot_id=plan["shot_id"],
                             is_episode_mode=plan["is_episode_mode"])
    byname = {str(n.get("name")): n for n in (d.get("nodes") or [])}
    for it in items:
        n = byname.get(it["name"])
        if not n:
            p("!! 画布上没有节点「%s」，先跑 --nodes" % it["name"])
            continue
        files = [{"assetId": f["assetId"], "sourceSeedanceType": f["sourceSeedanceType"]}
                 for f in files_of(it)]
        prompt = it["prompt"]
        try:
            est = check_generation_task(refs_of(it), files, prompt, count=1)
        except PreflightError as e:
            p("!! %s preflight 不通过，跳过：%s" % (it["name"], str(e).split("\n")[0]))
            continue
        data = {"createNum": 1, "imagePrompt": prompt, "imgModel": plan["img_model"],
                "imageEditType": 0, "enhance": False, "aspectRatio": plan["aspect_ratio"],
                "imageQuality": plan["quality"], "resolution": plan["resolution"],
                "files": files, "materials": materials_of(it)}
        p("\n>>> CreateGenerationTask %s nodeId=%s" % (it["name"], n.get("id")))
        p("    files=%s" % json.dumps(files, ensure_ascii=False))
        p("    %s" % est)
        try:
            r = m.pay_create_generation_task(pid, cid, int(n.get("id")), data,
                                             episode_id=plan["episode_id"], shot_id=plan["shot_id"])
            p("    返回 %s" % json.dumps(r, ensure_ascii=False)[:200])
        except Exception as e:
            p("    !! 提交失败：%s" % str(e)[:300])
    p("\n### 审计")
    m.dump_audit()
    return 0


def cmd_wait(m, plan, out, prefix):
    got = wait_and_download(m, plan["project"], plan["canvas"], name_prefix=prefix,
                            out_dir=out, episode_id=plan["episode_id"],
                            shot_id=plan["shot_id"], is_episode_mode=plan["is_episode_mode"])
    return 0 if got else 3


def cmd_verify(plan, out):
    """验收：下载回来的图与 measured 比例是否一致（md5 由调用方另行比对）"""
    out = pathlib.Path(out)
    bad = 0
    for it in plan["items"]:
        hits = list(out.glob("%s*.png" % it["name"][:20]))
        if not hits:
            p("  … %s 还没下载到 %s" % (it["name"], out))
            continue
        f = sorted(hits, key=lambda x: x.stat().st_mtime)[-1]
        w, h = image_size(f)
        try:
            check_measured(plan["measured"], w, h)
            p("  ✓ %-34s %dx%d  比例 %.3f 与节点框一致" % (f.name, w, h, w / h if h else 0))
        except PreflightError as e:
            bad += 1
            p("  ✗ %-34s %s" % (f.name, str(e).split("\n")[0]))
    p()
    p("验收%s" % ("**不通过**：%d 项比例不符" % bad if bad else "通过"))
    return 3 if bad else 0


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="画布内图片生成（默认 dry-run）")
    ap.add_argument("--plan", default=None, help="plan.json 路径")
    ap.add_argument("--upload", default=None, help="上传本地图当参考 → 打印 assetId")
    ap.add_argument("--name", default=None, help="配合 --upload：资产名")
    ap.add_argument("--sst", type=int, default=SST_UPLOAD, help="配合 --upload：类型（默认 0 普通上传）")
    ap.add_argument("--project", default=None)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只读校验（preflight + 估价）")
    g.add_argument("--nodes", action="store_true", help="建/刷新节点（免费）")
    g.add_argument("--commit", action="store_true", help="提交出图（🔴 需 --yes）")
    g.add_argument("--wait", action="store_true", help="轮询 + 下载")
    g.add_argument("--verify", action="store_true", help="验收（比例）")
    ap.add_argument("--only", type=int, default=0)
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--out", default=None, help="--wait/--verify 的落点目录")
    ap.add_argument("--prefix", default=None, help="--wait 的节点名前缀")
    a = ap.parse_args()

    m = mc.Mobo()
    if a.upload:
        pid = mc.require_project_id(a.project or (json.loads(pathlib.Path(a.plan).read_text(encoding="utf-8")).get("project") if a.plan else None))
        return cmd_upload(m, pid, a.upload, a.name, a.sst)

    if not a.plan:
        p("!! 需要 --plan（或用 --upload 上传参考图）")
        return 2
    plan = load_plan(a.plan)
    plan["project"] = mc.require_project_id(a.project or plan.get("project"))
    p("plan：%s  画布=%s ep=%s  %d 项" % (pathlib.Path(a.plan).name, plan["canvas"],
                                          plan["episode_id"], len(plan["items"])))
    out = pathlib.Path(a.out) if a.out else (HERE / "_v3_out")
    prefix = a.prefix

    if a.check:
        return cmd_check(plan)
    if a.nodes:
        return cmd_nodes(m, plan, a.only)
    if a.commit:
        return cmd_commit(m, plan, a.only, a.yes)
    if a.wait:
        return cmd_wait(m, plan, out, prefix)
    if a.verify:
        return cmd_verify(plan, out)
    p("（没给动作；用 --check / --nodes / --commit / --wait / --verify）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
