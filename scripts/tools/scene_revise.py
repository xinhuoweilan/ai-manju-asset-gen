# -*- coding: utf-8 -*-
"""按「修改要求」重出场景提示词 —— 场景资产审核返工用。

用户审完图后会说「XX 场景要改：多加墓碑 / 把桌子去掉 / 要有西方感」，
这时不要动 `scene_from_list.py`（那是首轮出图的入口），用这个入口。

它与 `scene_from_list.py` 的差别只有一处：**在 user prompt 里追加一段「修改要求」**。
其余全部复用原管线：同一份 SCENE_SYSTEM_PROMPT、同一套依据抽取（scene_context）、
同一种 docx 格式（下游 `asset-gen/append_versions.py` 按
`场景名 - 方案N（场次）` 解析标题）。

用法：
  # ① dry-run：零成本，核对依据 + 打印将要注入的修改要求
  python -X utf8 tools/scene_revise.py --input "<docx>" --scenes tools/revise_示例剧_01.txt --dry-run
  # ② 真跑
  python -X utf8 tools/scene_revise.py --input "<docx>" --scenes tools/revise_示例剧_01.txt --label 返工01

清单格式（每行一个场景，# 开头为注释行）：
  场景名<TAB>场次号<TAB>修改要求
  北角墓园	16-1	现有墓碑太稀疏，要增加大量墓碑，成排密集、层层叠叠
  # 只有两个字段时 = 没有额外修改要求（等价于重出）

⚠️ 缓存键里带上了「修改要求」的指纹 —— 同一个场景换个要求重跑**不会**命中旧缓存，
   但要求没变时重跑会命中，避免重复烧钱。
"""
import argparse
import hashlib
import os
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
sys.path.insert(0, str(_SRC))

from config import CACHE_DIR, INCLUDE_BILINGUAL, SCENE_CONCURRENCY        # noqa: E402
from docx_utils import save_text_docx                                    # noqa: E402
from scene_context import (build_block_index, build_scene_context,       # noqa: E402
                           collect_character_names)
from scene_prompt_builder import (SCENE_SYSTEM_PROMPT, split_variants,   # noqa: E402
                                  _time_envs_from_codes)
from ds_api import call_ds_api                                           # noqa: E402
from cache_utils import load_cache, save_cache                           # noqa: E402
from main import load_script, build_scene_docx_sections                  # noqa: E402

OUTPUT_DIR = _SRC.parent / "output"

CODE_IN_LIST_RE = re.compile(r"^(\d+)\s*[-－—]\s*(\d+)$")


def parse_list_file(path: Path):
    """读清单 → [(场景名, ['12-1', ...], 修改要求), ...]"""
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
        note = parts[2].strip() if len(parts) > 2 else ""
        rows.append((name, codes, note))
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


_PROMPT_HEAD_RE = re.compile(r"伪真人电影级实拍|电影级实拍|伪真人")


def strip_self_title(text: str) -> str:
    """删掉模型自拟的方案小标题。

    标准产物第一句必须是「伪真人电影级实拍，…」（下游把整段喂给文生图模型）。
    DeepSeek 有时会先在首行加一句自拟标题（如「坡地阶梯式密集墓园，墓碑沿等高线层层排列」），
    这行不是画质描述，会干扰出图 ⇒ 丢掉所有出现在画质开头之前的短行。
    """
    lines = str(text).split("\n")
    idx = 0
    for i, ln in enumerate(lines):
        if _PROMPT_HEAD_RE.search(ln):
            idx = i          # 找到画质开头，之前的都算标题
            break
    else:
        return str(text).strip()      # 整篇都没有画质开头 ⇒ 原样返回，不猜
    return "\n".join(lines[idx:]).strip()


