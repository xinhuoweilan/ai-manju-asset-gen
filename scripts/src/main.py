import argparse
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CACHE_DIR, DS_API_KEY, INCLUDE_BILINGUAL, SCENE_CONCURRENCY
from file_reader import read_script, find_input_file, list_candidates
from docx_utils import save_text_docx, save_scene_table_docx
from scene_extractor import (
    split_into_blocks, run_merge, group_scenes, build_merge_report, resolve_name,
    block_code, collect_name_stats, collect_blocked_pairs,
)
from scene_context import (build_block_index, build_scene_context,
                           collect_character_names)
from scene_prompt_builder import generate_scene_prompts
from character_prompt_builder import (
    generate_multiple_characters, collect_roster, build_roster_report,
)


AUTO_ROSTER_KEYS = {"auto", "all", "全部", "自动"}


def build_scene_docx_sections(scene_results: list) -> list:
    sections = []
    for item in scene_results:
        scene_name = item["scene"]
        codes = "  ".join(item["codes"])
        prompts = item["prompts"] or [""]
        for i, prompt in enumerate(prompts, 1):
            label = f"方案{i}" if len(prompts) > 1 else "方案"
            sections.append({
                "heading": f"{scene_name} - {label}（{codes}）",
                "content": prompt,
            })
    return sections


def build_character_docx_sections(character_results: list) -> list:
    sections = []
    for item in character_results:
        if not item.get("prompts"):
            continue                      # 未匹配的人物不进产物
        name = item["name"]
        role_type = item.get("role_label") or "次要角色"
        for i, prompt in enumerate(item["prompts"], 1):
            # ⚠️ 单版也曾写成「唯一方案」——下游「asset-gen」的 fill_*.py 是按
            # 「-方案N」解析标题的，认不出来就把整条静默跳过。2026-09-24 实测：
            # 46 个代称角色（保安/管家/记者们…）全被丢掉，一条都上不了画布。
            # 统一叫「方案1」后既保留了「这角色只有一版」的信息，又能被下游认出来。
            version_label = f"方案{i}"
            sections.append({
                "heading": f"{name} - {version_label}（{role_type}）",
                "content": prompt,
            })
    return sections


def build_character_batch_path(output_dir: str, names: list) -> str:
    """给「这一批人物」产物起一个带时间戳的文件名，保证永不覆盖上一批。

    产物落在 output/人物提示词/ 子目录里，形如：
        20260922-1930_苏晚意+容晏.docx       （3 人以内：直接列出名字）
        20260922-2015_苏晚意等63人.docx     （人多时：只留第一个 + 总数）
    """
    stamp = time.strftime("%Y%m%d-%H%M")
    if not names:
        who = "人物"
    elif len(names) <= 3:
        who = "+".join(names)
    else:
        who = f"{names[0]}等{len(names)}人"
    # 文件名里不能有 \ / : * ? " < > | 与空白
    who = re.sub(r'[\\/:*?"<>|\s]+', "_", who)[:40].strip("_") or "人物"
    folder = Path(output_dir) / "人物提示词"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder / f"{stamp}_{who}.docx")


def load_script(input_path: str) -> tuple:
    print(f"读取剧本：{input_path}")
    full_script = read_script(input_path)
    blocks = split_into_blocks(full_script)

    print(f"剧本共 {len(full_script)} 字，切出 {len(blocks)} 个场次块")
    eps = sorted({int(b["episode"]) for b in blocks if str(b["episode"]).isdigit()})
    if eps:
        print(f"场次覆盖：第 {eps[0]} 集 ~ 第 {eps[-1]} 集")
    if not blocks:
        print("[错误] 没解析到任何中文场次标题，请检查剧本格式")
    return full_script, blocks


