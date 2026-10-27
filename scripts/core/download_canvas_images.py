# -*- coding: utf-8 -*-
"""把画布上**已生成**的图片全部下载到本地文件夹（**只读，零扣费**）。

用法：
    python download_canvas_images.py --out "<ASSET_ROOT>/<剧名>/画布"   # 画布跟工作区指针
    python download_canvas_images.py --dry-run      # 只看会下哪些，不落盘

口径：
  · 只调 GetDetail（免费接口）；**绝不**调用 CreateGenerationTask ⇒ 不花一分钱
  · 文件名 = 画布上的节点名（`苏晚意-方案1.png`）；重名自动追加 `-<nodeId>`
  · 幂等：目标文件已存在且 >1KB 就跳过；`--force` 强制重下
  · 图片落 `--out`；**默认不产生任何元数据 json**（用户口径 2026-09-24：这类文件多余，
    快照更是拍下即过时）。确需留档时显式加 `--meta`（写脚本目录 `archives/`）
    或 `--meta-dir <目录>` —— 素材目录永远只放成品图
  · 默认输出目录 = `exports/<canvasId>_<时间戳>/`（本目录下）
"""
import argparse
import json
import pathlib
import re
import sys
import time

import requests

HERE = pathlib.Path(__file__).resolve().parent.parent  # scripts/（本文件在 scripts/core/）
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入搜索路径
from mc import BASE, Mobo, default_project_id, require_project_id  # noqa: E402

PROJECT_ID = default_project_id()   # 由 current_canvas.json 决定
# ⚠️ 口径：1 = 铺好了但**没提交生成**（不是"排队中"）。
#    本脚本不用这个常量（下载只认 resultContent 有没有值）；状态汇总在 finish.py 里做。
STATUS_TXT = {0: "待处理", 1: "未提交", 2: "生成中", 3: "已完成", 4: "失败", 5: "已取消"}
BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')

# 工作区指针（current_canvas.json）只在 mc.py 里有一份 —— 本文件不再重复定义（2026-09-27 收口）
from mc import POINTER_FILE as CANVAS_JSON, default_canvas_id  # noqa: E402

# 元数据（manifest / 画布快照）**默认不生成**；只有显式 `--meta` 时才落这里
# （用户口径 2026-09-24：素材目录只放成品图，这类 json 一律不多余生成）
ARCHIVE_DIR = HERE / "archives"


def safe_name(name, fallback):
    s = BAD_CHARS.sub("_", (name or "").strip()).strip(" .") or fallback
    return s[:120]