def build_revise_user_prompt(location, time_env, script_context, block_count, note):
    """与 scene_prompt_builder.build_scene_user_prompt 同构，只在末尾追加「修改要求」。"""
    if block_count > 1:
        multi = f"""
注意：上面这段依据来自 **同一处空间的 {block_count} 个场次**（标题里的集号不同，但地点是同一个）。
请提取这些场次里**稳定成立**的空间特征：建筑结构、格局、材质、固定家具、固定装置、色彩基调。
某个场次里才有的临时物品、临时摆设、节日装饰不要带进来。
多个场次都指向的破坏痕迹（如反复提到塌陷、破损、烧灼）可以保留。
"""
    else:
        multi = ""
    extra = ""
    if note:
        extra = f"""
【本次修改要求（最高优先级，三版都必须满足）】
{note}
修改要求与上面剧本原文冲突时，以修改要求为准；但剧本里的地点、时间、内外不能改。
"""
    return f"""场景地点：{location}
时间 / 内外：{time_env}

以下是该场景所在场次的剧本原文（这是你唯一的依据）：
--------
{script_context}
--------
{multi}{extra}
请严格依据上面的剧本原文，为这个场景生成三份不同的场景提示词。"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="剧本 docx / txt")
    ap.add_argument("--scenes", required=True, help="返工清单文件")
    ap.add_argument("--label", default=None, help="产物批次名（默认取清单文件名）")
    ap.add_argument("--dry-run", action="store_true", help="只核对依据，不调 API")
    ap.add_argument("--no-cache", action="store_true", help="不吃缓存也不写缓存")
    ap.add_argument("--from-cache", action="store_true",
                    help="只吃缓存重出 docx，不调 API（改了后处理逻辑时用，零成本）")
    ap.add_argument("--jobs", type=int, default=None, help="并发数（默认跟 config 一致）")
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
    for name, codes, note in wanted:
        resolved = resolve_codes(codes, by_ep_no, name)
        if not resolved:
            missed.append(name)
            continue
        scene_rows.append((name, resolved, note))

    print("=" * 78)
    print(f"返工清单：{list_path.name} —— 共 {len(wanted)} 个场景，"
          f"解析成功 {len(scene_rows)} 个")
    if missed:
        print(f"!! 以下 {len(missed)} 个场景一场都没匹配上，已跳过：{missed}")
    print("=" * 78)
    for name, resolved, note in scene_rows:
        print(f"  · {name}  ({len(resolved)} 场)")
        print(f"      修改要求：{note or '（无，等价于重出）'}")

    index = build_block_index(blocks)
    names = collect_character_names(blocks)
    cache_dir = OUTPUT_DIR / CACHE_DIR

    if args.dry_run:
        print("\n=== DRY RUN：只核对每个场景匹配到的剧本依据，不调 API ===")
        total, empty = 0, 0
        for i, (name, resolved, note) in enumerate(scene_rows, 1):
            ctx, notes = build_scene_context(resolved, index, INCLUDE_BILINGUAL,
                                             compact=True, character_names=names)
            n = len([ln for ln in ctx.split("\n") if ln.strip()])
            total += n
            if n == 0:
                empty += 1
            print(f"  {i:2d}. {name:<24} {len(resolved)} 场  依据 {n:3d} 行"
                  f"{'   <== 依据为空！' if n == 0 else ''}")
        print(f"\n依据合计 {total} 行；{len(scene_rows)} 个场景中 {empty} 个没匹配到依据")
        print("（DRY RUN 结束，未调用任何 API）")
        return 0 if not empty else 2

    # 并发生成（同一套 prompt / 依据 / 解析，只是 user prompt 多了修改要求）
    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _one(idx, name, resolved, note, total):
        ctx, notes = build_scene_context(resolved, index, INCLUDE_BILINGUAL,
                                         compact=True, character_names=names)
        line_count = len([ln for ln in ctx.split("\n") if ln.strip()])
        fp = hashlib.md5((note or "").encode("utf-8")).hexdigest()[:8]
        cache_key = f"revise::{name}::{'|'.join(resolved)}::{fp}"
        cached = None if args.no_cache else load_cache(str(cache_dir), cache_key)

        if cached:
            # 命中缓存也走一遍清洗 —— 改了后处理逻辑（比如 strip_self_title）时，
            # 不要重调 API，直接把老产物洗干净重出 docx。
            variants = [strip_self_title(v) for v in (cached.get("prompts") or [])]
            dirty = variants != (cached.get("prompts") or [])
            cached["prompts"] = variants
            cached["degraded"] = len(variants) < 3
            if dirty:
                print(f"[{idx}/{total}] {name} —— 命中缓存，已清洗自拟标题")
                if not args.no_cache:
                    save_cache(str(cache_dir), cache_key, cached)
            else:
                print(f"[{idx}/{total}] {name} —— 命中缓存，跳过")
            return cached

        if args.from_cache:
            raise SystemExit(f"!! [{name}] 缓存里没有（键 {cache_key}），"
                             f"--from-cache 模式不调 API，先去掉这个参数真跑一次")

        user_prompt = build_revise_user_prompt(name, _time_envs_from_codes(resolved),
                                               ctx, len(resolved), note)
        text = call_ds_api(SCENE_SYSTEM_PROMPT, user_prompt, temperature=0.7, max_tokens=2500)
        variants = [strip_self_title(v) for v in split_variants(text)]
        degraded = len(variants) < 3
        if degraded:
            print(f"[{idx}/{total}] {name} —— [警告] 只解析出 {len(variants)} 版")
            if not variants:
                variants = [text.strip()]
        else:
            print(f"[{idx}/{total}] {name} ✓  依据 {line_count} 行")
        item = {"scene": name, "codes": resolved, "prompts": variants[:3],
                "degraded": degraded, "context_lines": line_count}
        if not args.no_cache:
            save_cache(str(cache_dir), cache_key, item)
        return item

    jobs = max(1, int(args.jobs or SCENE_CONCURRENCY))
    results = [None] * len(scene_rows)
    with ThreadPoolExecutor(max_workers=min(jobs, len(scene_rows))) as pool:
        futs = {pool.submit(_one, i, n, c, nt, len(scene_rows)): i
                for i, (n, c, nt) in enumerate(scene_rows, 1)}
        for f in as_completed(futs):
            results[futs[f] - 1] = f.result()
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