def print_merge_summary(detail: dict):
    """把合并明细和改名映射打到控制台，方便直接看。"""
    rows = detail["rows"]
    filtered = detail["filtered"]
    name_map = detail["name_map"]

    loc_of = {block_code(b): b["location"] for b in filtered}
    ep_of = {block_code(b): b["episode"] for b in filtered}

    multi = [r for r in rows if len(r) > 2]
    print(f"\n{'=' * 60}")
    print(f"合并结果：{len(filtered)} 个场次 → {len(rows)} 行"
          f"（其中 {len(multi)} 行并入了多个场次）")
    print(f"{'=' * 60}")

    print(f"\n【改名的场景名】模型判定为「同一空间的不同叫法」：{len(name_map)} 条")
    if not name_map:
        print("  无")
    for old, new in name_map.items():
        print(f"  {old}  →  {resolve_name(old, name_map)}")

    blocked = collect_blocked_pairs(
        [n for n, _ in collect_name_stats(filtered)],
        sorted({v for v in name_map.values() if v}),
    )
    print(f"\n【被规则拦下的归并】{len(blocked)} 条 —— 这些是故意没并的，各自成行：")
    if not blocked:
        print("  无")
    for long, short, why in blocked:
        print(f"  ✗ {long}  不并进「{short}」：{why}")

    print(f"\n【并入了多个场次的行】逐行核对，右边这些场次是否真的同属一处空间：")
    if not multi:
        print("  无")
    for r in multi:
        display, codes = r[0], r[1:]
        canon = resolve_name(loc_of.get(codes[0], ""), name_map)
        eps = []
        for c in codes:
            e = ep_of.get(c, "?")
            if e not in eps:
                eps.append(e)
        print(f"\n  ▸ {display}   {len(codes)} 个场次｜第 {','.join(eps)} 集")
        for c in codes:
            src = loc_of.get(c, "?")
            mark = "" if resolve_name(src, name_map) == canon else f"   ← 原名：{src}"
            print(f"      {c}{mark}")


def scene_task(input_path: str, output_dir: str, start_ep: int, end_ep: int,
               dry_run: bool = False, use_cache: bool = True, no_merge: bool = False,
               merge_only: bool = False, group_by: str = "env",
               compact: bool = True, jobs: int = None):
    full_script, blocks = load_script(input_path)
    if not blocks:
        return None

    ep_blocks = [b for b in blocks if start_ep <= int(b["episode"]) <= end_ep]
    cache_dir = Path(output_dir) / CACHE_DIR

    print(f"\n提取并合并第 {start_ep} ~ {end_ep} 集场景...")
    detail = run_merge(blocks, start_ep, end_ep, cache_dir=str(cache_dir),
                       use_cache=use_cache, skip_llm=no_merge, group_by=group_by)
    scene_rows = detail["rows"]
    print(f"区间内 {len(ep_blocks)} 个场次 → {len(scene_rows)} 个场景")

    table_path = str(Path(output_dir) / "场景合并表.docx")
    report_path = str(Path(output_dir) / "合并质检报告.txt")

    # --dry-run 是「只看不写」的自检模式。老实现照样把这两份产物写出去 ——
    # 拿 `--scene 1-2 --dry-run` 核对小范围时，会把全本 55 行的场景合并表
    # 覆盖成 4 行：一次自检动作悄悄毁掉正式产物，还很难发现。现在不写了。
    if dry_run and not merge_only:
        print("[DRY RUN] 不写场景合并表 / 质检报告，原有产物保持原样")
    else:
        save_scene_table_docx(f"场景—集数对照表（第 {start_ep}~{end_ep} 集）",
                              scene_rows, table_path)
        print(f"场景合并表已保存：{table_path}")

        report = build_merge_report(scene_rows, detail["filtered"], detail["name_map"],
                                    detail["raw"], group_by=group_by)
        Path(report_path).write_text(report, encoding="utf-8")
        print(f"合并质检报告已保存：{report_path}")

    if merge_only:
        print("\n=== 只跑合并（--merge-only）：不再生成场景提示词，零额外 API ===")
        print_merge_summary(detail)
        return table_path

    if not scene_rows:
        print("没有提取到场景，跳过提示词生成")
        return None

    if dry_run:
        mode = "精简（去对白/去译文，取空间证据）" if compact else "全量原文"
        print(f"\n=== DRY RUN：只核对每个场景匹配到的剧本依据，不调 API ===（口径：{mode}）")
        index = build_block_index(blocks)
        names = collect_character_names(blocks) if compact else set()
        empty = 0
        total = 0
        rows_len = []
        for i, row in enumerate(scene_rows, 1):
            ctx, notes = build_scene_context(row[1:], index, INCLUDE_BILINGUAL,
                                             compact=compact, character_names=names)
            n = len([ln for ln in ctx.split("\n") if ln.strip()])
            rows_len.append(n)
            total += n
            flag = "   <== 依据为空！" if n == 0 else ""
            if n == 0:
                empty += 1
            print(f"  {i:2d}. {row[0]:<26} {(str(row[1]) + (' 等%d场' % (len(row) - 2)) if len(row) > 2 else ''):<16} "
                  f"依据 {n:3d} 行{flag}")
        rows_len.sort()
        print(f"\n共 {len(scene_rows)} 个场景，其中 {empty} 个没匹配到剧本依据")
        if rows_len:
            print(f"依据行数：最小 {rows_len[0]}｜中位 {rows_len[len(rows_len) // 2]}｜"
                  f"最大 {rows_len[-1]}｜合计 {total}")
        print("（DRY RUN 结束，未调用任何 API）")
        return None

    print("\n开始为每个场景生成三份提示词...")
    scene_results = generate_scene_prompts(
        scene_rows, blocks, cache_dir=str(cache_dir), use_cache=use_cache,
        compact=compact, jobs=jobs,
    )

    sections = build_scene_docx_sections(scene_results)
    docx_path = str(Path(output_dir) / "场景提示词.docx")
    save_text_docx("场景提示词", sections, docx_path)
    print(f"\n场景提示词已保存：{docx_path}")
    return docx_path


