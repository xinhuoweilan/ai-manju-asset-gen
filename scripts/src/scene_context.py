"""把「场景」映射回剧本原文块 —— 生成提示词的唯一依据。

为什么要单独一个模块：
原实现是拿地点名（如「C市购物中心」）去整本剧本里逐行搜，
但剧本正文（△动作行 / 对白 / SFX）几乎不会复述地点名，
实测只能命中场次标题行本身（1 行），等于没喂剧本 ——
而 system prompt 的最高原则写的是「必须严格根据剧本生成」。

这里改成按场次标题切块：
一块 = 场次标题行 → 下一个场次标题之前的所有行。
一个「场景」可能由多个场次编号组成（合并结果），就把这些块拼起来。

【为什么要有 compact 模式（默认开启）】
合并键是「场景名 + 内外」，所以「容家庄园客厅」这种空间会带 11 个场次、
「环山车道」带 12 个。全量塞进去是 251 行 / 241 行，而且里面
一半是人物对白 —— 对「纯空镜、不要人物」的场景提示词来说，
对白不仅没用，还会把模型往人物和情节上带。

实测（全本 53 行场景，共 2636 行原文）：
    全量（双语 + 对白）                 2636 行，最长 251 行
    compact（只留空间证据）              761 行，最长 86 行，中位 10 行
保留的是 △ 动作行里的环境描写、SFX、屏幕字、其他非对白描写；
丢掉的是纯英文译文、人物对白、出场人物名单、分集标记行。

【对白怎么判定：人名白名单，不靠冒号猜】
白名单有两个来源，缺一不可：
  1. 剧本每场的「出场人物：苏晚意 / 容晏」行 —— 主要角色
  2. 对白行本身的说话人（排除标签词）—— 补上群众角色与简称
     （如「苏晚」是「苏晚意」的简称、「拍卖师」「围观者甲」是群众）
只有「冒号前正好是白名单里的人名（或其前缀）」才算对白。
这样 SFX 行（"SFX：玻璃碎裂。"）、屏幕字、旁白、短信这些
**恰好是空间线索**的行会被安全保留 —— 早期版本用「冒号前是短词就删」
的粗规则，会把「SFX：货架接连倒塌」误删掉。
"""
import re

CODE_RE = re.compile(r"^(\d+)\s*--\s*(\d+)\s*(.*)$")
CJK_RE = re.compile(r"[\u4e00-\u9fa5]")

# 提取人名的行前缀
CHARACTER_LINE_RE = re.compile(r"^(?:出场人物|CHARACTERS)\s*[：:]\s*(.+)$")
# 一个「实打实的英文词」——用来识别中英混排行的英文尾巴。
# 单个字母（C市 / S级 / 8K）不触发，避免把标题行和画质词切坏。
LONG_EN_WORD_RE = re.compile(r"[A-Za-z]{10,}")
# 分集标记行，如「第 7 集」「第54集」「第 7 集（卡点）」
EPISODE_MARK_RE = re.compile(r"^第\s*\d+\s*集\s*(?:（[^）]*）)?\s*$")

# 冒号前是这些词时不是说话人，是剧本标签 —— 别当人名收进白名单
LABEL_WORDS = {
    "出场人物", "屏幕字", "短信", "旁白", "画外音", "音效", "字幕", "标题",
    "画面", "镜头", "地点", "时间", "场景", "道具", "备注", "提示", "注",
    "SFX", "BGM", "VO", "OS", "CHARACTERS",
}


def parse_code(code: str):
    """解析场景编号，格式：集号--场次号时间内外，如 3--1日内。"""
    m = CODE_RE.match(str(code).strip())
    if not m:
        return None
    return {
        "episode": m.group(1),
        "scene_no": m.group(2),
        "time_env": m.group(3).strip(),
    }


def build_block_index(blocks: list) -> dict:
    return {f"{b['episode']}--{b['scene_no']}{b['time_env']}": b for b in blocks}


def _lookup(info: dict, index: dict):
    exact = f"{info['episode']}--{info['scene_no']}{info['time_env']}"
    if exact in index:
        return index[exact]
    # 编号里的时间/内外写法跟剧本原文有出入时，退化成「集号+场次号」匹配
    prefix = f"{info['episode']}--{info['scene_no']}"
    for key, blk in index.items():
        if key.startswith(prefix):
            return blk
    return None


def strip_english(text: str) -> str:
    """去掉纯英文/无中文的行（用于 INCLUDE_BILINGUAL=False 时省 token）。"""
    return "\n".join(ln for ln in text.split("\n") if CJK_RE.search(ln))


def _clean_name(raw: str) -> str:
    """把「苏晚意（平静）」清成「苏晚意」，并滤掉显然不是人名的东西。"""
    nm = re.sub(r"[（(].*?[)）]", "", str(raw)).strip()
    if not nm or not CJK_RE.search(nm):
        return ""
    if len(nm) > 8 or re.search(r"\d", nm):
        return ""
    if nm in LABEL_WORDS:
        return ""
    return nm


