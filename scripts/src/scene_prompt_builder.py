import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import INCLUDE_BILINGUAL, SCENE_CONCURRENCY
from ds_api import call_ds_api
from cache_utils import load_cache, save_cache
from progress import format_duration, progress_line
from scene_context import (build_block_index, build_scene_context,
                           collect_character_names)


SCENE_SYSTEM_PROMPT = """你是一个场景提示词生成器。你的最高原则是：场景必须严格根据剧本内容生成。

【最高原则：剧本驱动】
必须严格参照我提供的剧本原文生成场景。
从剧本中提取所有可用的地点、时间、内外、环境、固定结构、固定家具、固定装置、材质、文字标识、氛围线索。
剧本没写的风格元素不要强加。
剧本写什么世界，就生成什么世界。
剧本写什么空间，就生成什么空间。
剧本里发生的事，要在地面的空间状态上留下对应痕迹。

【禁止脱离剧本】
不要套用固定模板。
不要因为之前生成过某种风格，就默认下一个场景也是那种风格。
不要自行发明剧本里没有的场景类型。

【画风】
默认伪真人画风，电影级实拍感，超写实材质，数字人/影视剧照感。
不做二次元、不做纯插画、不做明显漫画风。

【内容】
只生成场景，不出现任何人物、剪影、手、脸、身体、人群。纯空镜。

【禁止出现：可移动物体】
场景中不要出现可移动物体，例如：车辆、枪械、刀具、手机、托盘、礼物盒、餐具、酒杯、花篮、路障等一切可移动物品。
只保留空间本身：建筑、墙面、地面、天花板、门窗、固定结构、固定家具、固定装置、固定装饰、材质、颜色、氛围。

【空间状态规则】
空间一律呈现完好、正常、整洁的原本状态——这是空间外观参考图，不是灾难现场。
严禁出现破损、破败、坍塌、废墟、烧灼痕迹、结构断裂、倒塌或倾覆的柜体货架、开裂的墙体与地面。
即使剧本里发生了打斗、破坏、灾难等情节，场景图也不得呈现损坏形态，一律做正常状态。
唯一例外：剧本明确标注该空间本身就是废墟/已损毁（如「营帐废墟」），才按剧本呈现其既有状态。

【禁止出现：碎片、血迹与污渍】
严禁出现碎玻璃渣、散落碎片、纸屑等小型可移动物。
严禁出现血迹、血痕、溅血。
不要描述水渍、泥渍、锈渍等污渍。

【文字语言规则】
如果剧本是中英双语，场景中出现的文字、标识、招牌、门牌、路标、楼层指引、机构名称、标语、告示、屏幕文字等，一律使用英文。
如果剧本是纯中文，则按剧本语境决定文字语言。

【三份差异】
同一个场景生成三份提示词。
三份必须是三个完全不同的场景设计方案。
不是换角度，不是换整洁度，不是通过脏乱凸显不同。
而是空间设计、风格、布局、概念明显不同。
三份都必须符合同一个剧本设定。

【禁止】
不要光影描述。
不要聚焦局部。
不要只写一个角落。
不要出现人物相关元素。
不要出现可移动物体。
不要出现碎玻璃渣与散落碎片。
不要描述血迹与污渍。
不要出现破损、破败、坍塌、废墟等任何损坏形态（空间一律正常完好）。
不要脱离剧本自行发明场景。

【场景范围】
整个房间 / 整个空间 / 整个区域全景。
从门口向内、从一角看向整体、从外部看向整体等，但必须是完整空间。

【输出格式】
每份包含：
伪真人电影级实拍、地点、时间、内外、空无一人、全景/广角/标准镜头、完整空间描述、颜色、材质、画质、空镜、无人物、无剪影、无手、无脸。

常用画质后缀：
超写实材质，电影胶片颗粒，ARRI Alexa 拍摄，35mm 或 50mm 镜头，高动态范围，8K，空镜，无人物，无剪影，无手，无脸。

【输出要求】
请直接输出三份中文提示词。
每份提示词前标注"方案一："、"方案二："、"方案三："。
每份提示词之间用空行分隔。"""


