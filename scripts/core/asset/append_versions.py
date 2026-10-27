# -*- coding: utf-8 -*-
"""在指定画布上「往下追加」新方案节点 —— 改形象专用。

用户口径（2026-09-24 定稿）：
  要改某个角色/场景的形象时，**不要删掉已经出图的那些节点**，
  直接把新方案接在该列的**下面**继续排（同一个 x，y 依次往下加一个行距）。

用法：
  python append_versions.py --names 苏晚意                    # dry-run（画布跟工作区指针走）
  python append_versions.py --names 苏晚意 --commit
  python append_versions.py --kind scene --names 咖啡厅 --commit
  python append_versions.py --kind scene --names auto --commit   # 铺满 docx 里全部场景
  python append_versions.py --kind char  --names auto --commit   # 铺满 docx 里全部人物
  （不传 --canvas 时从 工作区/current_canvas.json 读 canvasId）

硬护栏（写在代码里，不靠人记）：
  1. 目标画布由 --canvas 指定，必须能在项目画布列表里找到，否则拒绝
  2. **只 create，永不 delete、永不 update 已有节点**（本脚本不 import 任何 pay_ 方法）
  3. 新节点名自动续号（该列已有 方案1..3 → 新的接着 方案4..6），不会撞名
  4. 单次 UpdateNode 原子写入
"""
import argparse
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core/子目录）
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
from mc import Mobo, default_project_id, require_project_id  # noqa: E402
import fill_sandbox as FS  # noqa: E402

PROJECT_ID = default_project_id()   # 由 current_canvas.json 决定（换剧自动跟着走）
ROW_PITCH = FS.ROW_Y[1] - FS.ROW_Y[0]      # 444.0 —— 三档行 y 的间距
DOCX_BY_KIND = {
    "char": FS.GEN_OUT / "人物提示词.docx",
    "scene": FS.GEN_OUT / "场景提示词.docx",
}
NODE_RE = re.compile(r"^(?P<base>.+)-方案(?P<n>\d+)$")

# 工作区指针（current_canvas.json）只在 mc.py 里有一份 —— 本文件不再重复定义（2026-09-27 收口）
from mc import POINTER_FILE as CANVAS_JSON, default_canvas_id  # noqa: E402


def find_canvas(m, canvas_id):
    lst = m.read_canvas_list(PROJECT_ID, episode_id="0", shot_id="0", is_episode_mode=False)
    for c in lst or []:
        if str(c.get("id")) == str(canvas_id):
            return c
    return None