def character_task(input_path: str, output_dir: str, names: list,
                   dry_run: bool = False, use_cache: bool = True,
                   auto_roster: bool = False, jobs: int = None):
    full_script, blocks = load_script(input_path)
    if not blocks:
        return None

    if auto_roster:
        names = collect_roster(blocks)
        print(f"自动提取人物名单：{len(names)} 个（出场人物行 + 对白说话人，简称已折回本人）")

    if not names:
        print("没有可处理的人物")
        return None

    if dry_run:
        print("\n=== DRY RUN：只核对人物名单与版数判定，不调 API ===")
        report = build_roster_report(names, full_script, blocks)
        print(report)
        print("（DRY RUN 结束，未调用任何 API）")

        if auto_roster:
            # 只有「auto 全本名单」才落盘成底稿 ——
            # 随手传两三个名字来核对时不会把全本名单覆盖掉
            roster_path = Path(output_dir) / "人物名单.txt"
            roster_path.write_text(report + "\n", encoding="utf-8")
            print(f"\n人物名单已保存：{roster_path}")
            print("（这份就是给你手改的底稿：删掉不想要的、改完用 "
                  "--character 名字1,名字2 回喂即可）")
            return str(roster_path)
        print("（本次是手动指定的名字，未覆盖 output/人物名单.txt）")
        return None

    cache_dir = Path(output_dir) / CACHE_DIR

    print(f"\n开始为 {len(names)} 个人物生成提示词：{', '.join(names)}")
    character_results = generate_multiple_characters(
        names, full_script, blocks, cache_dir=str(cache_dir), use_cache=use_cache,
        jobs=jobs,
    )

    unmatched = [r["name"] for r in character_results
                 if r and r.get("status") == "unmatched"]
    if unmatched:
        print("\n" + "!" * 64)
        print(f"[注意] 以下 {len(unmatched)} 个名字在剧本里找不到，已跳过"
              f"（没生成、没花钱、没进 docx）：")
        for nm in unmatched:
            print(f"    · {nm}")
        print("        请核对是不是写错了，或者剧本里用的是别的叫法。")
        print("!" * 64)

    failed = [r["name"] for r in character_results if r and r.get("status") == "error"]
    degraded = [r["name"] for r in character_results if r and r.get("degraded")]
    similar = [r["name"] for r in character_results if r and r.get("similar_pairs")]
    # v10：锚点缺性别的角色（老缓存没有这个字段 → None，不会被误报）
    no_gender = [r["name"] for r in character_results if r and r.get("gender_ok") is False]
    # 族裔风险（用 or [] 兜住老缓存，不会误报）；v12 起不再点名「锚点缺骨相词」
    race_risk = [r["name"] for r in character_results
                 if r and (r.get("asian_cues") or r.get("dark_iris"))]
    if failed:
        print(f"\n[注意] 以下 {len(failed)} 个人物生成失败，已跳过（重跑会自动跳过已完成的）："
              f"{failed}")
    if degraded:
        print(f"\n[注意] 以下 {len(degraded)} 个人物的多版解析不完整，建议重跑：{degraded}")
    if similar:
        print(f"\n[注意] 以下 {len(similar)} 个人物的多版过于相似，建议 --no-cache 重跑："
              f"{similar}")
    if no_gender:
        print(f"\n[注意] 以下 {len(no_gender)} 个人物的「固定设定」锚点里没有性别词，"
              f"跨性别画错的风险较高，建议 --no-cache 重跑：{no_gender}")
    if race_risk:
        print(f"\n[注意] 以下 {len(race_risk)} 个人物的正文里出现高风险的族裔漂移词"
              f"（东亚脸特征词 或 白种人却写了深色瞳），出图容易偏亚洲脸，"
              f"建议 --no-cache 重跑：{race_risk}")

    sections = build_character_docx_sections(character_results)
    if not sections:
        # 一个能写的都没有（名字全打错 / 全部失败 / 全部未匹配）——
        # 绝不能拿空列表去覆盖：那会把上一批产物冲成一个只有标题的空 docx。
        print(f"\n[注意] 本次没有任何可写入的提示词，产物目录未改动")
        return None

    # 【按批次保留】每一批单独一个文件，放在 output/人物提示词/ 下，永不覆盖上一批；
    # 同时刷新 output/人物提示词.docx 作为「最新一批」的固定入口（方便直接打开/预览）。
    made = [r["name"] for r in character_results if r and r.get("prompts")]
    batch_path = build_character_batch_path(output_dir, made)
    save_text_docx("人物提示词", sections, batch_path)

    latest_path = str(Path(output_dir) / "人物提示词.docx")
    save_text_docx("人物提示词", sections, latest_path)

    print(f"\n人物提示词已保存：{batch_path}（{len(sections)} 份）")
    print(f"最新一批副本：{latest_path}")
    return batch_path