# 依据口径版本号。改动 scene_context 的证据抽取规则时要一起改，
# 否则旧缓存会把「按老依据生成的提示词」当成新结果复用。
CONTEXT_VERSION = "v3-no-damage"

# 缓存指纹：只要上面的 system prompt、INCLUDE_BILINGUAL 或依据口径变了，
# 指纹就变，旧缓存自动失效 —— 不会把按老规则生成的提示词当成新的复用。
CACHE_FINGERPRINT = hashlib.md5(
    (SCENE_SYSTEM_PROMPT + "|" + str(INCLUDE_BILINGUAL) + "|" + CONTEXT_VERSION).encode("utf-8")
).hexdigest()[:8]

VARIANT_RE = re.compile(r"方\s*案\s*[一二三四1234]\s*[:：]")


def build_scene_user_prompt(location: str, time_env: str, script_context: str,
                            block_count: int = 1) -> str:
    if block_count > 1:
        multi = f"""
注意：上面这段依据来自 **同一处空间的 {block_count} 个场次**（标题里的集号不同，但地点是同一个）。
请提取这些场次里**稳定成立**的空间特征：建筑结构、格局、材质、固定家具、固定装置、色彩基调。
某个场次里才有的临时物品、临时摆设、节日装饰不要带进来。
剧本里出现的破坏痕迹（塌陷、破损、烧灼等）一律不要带进空间外观——空间保持完好正常。
"""
    else:
        multi = ""
    return f"""场景地点：{location}
时间 / 内外：{time_env}

以下是该场景所在场次的剧本原文（这是你唯一的依据）：
--------
{script_context}
--------
{multi}
请严格依据上面的剧本原文，为这个场景生成三份不同的场景提示词。"""


def _time_envs_from_codes(codes: list) -> str:
    out = []
    for code in codes:
        m = re.search(r"[晨早午日黄昏夜雨]+[内外]", str(code))
        if m and m.group(0) not in out:
            out.append(m.group(0))
    return "、".join(out)


def split_variants(text: str) -> list:
    """把模型输出拆成三版。

    原来只认全角冒号的「方案一：」，一旦模型写成「方案一:」或加粗，
    就直接降级成「把整段原文复制三份」—— 产物看着有三版其实一模一样。
    这里多兜一层，并且宁可少给一版也不复制充数。
    """
    parts = [p.strip() for p in VARIANT_RE.split(str(text))]
    parts = [p for p in parts if len(p) > 20]
    if len(parts) >= 3:
        return parts[:3]

    chunks = [c.strip() for c in re.split(r"\n\s*\n", str(text)) if len(c.strip()) > 20]
    if len(chunks) >= 3:
        return chunks[:3]

    return parts


def _generate_one(idx: int, total: int, scene_name: str, codes: list, cache_key: str,
                  block_index: dict, character_names: set, cache_dir,
                  use_cache: bool, compact: bool) -> tuple:
    """生成一个场景的三版提示词。返回 (结果, 是否降级)。

    只读自己那几个剧本块、只写自己那一份缓存 —— 没有任何跨场景的共享状态，
    所以丢进线程池并发跑，产物与串行逐个跑**逐字节一致**，
    区别只是不用排队干等 API 返回。
    """
    script_context, notes = build_scene_context(
        codes, block_index, INCLUDE_BILINGUAL,
        compact=compact, character_names=character_names,
    )
    line_count = len([ln for ln in script_context.split("\n") if ln.strip()])

    if line_count == 0:
        print(f"[{idx}/{total}] {scene_name} —— [警告] 没匹配到剧本正文，依据为空！")
    else:
        print(f"[{idx}/{total}] {scene_name} —— 剧本依据 {line_count} 行"
              f"（{'; '.join(notes)}）")

    user_prompt = build_scene_user_prompt(scene_name, _time_envs_from_codes(codes),
                                          script_context, block_count=len(codes))
    text = call_ds_api(SCENE_SYSTEM_PROMPT, user_prompt, temperature=0.7, max_tokens=2500)

    variants = split_variants(text)
    degraded = len(variants) < 3
    if degraded:
        print(f"[{idx}/{total}] {scene_name} —— [警告] 只解析出 {len(variants)} 版"
              f"（期望 3 版），已原样保留、不复制充数")
        if not variants:
            variants = [text.strip()]
    else:
        print(f"[{idx}/{total}] {scene_name} ✓")

    item = {
        "scene": scene_name,
        "codes": codes,
        "prompts": variants[:3],
        "degraded": degraded,
        "context_lines": line_count,
    }
    if use_cache:
        save_cache(cache_dir, cache_key, item)
    return item, degraded


