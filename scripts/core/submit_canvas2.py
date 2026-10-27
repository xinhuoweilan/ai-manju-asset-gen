# -*- coding: utf-8 -*-
"""
画布2 —— 提交生成任务（**唯一会花钱的脚本**）。

用法：
  python submit_canvas2.py                                              # ① 只估价：清单 + 总价，不提交
  python submit_canvas2.py --node 苏晚意-方案1 --max-spend 1 --commit
  python submit_canvas2.py --node A --node B --node C --parallel 3 --max-spend 1 --commit
  python submit_canvas2.py --limit 3 --max-spend 1 --commit
  python submit_canvas2.py --all --max-spend 10 --commit

硬护栏（写在代码里，不靠人记）：
  1. 没有 --commit 就**绝不调用** CreateGenerationTask（只走只读估价）
  2. 有 --commit 但没有 --max-spend 直接拒跑
  3. 实际估价 > --max-spend 直接拒跑
  4. 提交前打印「将花多少钱」，提交后原始请求/响应 + 起止时间落盘留证
  5. 串行模式出错即停；并行模式（--parallel N）一次性发出 N 个，用 clone() 独立 Session
"""
import argparse
import json
import pathlib
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core）
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
from mc import Mobo, MoboError, default_project_id, require_project_id  # noqa: E402
from mc import POINTER_FILE as CANVAS_JSON, require_canvas_id  # noqa: E402

PROJECT_ID = default_project_id()   # 由 current_canvas.json 决定
# ⚠️ 不再有 FALLBACK_CANVAS 兜底（2026-09-27 二次收口）：本脚本是**全链唯一扣费**的地方，
#    读不到 canvasId 就必须停手 —— 否则会拿旧画布去花钱。见 mc.POINTER_HINT。

_PRINT_LOCK = threading.Lock()


def log(msg):
    with _PRINT_LOCK:
        print(msg)


def build_files(node):
    """★★★ 从节点的 params.imageList 生成 `files` —— **这才是参考图真正的绑定通道**。

    ⚠️⚠️ 2026-10-06 二次修复（这是「给了参考图、形象还是不对」的第二层根因）：

      第一层根因是 `@图片1` 被画布改写成 nodeKey（已由 resolve_prompt 修掉），
      但改完仍然不对 —— 因为**参考图根本就没喂进去**。

      真正的机制（从前端 chunk_010.js 反推，证据确凿）：

        I8 = refs => {
          const t = [], n = [];
          refs.forEach(a => {
            const i = (a.assetId ?? "").trim();
            const c = Number(a.sourceSeedanceType);
            if (!i)   { n.push({...reason:"missing-asset-id"});   return }
            if (!isFinite(c)) { n.push({...reason:"invalid-source-type"}); return }
            t.push({ assetId: i, sourceSeedanceType: c })        // ← files
          })
          return n.length ? {ok:!1, invalidReferences:n} : {ok:!0, files:t}
        }

      而 refs（references）由 `vu({graph, targetNodeId})` 推导：
        - 先取该节点的**连线**（connections 里 targetNodeId === 本节点）
        - 再按 `imageListOrder` / `mixedListOrder` 排序
        - 每个上游节点用 `G0(node)` 提取
            { nodeId, mediaType, label, url, assetId, sourceSeedanceType, width, height }
          （assetId 来自节点 data 的当前选中素材）

      ⇒ **`files` 数组顺序 = `@图片N` 的 N（1-based）**
      ⇒ 提交时若 `files: []`，模型收到**零张参考图**，等于纯文生图，形象全凭想象 —— 这就是现象。

      反证对比（第1集原生「分镜 6」任务快照 generationDataJson）：
        files = [{"assetId":"1407099","sourceSeedanceType":4},   ← 人物形象
                 {"assetId":"1407340","sourceSeedanceType":0},
                 {"assetId":"1407402","sourceSeedanceType":0},
                 {"assetId":"1407587","sourceSeedanceType":0}]
        prompt 里写着「@图片1作为小美形象参考 / @图片2作为场景参考 ...」
        ⇒ 一一对应，确认无疑。

      ⚠️ 注意：服务端**不存** materials（查询返回恒为 []），快照里的 materials 也只是回显；
         **真正被消费的是 `files`**。

    imageList 每项形如：
      {"nodeId":"i-storyboard-1tizi8y","mediaType":"image",
       "label":"Elena Vitiello（码头）","url":"https://...",
       "assetId":"1420040","sourceSeedanceType":4,"width":100,"height":56}
    """
    p = json.loads(node["data"]).get("params", {})
    out = []
    for it in (p.get("imageList") or []):
        if not isinstance(it, dict):
            continue
        aid = str(it.get("assetId") or it.get("seedanceId") or "").strip()
        if not aid:
            continue                       # 前端 I8 对缺 assetId 的项直接报错丢弃
        try:
            st = int(it.get("sourceSeedanceType"))
        except (TypeError, ValueError):
            st = 0                          # 前端要求 Number.isFinite，兜底 0（自定义上传）
        out.append({"assetId": aid, "sourceSeedanceType": st})
    return out


