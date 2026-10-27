# -*- coding: utf-8 -*-
"""按「外部给定的场景清单」出场景提示词 —— 跳过「提取 + 合并」那一步。

用户自己定好了场景名和它们对应的场次，所以不需要 run_merge 去聚类。
除了这一步，**其余全部复用原管线**：同一份 SCENE_SYSTEM_PROMPT、同一套
依据抽取（scene_context）、同一个缓存、同一种 docx 格式（下游
`asset-gen/append_versions.py` 就是按 `场景名 - 方案N（场次）` 解析标题的）。

用法：
  python -X utf8 tools/scene_from_list.py --input "<docx>" --scenes tools/scenes_示例剧.txt
  python -X utf8 tools/scene_from_list.py --input "<docx>" --scenes <清单> --dry-run
  python -X utf8 tools/scene_from_list.py --input "<docx>" --scenes <清单> --no-cache
  python -X utf8 tools/scene_from_list.py --input "<docx>" --scenes <清单> --jobs 4

清单格式（每行一个场景，# 开头为注释行）：
  场景名<TAB>12-1,12-2,13-1
  场景名|12-1,12-2          （制表符写成 | 也行）

场次号写 `集-场`（如 12-1）即可 —— 脚本会自己去剧本里补上「日 内 / 夜 内」
（不补的话 user prompt 里的「时间 / 内外」会是空的，白天黑夜就给丢了）。
"""
import argparse
import os
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
sys.path.insert(0, str(_SRC))

from config import CACHE_DIR, INCLUDE_BILINGUAL, SCENE_CONCURRENCY  # noqa: E402
from docx_utils import save_text_docx                                # noqa: E402
from scene_context import (build_block_index, build_scene_context,   # noqa: E402
                           collect_character_names)
from scene_prompt_builder import generate_scene_prompts              # noqa: E402
from main import load_script, build_scene_docx_sections              # noqa: E402

OUTPUT_DIR = _SRC.parent / "output"

CODE_IN_LIST_RE = re.compile(r"^(\d+)\s*[-－—]\s*(\d+)$")


def parse_list_file(path: Path):
    """读清单 → [(场景名, ['12-1', ...]), ...]"""
    rows = []
    for ln_no, raw in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = re.split(r"\t|\|", line)
        if len(parts) < 2:
            print(f"  !! 第 {ln_no} 行没写场次号，跳过：{line}")
            continue
        name = parts[0].strip()
        codes = [c.strip() for c in re.split(r"[,，、]", parts[1]) if c.strip()]
        rows.append((name, codes))
    return rows


def resolve_codes(codes: list, by_ep_no: dict, scene_name: str):
    """`12-1` → `12--1夜内`（去剧本块里补时间内外）。补不到就报错停下，不猜。"""
    out, bad = [], []
    for c in codes:
        m = CODE_IN_LIST_RE.match(c)
        if not m:
            bad.append(f"{c}（格式不是 集-场）")
            continue
        blk = by_ep_no.get((m.group(1), m.group(2)))
        if not blk:
            bad.append(f"{c}（剧本里没有这一场）")
            continue
        out.append(f"{m.group(1)}--{m.group(2)}{blk['time_env']}")
    if bad:
        print(f"  !! [{scene_name}] 有场次解析不了：{'; '.join(bad)}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="剧本 docx / txt")
    ap.add_argument("--scenes", required=True, help="场景清单文件")
    ap.add_argument("--label", default=None, help="产物批次名（默认取清单文件名）")
    ap.add_argument("--dry-run", action="store_true", help="只核对依据，不调 API")
    ap.add_argument("--no-cache", action="store_true", help="不吃缓存也不写缓存")
    ap.add_argument("--jobs", type=int, default=None, help="并发数（默认跟 config 一致 = 串行）")
    args = ap.parse_args()

    list_path = Path(args.scenes)
    if not list_path.exists():
        print(f"!! 清单文件不存在：{list_path}")
        return 2
    label = args.label or re.sub(r"\.(txt|md)$", "", list_path.name)
    wanted = parse_list_file(list_path)
    if not wanted:
        print("!! 清单里没有一条有效场景")
        return 2

    full_script, blocks = load_script(args.input)
    if not blocks:
        return 2

    by_ep_no = {}
    for b in blocks:
        by_ep_no.setdefault((str(b["episode"]), str(b["scene_no"])), b)

    scene_rows, missed = [], []
    for name, codes in wanted:
        resolved = resolve_codes(codes, by_ep_no, name)
        if not resolved:
            missed.append(name)
            continue
        scene_rows.append(tuple([name] + resolved))

    print("=" * 78)
    print(f"场景清单：{list_path.name} —— 共 {len(wanted)} 个场景，"
          f"解析成功 {len(scene_rows)} 个")
    if missed:
        print(f"!! 以下 {len(missed)} 个场景一场都没匹配上，已跳过：{missed}")
    print("=" * 78)

    index = build_block_index(blocks)
    names = collect_character_names(blocks)
    cache_dir = OUTPUT_DIR / CACHE_DIR

    if args.dry_run:
        print("\n=== DRY RUN：只核对每个场景匹配到的剧本依据，不调 API ===")
        total, empty, lens = 0, 0, []
        for i, row in enumerate(scene_rows, 1):
            ctx, notes = build_scene_context(row[1:], index, INCLUDE_BILINGUAL,
                                             compact=True, character_names=names)
            n = len([ln for ln in ctx.split("\n") if ln.strip()])
            lens.append(n)
            total += n
            if n == 0:
                empty += 1
            print(f"  {i:2d}. {row[0]:<24} {len(row) - 1} 场  依据 {n:3d} 行"
                  f"{'   <== 依据为空！' if n == 0 else ''}")
            if n:
                print(f"        {'; '.join(notes)}")
        lens.sort()
        if lens:
            print(f"\n依据行数：最小 {lens[0]}｜中位 {lens[len(lens) // 2]}｜"
                  f"最大 {lens[-1]}｜合计 {total}")
        print(f"共 {len(scene_rows)} 个场景，其中 {empty} 个没匹配到依据")
        print("（DRY RUN 结束，未调用任何 API）")
        return 0 if not empty else 2

    print(f"\n开始为 {len(scene_rows)} 个场景生成三份提示词"
          f"（并发 {args.jobs or SCENE_CONCURRENCY}）...")
    results = generate_scene_prompts(
        scene_rows, blocks, cache_dir=str(cache_dir), use_cache=not args.no_cache,
        compact=True, jobs=args.jobs,
    )

    sections = build_scene_docx_sections(results)
    docx_path = OUTPUT_DIR / "场景提示词.docx"
    save_text_docx("场景提示词", sections, str(docx_path))
    print(f"\n场景提示词已保存：{docx_path}")

    stamp = time.strftime("%Y%m%d-%H%M")
    batch_dir = OUTPUT_DIR / "场景提示词"
    batch_dir.mkdir(parents=True, exist_ok=True)
    batch_path = batch_dir / f"{stamp}_{label}.docx"
    save_text_docx("场景提示词", sections, str(batch_path))
    print(f"本批副本已保存：{batch_path}")

    degraded = [r["scene"] for r in results if r and r.get("degraded")]
    if degraded:
        print(f"\n[注意] {len(degraded)} 个场景的三版解析不完整，建议重跑：{degraded}")
    print(f"\n本次 {len(results)} 个场景 × 3 版 = {len(sections)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