def generate_scene_prompts(scene_rows: list, blocks, cache_dir: str = None,
                           use_cache: bool = True, compact: bool = True,
                           jobs: int = None) -> list:
    # 兼容老调用：过去第二个参数传的是「剧本全文」，这里顺手切成块
    if isinstance(blocks, str):
        from scene_extractor import split_into_blocks
        blocks = split_into_blocks(blocks)

    jobs = max(1, int(jobs or SCENE_CONCURRENCY))
    block_index = build_block_index(blocks)
    character_names = collect_character_names(blocks) if compact else set()
    total = len(scene_rows)

    # 先摘掉命中缓存的：既不排队、也不再花钱
    results = [None] * total
    todo = []
    for i, row in enumerate(scene_rows, 1):
        cache_key = f"scene::{row[0]}::{'|'.join(row[1:])}::{CACHE_FINGERPRINT}"
        if use_cache:
            cached = load_cache(cache_dir, cache_key)
            if cached:
                print(f"[{i}/{total}] {row[0]} —— 命中缓存，跳过")
                results[i - 1] = cached
                continue
        todo.append((i, row, cache_key))

    done = []          # [(行号, 场景名)]，用来按原顺序汇报降级项
    workers = min(jobs, len(todo)) if todo else 1
    if workers > 1:
        print(f"           {len(todo)} 个场景待生成，{workers} 路并发")

    def _collect(idx, item, degraded):
        results[idx - 1] = item
        if degraded:
            done.append((idx, item["scene"]))

    if workers <= 1:
        started = time.time()
        for k, (idx, row, cache_key) in enumerate(todo, 1):
            item, degraded = _generate_one(
                idx, total, row[0], row[1:], cache_key, block_index,
                character_names, cache_dir, use_cache, compact)
            _collect(idx, item, degraded)
            print(f"  {progress_line(k, len(todo), started, row[0])}")
        if todo:
            print(f"\n本次共生成 {len(todo)} 个场景，用时 {format_duration(time.time() - started)}")
    else:
        pool = ThreadPoolExecutor(max_workers=workers)
        try:
            futures = {
                pool.submit(_generate_one, idx, total, row[0], row[1:], cache_key,
                            block_index, character_names, cache_dir,
                            use_cache, compact): idx
                for idx, row, cache_key in todo
            }
            for fut in as_completed(futures):
                item, degraded = fut.result()
                _collect(futures[fut], item, degraded)
        except BaseException:
            # 已跑完的场景都已落盘，重跑会直接跳过 —— 不浪费已经花掉的钱
            pool.shutdown(wait=False, cancel_futures=True)
            print("\n  [中断] 已完成的场景已写入缓存，重新运行会自动跳过它们")
            raise
        pool.shutdown(wait=True)

    if done:
        done.sort()
        names = [n for _, n in done]
        print(f"\n[注意] 以下 {len(names)} 个场景的三版解析不完整，建议重跑：{names}")

    return results