def resolve_prompt(p):
    """决定提交给模型的 prompt 文本（★ 2026-10-06 修，这是「形象参考失效」的根因）。

    画布节点里有**两个** prompt 字段：

      screenPrompt —— 我在编辑器里写的**原文**，`@图片1 / @图片2` 字面量完整保留 ✅
      prompt       —— 画布规范化后的版本，`@图片1` 被改写成 `@i-storyboard-1tizi8y` ❌

    为什么 `prompt` 不能用（从前端 chunk_010.js 反推，证据确凿）：

      const a = /@图片([1-9]\\d*)|@([^\\s@，,。；;、]+)/g;
      const c = i[1] ? `图片${i[1]}` : i[2];
      const l = i[1] ? t[Number(i[1]) - 1]                       // ← @图片N：按数组下标取 ✅
                     : t.find(o => [o.name, 文件名(o.serverData?.path), 文件名(o.url)]
                                     .includes(c));              // ← @其它：按 name/文件名匹配 ❌

      ⇒ `@图片N` 走下标分支，稳；
        `@i-storyboard-xxx`（nodeKey）既不等于节点 name、也不等于文件名 ⇒ **匹配失败**，
        该位置的参考图**绑定不上**，模型只看到一段无意义的 `@i-storyboard-xxx` 文本。

    实测（2026-10-06 12:38 那次）：4 个引用全被改写成 nodeKey ⇒ 4 张参考图全部没绑上
    ⇒ 出图的人物长相/服装与参考图完全不符。

    ⇒ 所以：**优先用 screenPrompt**；只有它缺失或没写 `@` 引用时才退回 prompt。
    """
    sp = (p.get("screenPrompt") or "").strip()
    pr = (p.get("prompt") or "").strip()
    if sp and ("@图片" in sp or "@" in sp):
        return sp
    return pr or sp


def build_image_data(node, *, create_num=1, img_model=None, quality=None, resolution=None,
                     aspect_ratio=None):
    """照抄前端 image-submit 的 data 结构。

    ⚠️ 三处关键（2026-10-06 两次修复）：
      1. `imagePrompt` 必须用 `resolve_prompt()` 取 screenPrompt —— 否则 `@图片N`
         被画布改写成 nodeKey，参考图全部绑不上（见 resolve_prompt 的说明）。【第一层根因】
      2. `files` 才是**参考图实际喂给模型**的数组，必须由 imageList 生成
         （见 build_files 的说明）。若填 []，等于纯文生图，人物形象全凭想象。【第二层根因】
      3. `materials` 是画布侧的资产标记，服务端不消费（查询恒返回 []），
         保留只为与前端结构一致、便于审计对照。
    """
    p = json.loads(node["data"]).get("params", {})
    st = p.get("settings") or {}
    files = build_files(node)
    return {
        "createNum": int(create_num),
        "imagePrompt": resolve_prompt(p),
        "imgModel": int(img_model if img_model is not None else p.get("imgModel", 1)),
        "imageEditType": int(p.get("imageEditType", 0) or 0),
        "enhance": bool(p.get("enhance", False)),
        "aspectRatio": int(aspect_ratio if aspect_ratio is not None else st.get("ratio", 2)),
        "imageQuality": int(quality if quality is not None else st.get("quality", 3)),
        "resolution": str(resolution if resolution is not None else st.get("resolution", "2K")),
        "files": files,                                   # ★ 参考图通道（真正的绑定入口）
        "materials": [{"seedanceId": f["assetId"], "seq": i}
                      for i, f in enumerate(files, start=1)],   # 仅回显对齐，服务端不消费
    }


