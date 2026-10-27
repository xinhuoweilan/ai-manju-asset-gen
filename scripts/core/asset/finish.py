# -*- coding: utf-8 -*-
"""finish.py —— AI 漫剧出图「收尾」：一条命令跑完 3 步（**只读，零扣费**）

它替代的是技能里那段靠模型临场循环 `GetDetail` 看 `node.status`、再手动调
`download_canvas_images.py`、再想「临时文件清了没」的编排。

跑法：
    PY="python"
    cd "<仓库根>"

    "$PY" -X utf8 finish.py                       # 等出图 → 下载归档到 H 盘 → 清临时
    "$PY" -X utf8 finish.py --no-wait             # 不等，现在已出多少就下多少
    "$PY" -X utf8 finish.py --folder 示例剧          # 覆盖剧名文件夹
    "$PY" -X utf8 finish.py --out "D:/tmp/图"      # 覆盖整个落点
    "$PY" -X utf8 finish.py --dry-run              # 只看会下哪些，不落盘

三步：
    1. 轮询 `Canvas/GetDetail`，直到**没有节点还处于「生成中」(status==2)**，或超时
       （不判「全部 == 3」—— 画布上常留着一批从没提交过的老节点，那个条件永远不成立）
    2. 下载该画布**已出图**的全部节点 → `<ASSET_ROOT>\\<剧名文件夹>\\人物场景资产图\\`
       （复用 `download_canvas_images.py`：幂等、先写 .part、清 24h 前的 .part 残留）
    3. 清临时：`prune_artifacts` 的保留策略（`sent_*` / `prompt_backup_*` 每组只留最近 5 份，
       送回收站可还原）由第 2 步末尾自动带走

落点从 `工作区/current_canvas.json` 的 `folder` 字段读（prep.py 写的）；
读不到就用 `--folder`；再没有就报候选并退出，**不猜**。

退出码：0 = 完成  2 = 需要人工拍板（不知道放哪个剧名文件夹）  4 = 超时 / 没有可下的图
"""
import argparse
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core/子目录）
sys.path.insert(0, str(HERE))

import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
# 工作区指针（current_canvas.json）与「项目号」口径**只在 mc.py 里有一份**，别在本文件重复定义。
from mc import (CFG, POINTER_FILE as CANVAS_JSON,  # noqa: E402
                load_pointer, require_project_id)

H_ROOT = CFG.asset_root          # 素材根目录：.env → ASSET_ROOT
SUBDIR = "人物场景资产图"

EXIT_ASK = 2
EXIT_NOTHING = 4

# 口径：1=待处理（铺好了但**没提交生成**）/ 2=生成中 / 3=已完成
STATUS_TXT = {0: "待处理", 1: "未提交", 2: "生成中", 3: "已完成", 4: "失败", 5: "已取消"}
BUSY = 2  # 只有这个状态值得等


def hr(title=""):
    print()
    print("=" * 78)
    if title:
        print(title)
        print("=" * 78)


def status_counts(nodes):
    c = {}
    for n in nodes:
        c[n.get("status")] = c.get(n.get("status"), 0) + 1
    return c


def fmt_counts(nodes):
    c = status_counts(nodes)
    parts = []
    for k in sorted(c, key=lambda x: (x is None, x)):
        label = STATUS_TXT.get(k, f"未知({k})")
        parts.append(f"{label} {c[k]}")
    return " / ".join(parts) if parts else "（无节点）"


def wait_for_generation(m, project_id, canvas_id, timeout, interval, dry_run):
    """轮询到「没有 status==2 的节点」为止。返回 (detail, timed_out)"""
    t0 = time.time()
    last = None
    while True:
        detail = m.read_canvas_detail(project_id, canvas_id, episode_id="0",
                                      shot_id="0", is_episode_mode=False)
        nodes = detail.get("nodes") or []
        busy = [n for n in nodes if n.get("status") == BUSY]
        done = [n for n in nodes if n.get("resultContent")]
        el = time.time() - t0
        print(f"   [{el:6.1f}s] {fmt_counts(nodes)}   已出图 {len(done)} 个")

        if not busy:
            return detail, False

        if dry_run:
            print("   (--dry-run：不等待，直接结束观察)")
            return detail, False

        if el + interval > timeout:
            print(f"   !! 等满 {timeout}s 仍有 {len(busy)} 个节点在生成中 —— 超时返回")
            print("      提示：4K 三张并行实测要 5~6 分钟，可用 --timeout 加大")
            return detail, True

        last = nodes
        time.sleep(interval)