def interactive_main(input_file: str, output_dir: str, dry_run: bool, use_cache: bool):
    print("=" * 50)
    print("漫剧提示词生成器")
    print("=" * 50)
    print(f"检测到剧本文件：{input_file}")
    print()
    print("请选择任务类型：")
    print("1. 场景提示词生成")
    print("2. 人物提示词生成（名字留空 = 自动提取全本名单）")
    print()

    choice = input("请输入数字（1 或 2）：").strip()

    if choice == "1":
        ep_range = input("请输入要处理的集数范围（例如 1-20）：").strip()
        try:
            start_ep, end_ep = map(int, ep_range.split("-"))
        except ValueError:
            print("输入格式错误，请使用 1-20 这样的格式")
            return
        scene_task(input_file, output_dir, start_ep, end_ep, dry_run, use_cache)

    elif choice == "2":
        names_input = input("请输入要生成的人物名字，多个用逗号分隔（留空 = 自动提取全部）：").strip()
        if not names_input or names_input.lower() in AUTO_ROSTER_KEYS:
            character_task(input_file, output_dir, [], dry_run, use_cache, auto_roster=True)
        else:
            names = [n.strip() for n in names_input.split(",") if n.strip()]
            character_task(input_file, output_dir, names, dry_run, use_cache)

    else:
        print("无效选择")