def collect_character_names(blocks: list) -> set:
    """收集全剧人名白名单 —— 判定对白的依据。

    两个来源：
      1. 「出场人物：...」行 —— 主要角色，最可靠
      2. 对白行本身的说话人 —— 补群众角色（拍卖师 / 围观者甲）
         和简称（苏晚 → 苏晚意）
    比「冒号前字数少就当成对白」稳得多，不会误伤
    SFX / 屏幕字 / 旁白这类同样带冒号的空间线索行。
    """
    names = set()
    for b in blocks:
        lines = b.get("body_lines", [])

        for line in lines:
            m = CHARACTER_LINE_RE.match(line.strip())
            if not m:
                continue
            for raw in re.split(r"[/、,，]", m.group(1)):
                nm = _clean_name(raw)
                if nm:
                    names.add(nm)

        for line in lines:
            s = line.strip()
            if s.startswith("△"):
                continue
            m = re.match(r"^([^：:]{1,20})[：:]", s)
            if not m:
                continue
            nm = _clean_name(m.group(1))
            if nm:
                names.add(nm)

    return names


def cut_english_tail(line: str) -> str:
    """切掉中英混排行的英文尾巴。

    剧本里偶尔出现中文句和英文译文挤在同一行：
        △容晏摩挲着袖口，启动轮椅，挡在花园通往客厅的必经之路。 △Christopher Evans rubs...
    只切「出现 10 个字母以上的英文词」的行，所以
    「1-1 C市购物中心 日 内」「屏幕字：欢迎回来，S。」不会被切坏。
    """
    m = LONG_EN_WORD_RE.search(line)
    if not m:
        return line
    head = line[: m.start()].rstrip(" \u3000\t△")
    return head if CJK_RE.search(head) else line


def is_character_dialogue(line: str, character_names: set) -> bool:
    """是不是「人名 + 冒号」形式的对白行。"""
    s = line.strip()
    if s.startswith("△"):
        return False
    m = re.match(r"^([^：:]{1,20})[：:]", s)
    if not m:
        return False
    # 「苏晚意（平静）：」→ 剥掉括号里的情绪标注再比对
    head = re.sub(r"[（(].*?[)）]\s*$", "", m.group(1)).strip()
    if not head or not CJK_RE.search(head):
        return False
    if head in character_names:
        return True
    # 简称：「苏晚」是「苏晚意」的前缀（要求至少 2 字，避免单字误伤）
    return len(head) >= 2 and any(n.startswith(head) for n in character_names)


def is_character_roster(line: str) -> bool:
    """是不是人物名单行 —— 对空镜没有价值，剔掉。

    注意这里用「包含」而不是「行首匹配」：
    剧本里有 6 行是英文场景标题和名单挤在一起，形如
        Mountain Circuit Starting Line Night Exterior 出场人物：苏晚意、顾瑾、容征
    这种整行对空镜都是噪音。
    """
    s = line.strip()
    if "出场人物" in s:
        return True
    return bool(re.match(r"^CHARACTERS\b", s, re.I))


def extract_evidence(block: dict, character_names: set) -> list:
    """从一个场次块里抽出「空间证据」行。

    保留：△ 动作行里的环境描写、SFX 行、屏幕字、其他非对白描写。
    剔除：分集标记、纯英文（译文）、人名对白、出场人物名单。
    """
    out = []
    for raw in block.get("body_lines", []):
        line = cut_english_tail(raw.strip())
        if not line:
            continue
        if EPISODE_MARK_RE.match(line):       # 「第 7 集」这类分集标记
            continue
        if not CJK_RE.search(line):           # 纯英文译文行
            continue
        if is_character_roster(line):         # 出场人物名单（含混排行）
            continue
        if is_character_dialogue(line, character_names):
            continue
        out.append(line)
    return out


def _block_header(block: dict) -> str:
    """场次标记头 —— 让模型知道这一段出自哪一集的哪一场。"""
    return (f"【第{block['episode']}集 {block['episode']}-{block['scene_no']} "
            f"{block['location']} {block['time_env']}】")


def build_scene_context(codes: list, block_index: dict, include_bilingual: bool = True,
                        compact: bool = True, character_names: set = None):
    """拼出一个场景的剧本依据。

    返回 (依据文本, 命中说明列表)。命中说明用于日志与排查，
    编不出来的时候能一眼看出是哪个场次没匹配上。

    compact=True（默认）时逐块抽取空间证据行，并在每块前加场次标记头；
    同一行完全重复的只留一次。compact=False 时保留旧的全量口径。
    """
    parts = []
    notes = []
    for code in codes:
        info = parse_code(code)
        if not info:
            notes.append(f"{code}（编号无法解析）")
            continue
        blk = _lookup(info, block_index)
        if not blk:
            notes.append(f"{code}（未找到对应场次块）")
            continue

        if compact:
            evidence = extract_evidence(blk, character_names or set())
            if not evidence:
                notes.append(f"{code}（0 行证据）")
                continue
            parts.append(_block_header(blk) + "\n" + "\n".join(evidence))
            notes.append(f"{code}（{len(evidence)} 行）")
        else:
            parts.append(blk["body_text"])
            notes.append(f"{code}（{len(blk['body_lines'])} 行）")

    if compact:
        # 同一空间跨多集反复出现，同一句描写可能重复多次 —— 只留首次
        seen = set()
        deduped = []
        for part in parts:
            head, _, body = part.partition("\n")
            keep = []
            for line in body.split("\n"):
                key = line.strip()
                if key and key in seen:
                    continue
                seen.add(key)
                keep.append(line)
            deduped.append(head + ("\n" + "\n".join(keep) if keep else ""))
        parts = deduped

    text = "\n\n".join(parts)
    if not compact and not include_bilingual:
        text = strip_english(text)
    return text, notes