def full_url(u):
    if not u:
        return u
    return u if u.startswith("http") else BASE + u


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=PROJECT_ID)
    ap.add_argument("--canvas", default=None,
                    help="目标画布 canvasId；不传则从 current_canvas.json 读（默认）")
    ap.add_argument("--episode", default="0")
    ap.add_argument("--shot", default="0")
    ap.add_argument("--out", default=None, help="输出文件夹；默认 exports/<canvasId>_<时间戳>/")
    ap.add_argument("--meta", action="store_true",
                    help=f"显式留档：写 manifest + 画布快照到 {ARCHIVE_DIR.name}/（默认不写）")
    ap.add_argument("--meta-dir", default=None,
                    help="留档目录（给这个参数即等于开启留档，位置覆盖 --meta 的默认值）")
    ap.add_argument("--dry-run", action="store_true", help="只列出将要下载的，不写任何文件")
    ap.add_argument("--force", action="store_true", help="已存在的文件也重下")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--keep", type=int, default=5,
                    help="收尾清理：带时间戳的留痕（sent_*/prompt_backup_*）每组保留份数，默认 5")
    ap.add_argument("--no-prune", action="store_true", help="跳过收尾的留痕清理")
    a = ap.parse_args()
    require_project_id(a.project)       # 指针缺了就停手报错（绝不回退旧项目）

    # 没传 --canvas：读工作区指针（口径只在 mc.default_canvas_id 里）
    if not a.canvas:
        a.canvas = default_canvas_id()
        if not a.canvas:
            print(f"!! 没传 --canvas，{CANVAS_JSON} 里也没有 canvasId。")
            print("   二选一：① 传 --canvas <id>；② 先跑 prep.py 写 current_canvas.json")
            return 2
        print(f"  (默认从 {CANVAS_JSON.name} 读 canvasId = {a.canvas})")

    out = pathlib.Path(a.out) if a.out else (HERE / "exports" /
                                             f"{a.canvas}_{time.strftime('%Y%m%d-%H%M%S')}")

    # 启动时清掉「本次输出目录 + exports/」下 24 小时前的 .part 残留
    # （正在下载的不会被误删；24h 阈值 = 隔夜才清，绝不碰当次任务）
    # ⚠️ 必须把 `--out` 目录一起扫：实际用法是把图下到 <ASSET_ROOT>\<剧名>\…，
    #    只扫 exports/ 等于扫一个没人用的目录，H 盘上的 .part 永远没人清（实测踩到）。
    cutoff = time.time() - 86400
    scan_dirs, seen = [], set()
    for d in (out, HERE / "exports"):
        try:
            key = str(d.resolve()).lower()
        except OSError:
            key = str(d).lower()
        if key in seen or not d.exists():
            continue
        seen.add(key)
        scan_dirs.append(d)

    # ⚠️ 去重：默认输出目录就在 exports/ 里，两处 rglob 会扫到同一个文件，
    #    不去重的话 `stale` 里会有两份，第二次 unlink 直接 FileNotFoundError。
    parts, seen_files = [], set()
    for d in scan_dirs:
        for p in d.rglob("*.png.part"):
            k = str(p.resolve()).lower()
            if k in seen_files:
                continue
            seen_files.add(k)
            parts.append(p)

    stale = [p for p in parts if p.stat().st_mtime < cutoff]
    if stale:
        print(f"[启动清理] 发现 {len(stale)} 个 24h 前的 .part 残留，清理：")
        for p in stale:
            age_h = (time.time() - p.stat().st_mtime) / 3600
            try:
                p.unlink()
            except FileNotFoundError:
                continue
            print(f"  - {p.parent.name}/{p.name}  ({age_h:.1f} 小时前)")
    elif parts:
        print(f"[启动清理] 扫到 {len(parts)} 个 .part，都在 24h 内（可能正在下载），保留不动。")
    else:
        print("[启动清理] 无需清理（无 .part 残留）。")
    if scan_dirs:
        print(f"  扫描范围：{'; '.join(str(d) for d in scan_dirs)}")
    print()

    m = Mobo()
    detail = m.read_canvas_detail(a.project, a.canvas, episode_id=a.episode,
                                  shot_id=a.shot, is_episode_mode=False)
    nodes = detail.get("nodes") or []
    got = [n for n in nodes if n.get("resultContent")]

    print("=" * 78)
    print(f"画布 {a.canvas}：节点 {len(nodes)} 个，已出图 {len(got)} 个")
    print(f"输出目录：{out}")
    print("=" * 78)
    if not got:
        print("该画布没有已生成的图片，未建目录、未下载。")
        print()
        print("审计：")
        print(m.dump_audit())
        return 0

    if not a.dry_run:
        out.mkdir(parents=True, exist_ok=True)

    used, rows = {}, []
    for n in got:
        nid = n.get("id")
        fn = safe_name(n.get("name"), f"node{nid}")
        if fn in used:
            fn = f"{fn}-{nid}"
        used[fn] = 1
        url = full_url(n["resultContent"])
        p = out / f"{fn}.png"
        rec = {"nodeId": nid, "name": n.get("name"), "nodeKey": n.get("nodeKey"),
               "status": n.get("status"), "file": str(p), "url": url}

        if a.dry_run:
            print(f"  [dry] {p.name}   <- 节点 {nid} ({n.get('name')})")
            rec["ok"] = None
            rows.append(rec)
            continue

        if p.exists() and p.stat().st_size > 1024 and not a.force:
            rec.update(ok=True, bytes=p.stat().st_size, note="已存在，跳过")
            print(f"  = {p.name}  已存在（{p.stat().st_size/1024:.0f} KB），跳过")
            rows.append(rec)
            continue

        try:
            r = requests.get(url, timeout=a.timeout)
            r.raise_for_status()
            tmp = p.with_suffix(".png.part")
            tmp.write_bytes(r.content)
            tmp.replace(p)
            rec.update(ok=True, bytes=len(r.content))
            print(f"  + {p.name}  {len(r.content)/1024:.0f} KB")
        except Exception as e:
            rec.update(ok=False, error=str(e))
            print(f"  ! {p.name}  下载失败：{e}")
        rows.append(rec)

    # 元数据**默认不生成**（用户口径 2026-09-24：多余，且快照拍下即过时）。
    # 只有显式 `--meta` / `--meta-dir` 才写，而且永远不写进素材目录。
    meta_paths = []
    if not a.dry_run and (a.meta or a.meta_dir):
        meta_dir = pathlib.Path(a.meta_dir) if a.meta_dir else ARCHIVE_DIR
        meta_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        stem = f"{safe_name(out.name, 'out')}__canvas{a.canvas}__{stamp}"
        man = meta_dir / f"{stem}_manifest.json"
        sna = meta_dir / f"{stem}_snapshot.json"
        man.write_text(
            json.dumps({"canvasId": a.canvas, "projectId": a.project,
                        "exportedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                        "outDir": str(out), "items": rows},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        sna.write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
        meta_paths = [man, sna]

    ok_n = sum(1 for r in rows if r.get("ok"))
    print()
    print(f"完成：{ok_n}/{len(rows)} 张已就位 -> {out}")
    for p in meta_paths:
        print(f"元数据（留档，在素材目录之外）：{p}")
    print()
    print("审计（确认没碰付费接口）：")
    audit = m.dump_audit()
    print(audit)
    if "PAY:" in audit:
        print("!!! 警告：本次审计里出现了付费接口调用，请立刻核对 !!!")
        return 2
    print("→ 本次只调了只读接口，零扣费。")

    # 收尾：把会无限累积的「带时间戳留痕」按保留策略清一次
    # （用户口径 2026-09-24：「以后这种多余的也不用生成放进去」——
    #   sent_update_*/sent_append_*/prompt_backup_* 每组只留最近 N 份，其余送回收站。
    #   绝不碰凭证 / 付费凭证 / meta.json；清理失败不影响下载结果。）
    if a.no_prune or a.dry_run:
        print("[保留策略] 已跳过。")
    else:
        try:
            import prune_artifacts as PA
            victims, _detail = PA.pick_by_retention(HERE, a.keep)
            if victims:
                # ⚠️ 体积必须在回收**之前**算 —— 删完再 stat 会 FileNotFoundError
                #    （实测踩到：文件已删但日志报成「跳过」，看着像没干活）
                kb = sum(p.stat().st_size for p in victims) / 1024
                ok, bad, _rcs = PA.recycle(victims)
                print(f"[保留策略] 留痕超出 {a.keep} 份：{len(ok)} 个已送回收站（{kb:.1f} KB）")
                for p in victims:
                    print(f"   - {p.relative_to(HERE)}")
                if bad:
                    print(f"   !! {len(bad)} 个失败：{[str(x) for x in bad]}")
            else:
                print(f"[保留策略] 留痕都在 {a.keep} 份以内，无需清理。")
        except Exception as e:
            print(f"[保留策略] 跳过（{e!r}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