def main():
    parser = argparse.ArgumentParser(description="漫剧提示词生成器")
    parser.add_argument("--scene", type=str, help="场景任务：集数范围，如 1-20")
    parser.add_argument("--character", type=str,
                        help="人物任务：人物名字（逗号分隔）；填 auto / 全部 = 自动从剧本提取名单")
    parser.add_argument("--input", type=str, default=None, help="输入文件路径")
    parser.add_argument("--output", type=str, default=None, help="输出目录")
    parser.add_argument("--dry-run", action="store_true",
                        help="只核对场景切块 / 人物名单与版数，不调用 API（省钱自检）")
    parser.add_argument("--no-cache", action="store_true", help="忽略缓存，强制重新生成")
    parser.add_argument("--no-merge", action="store_true",
                        help="跳过模型判名，只按确定性规则分组（零 API）")
    parser.add_argument("--merge-only", action="store_true",
                        help="只跑场景合并并出质检报告，不生成场景提示词（省 API）")
    parser.add_argument("--group-by", choices=["env", "episode"], default="env",
                        help="分组口径：env=场景名+内外（默认，同一空间跨集跨时段并成一行）；"
                             "episode=场景名+集号（同名不同集一律不并）")
    parser.add_argument("--full-context", action="store_true",
                        help="【仅影响场景】喂给模型全量剧本原文（含对白与英文译文）。"
                             "默认用精简口径：只留空间证据（△描写/SFX/屏幕字），去对白、去译文，"
                             "体积约为全量的 1/5。人物依据另有一套口径，不受这个开关影响")
    parser.add_argument("--jobs", type=int, default=None,
                        help="并发数，场景与人物默认都是 1（串行，防止短时高并发"
                             "触发反代 API 封禁）。想开并发用 --jobs N；"
                             "每个人物/场景只依赖自己的剧本依据，并发不改变产物。"
                             "换回本地 ds-free-api 代理时务必保持 --jobs 1")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent.parent
    input_dir = base_dir / "input"

    if args.input:
        input_file = args.input
        print(f"[指定输入] {input_file}")
    else:
        input_file = find_input_file(str(input_dir))
        candidates = list_candidates(str(input_dir))
        if len(candidates) > 1:
            print("[注意] input 目录下有多个剧本文件，本次实际使用的是：")
            print(f"        >>> {input_file}")
            print("        其他候选：")
            for c in candidates:
                if c != input_file:
                    print(f"            {c}")
            print("        要换文件请用 --input 指定。")

    if not input_file:
        print(f"错误：在 {input_dir} 目录下没有找到 .txt 或 .docx 文件")
        print("请将剧本文件放入 input 目录，或使用 --input 指定路径")
        return

    output_dir = Path(args.output) if args.output else base_dir / "output"
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    use_cache = not args.no_cache

    jobs = max(1, int(args.jobs or SCENE_CONCURRENCY))
    import ds_api
    ds_api.set_concurrency(jobs)

    # 没有 Key 就别发注定 401 的请求：先给一句清楚的话
    if not DS_API_KEY and not args.dry_run and not args.merge_only:
        print("[错误] 没读到 DeepSeek API Key。请任选一种方式配置：")
        print("       1) 设置环境变量 LLM_API_KEY")
        print(f"       2) 把 Key（base64 一行）写进 "
              f"{Path(__file__).resolve().parent / '.ds_key'}")
        return

    if args.scene and args.character:
        print("[提示] 本次同时指定了场景与人物，将依次执行：先场景，后人物\n")

    ran = False
    if args.scene:
        try:
            start_ep, end_ep = map(int, args.scene.split("-"))
        except ValueError:
            print("集数范围格式错误，请使用 1-20 这样的格式")
            return
        scene_task(input_file, str(output_dir), start_ep, end_ep,
                   args.dry_run, use_cache, args.no_merge, args.merge_only,
                   args.group_by, compact=not args.full_context, jobs=jobs)
        ran = True
    if args.character:
        if ran:
            print("\n" + "=" * 60 + "\n")
        raw = args.character.strip()
        # 人物默认串行（防反代封禁）；--jobs N 显式给了才开并发
        char_jobs = args.jobs
        if raw.lower() in AUTO_ROSTER_KEYS:
            character_task(input_file, str(output_dir), [], args.dry_run, use_cache,
                           auto_roster=True, jobs=char_jobs)
        else:
            names = [n.strip() for n in raw.split(",") if n.strip()]
            character_task(input_file, str(output_dir), names, args.dry_run, use_cache,
                           jobs=char_jobs)
        ran = True
    if not ran:
        interactive_main(input_file, str(output_dir), args.dry_run, use_cache)


if __name__ == "__main__":
    main()