def resolve_out(args, ptr, canvas_id):
    """定归档落点。返回 (out_path, 说明) 或 (None, 需要人工拍板的提示)"""
    if args.out:
        return pathlib.Path(args.out), "--out 显式指定"

    folder = args.folder or ptr.get("folder")
    if folder:
        return H_ROOT / folder / SUBDIR, (
            "--folder 指定" if args.folder else "current_canvas.json 的 folder 字段")

    cands = sorted(p.name for p in H_ROOT.iterdir() if p.is_dir()) if H_ROOT.exists() else []
    return None, (f"不知道放哪个剧名文件夹（current_canvas.json 里没有 folder 字段）。\n"
                  f"   <ASSET_ROOT>\\ 下现有：{cands or '(读不到 H 盘)'}\n"
                  f"   → 用 `--folder <其中一个>` 明确指定，别猜。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canvas", default=None, help="目标画布 canvasId；不传读 current_canvas.json")
    ap.add_argument("--project", default=None,
                    help="项目 id；不传读工作区 current_canvas.json（换剧自动跟着走）")
    ap.add_argument("--folder", default=None, help="H 盘剧名文件夹（如 示例剧）")
    ap.add_argument("--out", default=None, help="整个落点目录（覆盖 --folder 的拼法）")
    ap.add_argument("--no-wait", action="store_true", help="不轮询，现在已出多少就下多少")
    ap.add_argument("--timeout", type=int, default=600, help="轮询上限秒数，默认 600（10 分钟）")
    ap.add_argument("--interval", type=int, default=15, help="轮询间隔秒数，默认 15")
    ap.add_argument("--no-download", action="store_true", help="只等出图并报告，不下载")
    ap.add_argument("--force", action="store_true", help="已存在的图也重下")
    ap.add_argument("--keep", type=int, default=5, help="留痕保留份数，默认 5")
    ap.add_argument("--no-prune", action="store_true", help="跳过收尾留痕清理")
    ap.add_argument("--dry-run", action="store_true", help="只看会下哪些，不写文件")
    a = ap.parse_args()

    hr("finish.py —— AI 漫剧出图收尾（只读、零扣费）")

    ptr = load_pointer()
    canvas_id = a.canvas or ptr.get("canvasId")
    project_id = require_project_id(a.project)   # 指针缺了就停手（不回退旧项目）
    if not canvas_id:
        print(f"!! 没有 canvasId：既没传 --canvas，{CANVAS_JSON} 里也没有。")
        print("   → 先跑 prep.py（新剧开工），或直接传 --canvas <id>。")
        return EXIT_ASK
    src = "--canvas" if a.canvas else f"{CANVAS_JSON.name}"
    print(f"  画布     : {canvas_id}   （来自 {src}）")
    print(f"  项目     : {project_id}")

    out, why = resolve_out(a, ptr, canvas_id)
    if out is None:
        print(f"!! {why}")
        return EXIT_ASK
    print(f"  归档落点 : {out}   （依据：{why}）")

    # ---- 1. 等出图 ----
    hr("[1/3] 等出图（Canvas/GetDetail，免费）")
    import mc  # noqa
    m = mc.Mobo()
    try:
        detail, timed_out = wait_for_generation(
            m, project_id, canvas_id,
            timeout=0 if a.no_wait else a.timeout,
            interval=max(1, a.interval),
            dry_run=a.dry_run or a.no_wait,
        )
    except Exception as e:
        print(f"!! 读画布失败：{e}")
        return EXIT_ASK

    nodes = detail.get("nodes") or []
    got = [n for n in nodes if n.get("resultContent")]
    waiting = [n for n in nodes if n.get("status") == BUSY]
    never = [n for n in nodes if n.get("status") == 1]
    failed = [n for n in nodes if n.get("status") == 4]

    print()
    print(f"   节点 {len(nodes)} 个 —— {fmt_counts(nodes)}")
    print(f"   已出图 {len(got)} 个   |  未提交(status=1) {len(never)} 个   |  失败 {len(failed)} 个")
    if never:
        print(f"   ⚠️ 有 {len(never)} 个节点从没提交过生成 —— 如果要出它们，得回闸 B 让用户点头。")

    if not got:
        print()
        print("   该画布**没有任何已生成的图**，不下载。")
        if waiting:
            print(f"   （还有 {len(waiting)} 个在生成中，可加大 --timeout 重跑）")
        return EXIT_NOTHING

    if a.no_download:
        print("   (--no-download：只报告，不下载)")
        if "PAY:" in m.dump_audit():
            print("   !! 审计里出现了付费接口，请核对！")
            return EXIT_ASK
        print("   审计：本次只调了只读接口。")
        return 0

    # ---- 2+3. 下载归档 + 清临时（复用 download_canvas_images.py 的 main）----
    hr("[2/3] 下载归档（复用 download_canvas_images.py）")
    import download_canvas_images as DCM  # noqa

    argv = ["download_canvas_images.py", "--project", str(project_id),
            "--canvas", str(canvas_id), "--out", str(out),
            "--keep", str(a.keep)]
    if a.force:
        argv.append("--force")
    if a.no_prune:
        argv.append("--no-prune")
    if a.dry_run:
        argv.append("--dry-run")

    old_argv = sys.argv
    sys.argv = argv
    try:
        rc = DCM.main()
    finally:
        sys.argv = old_argv

    # ---- 3. 汇总 ----
    hr("收尾汇总")
    print(f"   画布     : {canvas_id}")
    print(f"   已出图   : {len(got)} 个")
    print(f"   归档落点 : {out}")
    if timed_out:
        print("   ⚠️ 本轮是**超时**返回的 —— 可能还有节点在生成，稍后可再跑一次 finish.py（幂等，已下的会跳过）。")
    if a.dry_run:
        print("   临时留痕 : 未动（--dry-run）")
    elif a.no_prune:
        print("   临时留痕 : 已跳过 (--no-prune)")
    else:
        print("   临时留痕 : 已按保留策略清理（送回收站，可还原）")
    if not a.dry_run:
        print()
        print("   ⚠️ 下载完还没结束 —— 按 SOP 还要把图**上传绑定到项目「资产准备」**：")
        print(f"      python -X utf8 asset_match.py --canvas {canvas_id}      # 先出匹配报告给用户看")
        print(f"      python -X utf8 bind_asset.py --project {project_id} --dir \"{out}\"")
    return rc


if __name__ == "__main__":
    sys.exit(main())