def group_columns(nodes):
    """把画布上已有节点按「基础名」分组成列：base -> {x, max_y, max_n, count}"""
    cols = {}
    for n in nodes:
        mm = NODE_RE.match(str(n.get("name") or ""))
        if not mm:
            continue
        base = mm.group("base")
        num = int(mm.group("n"))
        pos = n.get("position") or {}
        x = float(pos.get("x") or 0.0)
        y = float(pos.get("y") or 0.0)
        c = cols.setdefault(base, {"x": x, "max_y": y, "max_n": 0, "count": 0})
        c["max_y"] = max(c["max_y"], y)
        c["max_n"] = max(c["max_n"], num)
        c["count"] += 1
    return cols


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canvas", default=None,
                    help="目标画布 canvasId；不传则从 current_canvas.json 读（默认）")
    ap.add_argument("--names", required=True,
                    help="要追加的角色/场景基础名，逗号分隔（如 苏晚意 或 苏晚意,容晏）")
    ap.add_argument("--kind", default="char", choices=["char", "scene"],
                    help="取哪份 docx 作新提示词来源（默认 char=人物提示词.docx）")
    ap.add_argument("--from-docx", default="", help="直接指定 docx 路径，覆盖 --kind")
    ap.add_argument("--refs", default="",
                    help="参考图绑定 json（key=节点基础名，value={assetId,sourceSeedanceType,...})；"
                         "写入节点 params.imageList，submit 时经 build_files 变成 files，"
                         "提示词里用 @图片1 引用（图生图通道，2026-10-07 反打图 v2 启用）")
    ap.add_argument("--commit", action="store_true", help="真写（默认只 dry-run）")
    args = ap.parse_args()
    require_project_id(PROJECT_ID)      # 指针缺了就停手报错（绝不回退旧项目）

    # 没传 --canvas：读工作区指针（口径只在 mc.default_canvas_id 里）
    args.canvas_from_cli = bool(args.canvas)
    if not args.canvas:
        args.canvas = default_canvas_id()
        if not args.canvas:
            print(f"!! 没传 --canvas，{CANVAS_JSON} 里也没有 canvasId。")
            print("   二选一：① 传 --canvas <id>；② 先跑 prep.py 写 current_canvas.json")
            return 2
        print(f"  (默认从 {CANVAS_JSON.name} 读 canvasId = {args.canvas})")

    docx_path = pathlib.Path(args.from_docx) if args.from_docx else DOCX_BY_KIND[args.kind]
    names = [s.strip() for s in args.names.split(",") if s.strip()]
    if not names:
        print("!! --names 为空"); return 2

    refs = {}
    if args.refs:
        rp = pathlib.Path(args.refs)
        if not rp.exists():
            print(f"!! --refs 文件不存在：{rp}"); return 2
        refs = json.loads(rp.read_text(encoding="utf-8"))
        print(f"  参考图绑定：{rp.name}（{len(refs)} 项）")

    src = FS.parse_prompts(docx_path)            # [(名称, 方案号, 提示词), ...]
    by_name = {}
    for nm, n, pr in src:
        by_name.setdefault(nm, []).append((n, pr))
    for nm in by_name:
        by_name[nm].sort(key=lambda t: t[0])

    # --names auto = 该 docx 里的全部基础名（按首现顺序）。
    # 第一次在空画布上铺满时必须用这个，否则得手打几十个名字。
    if any(s.lower() == "auto" for s in names):
        if len(names) > 1:
            print("!! --names auto 不能和具体名字混用，要么写 auto、要么逐个写")
            return 2
        if not by_name:
            print(f"!! {docx_path.name} 里没解析出任何基础名，检查 docx 标题格式")
            return 2
        names = list(by_name)                    # dict 保序 = 文档里首现顺序
        print(f"  (--names auto → 展开为 {len(names)} 个基础名)")

    print("=" * 78)
    print(f"追加模式：只往该列「下面」加节点，不动也不删任何已有节点")
    print("=" * 78)
    print(f"  目标画布 : canvasId {args.canvas}"
          f"（{'命令行' if args.canvas_from_cli else '工作区 ' + CANVAS_JSON.name}）")
    print(f"  新提示词 : {docx_path.name}  —— {len(src)} 条 / {len(by_name)} 个基础名")
    print(f"  追加对象 : {names}")
    print()

    missing = [n for n in names if n not in by_name]
    if missing:
        print(f"  !! 这些名字在 {docx_path.name} 里没有：{missing}")
        print(f"     该文件里有：{list(by_name)}")
        return 2

    if not args.commit:
        print("[DRY RUN] 不连服务器。下面用画布现状推算每个新节点的坐标需要读一次画布，")
        print("         已跳过。加 --commit 才会读画布 + 计算续号 + 真写。")
        for nm in names:
            print(f"    {nm}: 新提示词 {len(by_name[nm])} 条 "
                  f"（方案{[n for n, _ in by_name[nm]]}）")
        return 0

    m = Mobo()
    c = find_canvas(m, args.canvas)
    if not c:
        print(f"  !! 项目 {PROJECT_ID} 下找不到 canvasId={args.canvas}，中止")
        print(m.dump_audit()); return 2
    print(f"  画布命中 : id={c['id']} name={c['name']!r} rev={c['revision']}")

    detail = m.read_canvas_detail(PROJECT_ID, c["id"], episode_id="0", shot_id="0",
                                  is_episode_mode=False)
    rev = int(detail.get("revision") or 0)
    nodes = detail.get("nodes") or []
    cols = group_columns(nodes)
    print(f"  画布现状 : revision={rev}  节点数={len(nodes)}  已识别 {len(cols)} 列")
    print()

    # 无对应列时，新节点落在最右侧再往右一列。
    # ⚠️ 必须用「每开一列就右推一列」的游标：写成固定值的话，
    #    空画布上一次性铺 N 个名字会全部叠在同一列的坐标上（看着像只写进去一个）。
    max_x = max([float((n.get("position") or {}).get("x") or 0.0) for n in nodes] or [0.0])
    next_new_x = (max_x + FS.COL_PITCH) if nodes else FS.BASE_X

    plan = []          # (新节点名, 提示词, x, y)
    report = []
    for nm in names:
        items = by_name[nm]
        col = cols.get(nm)
        if col:
            x = col["x"]
            y0 = col["max_y"] + ROW_PITCH
            n0 = col["max_n"] + 1
            where = f"接在已有 {col['count']} 个节点下方（该列 x={x:.0f}，最下 y={col['max_y']:.0f}）"
        else:
            x = next_new_x
            next_new_x += FS.COL_PITCH
            y0 = FS.ROW_Y[0]
            n0 = 1
            where = f"画布上没有这一列 → 新开一列 x={x:.0f}"
        for i, (_old_n, pr) in enumerate(items):
            plan.append((f"{nm}-方案{n0 + i}", pr, x, y0 + i * ROW_PITCH))
        report.append((nm, where, n0, len(items), y0))

    for nm, where, n0, cnt, y0 in report:
        print(f"  [{nm}] {where}")
        print(f"        新增 方案{n0}..方案{n0 + cnt - 1}，y = "
              f"{', '.join(f'{y0 + i * ROW_PITCH:.0f}' for i in range(cnt))}")
    print()
    print(f"  合计新增 {len(plan)} 个节点（画布将从 {len(nodes)} → {len(nodes) + len(plan)}）")
    print()

    create = FS.make_create_list(plan)

    # 参考图绑定：把 imageList 写进节点 params（submit_canvas2.build_files 会读它生成 files，
    # 提示词里的 @图片N 按 files 数组下标一一对应）。
    ref_hit = 0
    for node in create:
        mm = NODE_RE.match(node["name"])
        base = mm.group("base") if mm else node["name"]
        ref = refs.get(base) or refs.get(node["name"])
        if not ref or not str(ref.get("assetId") or "").strip():
            continue
        obj = json.loads(node["data"])
        obj["params"]["imageList"] = [{
            "nodeId": node["nodeKey"], "mediaType": "image",
            "label": ref.get("label") or base,
            "url": ref.get("url") or "",
            "assetId": str(ref["assetId"]).strip(),
            "sourceSeedanceType": int(ref.get("sourceSeedanceType", 5)),
            "width": ref.get("width", 16), "height": ref.get("height", 9),
        }]
        node["data"] = json.dumps(obj, ensure_ascii=False)
        ref_hit += 1
    print(f"  参考图绑定命中 {ref_hit}/{len(create)} 个节点"
          f"{'（refs 里其余 key 未命中将被忽略）' if ref_hit < len(refs) else ''}")
    print()
    print("=" * 78)
    print(f"提交 UpdateNode（create {len(create)} 项，delete 0 项，baseRevision={rev}）")
    print("=" * 78)
    t0 = time.time()
    resp = m.wr_update_nodes(PROJECT_ID, c["id"], rev, create=create)
    dt = time.time() - t0
    print(f"  HTTP 耗时 {dt:.2f}s  ->  revision="
          f"{(resp or {}).get('revision') if isinstance(resp, dict) else '?'}")

    outdir = HERE / "canvas_append"
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (outdir / f"sent_append_{stamp}.json").write_text(
        json.dumps(create, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print("=" * 78)
    print("回读校验（GetDetail）")
    print("=" * 78)
    back = m.read_canvas_detail(PROJECT_ID, c["id"], episode_id="0", shot_id="0",
                                is_episode_mode=False)
    got = {n["nodeKey"]: n for n in (back.get("nodes") or [])}
    ok = bad = 0
    for node in create:
        g = got.get(node["nodeKey"])
        if not g:
            print(f"  !! 丢失 {node['name']}"); bad += 1; continue
        want_p = json.loads(node["data"])["params"]
        have_p = json.loads(g["data"])["params"]
        if want_p["prompt"] != have_p["prompt"]:
            print(f"  !! 提示词不一致 {node['name']}"); bad += 1; continue
        w_ids = [str(i.get("assetId")) for i in (want_p.get("imageList") or [])
                 if isinstance(i, dict)]
        h_ids = [str(i.get("assetId")) for i in (have_p.get("imageList") or [])
                 if isinstance(i, dict)]
        if w_ids != h_ids:
            print(f"  !! imageList 不一致 {node['name']}: want={w_ids} got={h_ids}"); bad += 1
            continue
        ok += 1
    print(f"  新增提示词逐字一致 {ok}/{len(create)}   异常 {bad}")
    print(f"  画布节点总数 {len(got)}（写入前 {len(nodes)}，应为 {len(nodes) + len(create)}）")
    print(f"  已有节点是否被改动: {'无' if len(got) == len(nodes) + len(create) else '!! 数量不对，人工确认'}")
    print()
    print(m.dump_audit())
    (outdir / "audit.txt").write_text(m.dump_audit(), encoding="utf-8")

    # 回读结果落盘 —— render_preview.py 只认 <目录>/readback.json。
    # 之前 append 不落盘，导致「刚追加完就渲染预览」会把上一轮 42 节点的旧快照
    # 渲染出来（预览看着少三行，还以为没写进去）。目标目录按 canvasId 认：
    # 命中 canvas2/meta.json 就写 canvas2/（就是画布2 的产物目录），否则写 canvas_append/。
    rb_dir = outdir
    meta = HERE / "canvas2" / "meta.json"
    try:
        if meta.exists() and str(json.loads(meta.read_text(encoding="utf-8"))
                                 .get("canvasId")) == str(c["id"]):
            rb_dir = HERE / "canvas2"
    except Exception:
        pass
    (rb_dir / "readback.json").write_text(
        json.dumps(back, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  回读快照已更新 -> {rb_dir / 'readback.json'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