def submit_one(cli, project_id, canvas_id, node, data):
    """提交单个节点，返回结构化结果（记录起止时间，用于证明是否真并行）。"""
    t0 = time.time()
    try:
        r = cli.pay_create_generation_task(project_id, canvas_id, node["id"], data)
        t1 = time.time()
        return {
            "name": node["name"], "nodeId": node["id"], "ok": True,
            "taskBatchNo": (r or {}).get("taskBatchNo"),
            "tasks": (r or {}).get("tasks") or [],
            "t_start": round(t0 - T0, 3), "t_end": round(t1 - T0, 3),
            "seconds": round(t1 - t0, 3),
        }
    except MoboError as e:
        t1 = time.time()
        return {
            "name": node["name"], "nodeId": node["id"], "ok": False, "error": str(e),
            "t_start": round(t0 - T0, 3), "t_end": round(t1 - T0, 3),
            "seconds": round(t1 - t0, 3),
        }


def main():
    global T0
    ap = argparse.ArgumentParser()
    ap.add_argument("--canvas", default=None,
                    help="目标画布 canvasId；不传则从 current_canvas.json 读")
    ap.add_argument("--node", action="append", default=None,
                    help="按名称提交节点，可重复：--node A --node B")
    ap.add_argument("--limit", type=int, help="按画布顺序提交前 N 个")
    ap.add_argument("--all", action="store_true", help="提交画布上全部节点")
    ap.add_argument("--parallel", type=int, default=1, help="并发线程数（默认 1 = 串行）")
    ap.add_argument("--create-num", type=int, default=1, help="每节点出图张数（默认 1）")
    ap.add_argument("--max-spend", type=float, default=None, help="本次允许花的钱上限（元）")
    ap.add_argument("--commit", action="store_true", help="真提交（默认只估价）")
    args = ap.parse_args()
    require_project_id(PROJECT_ID)      # 指针缺了就停手报错（绝不回退旧项目）

    # ---------- 目标画布（读不到就停手 —— 本脚本是全链唯一扣费点）----------
    if args.canvas:
        canvas_id, src = str(args.canvas), "命令行 --canvas"
    else:
        canvas_id, src = require_canvas_id(), CANVAS_JSON.name
    outdir = HERE / f"canvas_{canvas_id}"
    outdir.mkdir(parents=True, exist_ok=True)
    print(f"  目标画布 canvasId = {canvas_id}（来源：{src}）  →  产物目录 {outdir.name}/")

    m = Mobo()

    detail = m.read_canvas_detail(PROJECT_ID, canvas_id, episode_id="0", shot_id="0",
                                  is_episode_mode=False)
    nodes = detail.get("nodes") or []
    print(f"画布 {canvas_id} 节点数 = {len(nodes)}  revision = {detail.get('revision')}")

    # ---------- 选目标 ----------
    if args.node:
        by_name = {n.get("name"): n for n in nodes}
        picked, missing = [], []
        for nm in args.node:                       # 保持用户给的顺序
            (picked if nm in by_name else missing).append(by_name.get(nm) or nm)
        if missing:
            print(f"!! 找不到这些节点名：{missing}")
            print("   可选：")
            for n in nodes:
                print("   ", n.get("name"))
            return 2
    elif args.limit:
        picked = nodes[:args.limit]
    elif args.all:
        picked = nodes
    else:
        picked = [nodes[0]] if nodes else []
        print("（未指定 --node/--limit/--all，默认只针对第 1 个节点估价）")

    # ---------- 估价 ----------
    print()
    print("=" * 78)
    print("待提交清单与单价")
    print("=" * 78)
    total = 0.0
    rows = []
    for n in picked:
        d = build_image_data(n, create_num=args.create_num)
        price = m.read_price(1, _id=d["imgModel"])
        unit = float((price or {}).get("preDeductAmount") or 0)
        cost = unit * d["createNum"]
        total += cost
        rows.append((n, d, unit, cost))
        print(f"  {n['name']:<26} id={n['id']:<8} imgModel={d['imgModel']:<4} "
              f"{d['resolution']:<3} 单价 {unit:.2f} × {d['createNum']} = {cost:.2f}")
    print()
    print(f"  >>> 合计 {len(picked)} 个节点，预计花费 ¥{total:.2f}")
    print(f"  >>> 提交方式：{'串行' if args.parallel <= 1 else f'并行 {args.parallel} 线程'}")

    if not args.commit:
        print()
        print("=" * 78)
        print("[只估价] 没有调用任何扣费接口。要真提交请同时给 --commit 和 --max-spend。")
        print("=" * 78)
        print(m.dump_audit())
        (outdir / "submit_estimate.json").write_text(
            json.dumps({"count": len(picked), "total": round(total, 2),
                        "items": [{"name": n["name"], "nodeId": n["id"], "unit": u, "cost": c,
                                   "data": d} for n, d, u, c in rows]},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    # ---------- 护栏 ----------
    if args.max_spend is None:
        print("!! 已给 --commit 但没给 --max-spend，拒跑（防止误花大钱）")
        return 3
    if total > args.max_spend:
        print(f"!! 预计花费 ¥{total:.2f} > 上限 ¥{args.max_spend:.2f}，拒跑")
        return 4

    # ---------- 真提交 ----------
    print()
    print("=" * 78)
    mode = "串行" if args.parallel <= 1 else f"并行 {args.parallel} 线程"
    print(f"真提交（{mode}）：{len(picked)} 个节点，预计 ¥{total:.2f}（上限 ¥{args.max_spend:.2f}）")
    print("=" * 78)
    T0 = time.time()
    results = []

    if args.parallel <= 1:
        for n, d, unit, cost in rows:
            r = submit_one(m, PROJECT_ID, canvas_id, n, d)
            results.append(r)
            if r["ok"]:
                print(f"  [OK ] {r['name']:<24} taskBatchNo={r['taskBatchNo']}  "
                      f"tasks={len(r['tasks'])}  {r['seconds']}s")
            else:
                print(f"  [ERR] {r['name']:<24} -> {r['error']}")
                print("  出错即停（避免连环扣费）")
                break
    else:
        with ThreadPoolExecutor(max_workers=args.parallel) as ex:
            futs = {}
            for n, d, unit, cost in rows:
                futs[ex.submit(submit_one, m.clone(), PROJECT_ID, canvas_id, n, d)] = n
            for f in as_completed(futs):
                r = f.result()
                results.append(r)
                tag = "OK " if r["ok"] else "ERR"
                extra = (f"taskBatchNo={r['taskBatchNo']}  tasks={len(r['tasks'])}"
                         if r["ok"] else r["error"])
                print(f"  [{tag}] {r['name']:<24} 起 {r['t_start']:>6.2f}s → 止 {r['t_end']:>6.2f}s  "
                      f"({r['seconds']}s)  {extra}")

    # 按提交顺序重排结果
    order = {n["name"]: i for i, (n, _, _, _) in enumerate(rows)}
    results.sort(key=lambda r: order.get(r["name"], 99))
    wall = time.time() - T0
    done = sum(1 for r in results if r["ok"])
    print()
    print(f"  >>> 实际成功 {done}/{len(rows)}   墙钟总耗时 {wall:.2f}s   "
          f"（各请求耗时之和 {sum(r['seconds'] for r in results):.2f}s）")

    stamp = time.strftime("%Y%m%d-%H%M%S")
    (outdir / f"submit_result_{stamp}.json").write_text(
        json.dumps({"total_est": round(total, 2), "done": done, "wall_seconds": round(wall, 3),
                    "parallel": args.parallel, "results": results},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print("审计：")
    print(m.dump_audit())
    return 0 if done == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
