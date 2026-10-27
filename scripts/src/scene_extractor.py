"""场次切块 + 场景归并。

归并分两个阶段，职责严格分开：

阶段 1（纯代码，零幻觉风险）
    按「场景名 + 内外」分组。
    归一后名字相同、且同为内景（或同为外景）的场次并到一行 —— 同一处空间
    不管出现在第几集、是白天还是夜里，都是同一处空间，只需要一份场景设定。
    内外不同则**一定**分成两行：内景和外景是两套完全不同的画面。
    这一阶段保证：124 个场次编号一个不少、一个不重。

阶段 2（LLM，只做语义判断）
    只回答一个问题：这些**名字不同**的场景名里，哪些其实指同一个空间？
    输出是「原名 -> 统一后名」的映射表，体量极小，不参与编号分配。
    模型没有机会漏场次，也没有机会把同名场次合并掉。

把编号分配权和名字判断权分开，是因为前者是确定性的（代码做）
而后者需要语义理解（模型做）。混在一起做就会两头都不可靠。
"""
import difflib
import itertools
import re

from ds_api import call_ds_api

SCENE_LINE_RE = re.compile(r"^(?:第(\d+)集\s+)?(\d+)[-—](\d+)\s+(.*)$")
CJK_RE = re.compile(r"[\u4e00-\u9fa5]")

# 时间词表：必须用「词组」而不是单字字符类。
# 原实现是 [晨早午日黄昏夜雨] 这种字符类，"傍晚"里的「晚」不在其中，
# 于是 "51-2 容家庄园客厅 傍晚 内" 的时间内外没被剥离，场景名残成
# "容家庄园客厅 傍晚 内" —— 与另外 9 个"容家庄园客厅"名字不同，
# 会被当成「同空间的不同叫法」而跨集归并。这是必须堵掉的坑。
TIME_WORDS = [
    "清晨", "早晨", "凌晨", "早上", "上午", "中午", "正午", "下午",
    "傍晚", "黄昏", "日间", "白天", "夜里", "夜晚", "深夜", "半夜",
    "午夜", "上半夜", "下半夜",
    "晨", "早", "午", "日", "夜", "雨",
]
_TIME_ALT = "|".join(sorted(TIME_WORDS, key=len, reverse=True))
TIME_ENV_RE = re.compile(r"(" + _TIME_ALT + r")\s*([内外])\s*$")


def is_chinese_scene_line(line: str) -> bool:
    """中文场次标题行（用来滤掉紧随其后的英文翻译行）。"""
    m = SCENE_LINE_RE.match(str(line).strip())
    if not m:
        return False
    return bool(CJK_RE.search(m.group(4)))


def parse_scene_line(line: str):
    m = SCENE_LINE_RE.match(str(line).strip())
    if not m:
        return None

    rest = m.group(4).strip()
    t = TIME_ENV_RE.search(rest)
    if t:
        time_env = t.group(1) + t.group(2)
        location = rest[:t.start()].strip()
    else:
        time_env = ""
        location = rest

    return {
        "episode": m.group(2),
        "scene_no": m.group(3),
        "location": location,
        "time_env": time_env,
    }


def split_into_blocks(text: str) -> list:
    """按场次标题把剧本切成块。

    一块 = 场次标题行 → 下一个场次标题之前的所有行。
    这是「这个场景的剧本依据」的最小单位，也是喂给模型的原始材料。
    """
    lines = [ln.rstrip() for ln in text.split("\n")]
    heads = [i for i, ln in enumerate(lines) if is_chinese_scene_line(ln)]

    blocks = []
    for k, start in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        info = parse_scene_line(lines[start])
        if not info:
            continue
        body = [ln.strip() for ln in lines[start:end] if ln.strip()]
        info["header_line"] = lines[start].strip()
        info["body_lines"] = body
        info["body_text"] = "\n".join(body)
        blocks.append(info)
    return blocks


def block_code(b: dict) -> str:
    """场次编号，如 53--3夜外。"""
    return f"{b['episode']}--{b['scene_no']}{b['time_env']}"


def env_kind(time_env: str) -> str:
    """把「日内 / 夜内 / 傍晚内 / 深夜内 / 日外 / 夜外…」压成「内」或「外」。

    时段（日 / 夜 / 晨 / 傍晚 …）**不参与区分**：同一处空间的内部，
    不管剧本写的是白天还是夜里，都是同一处空间，共用一份场景设定。
    只有内景与外景必须分开 —— 它们是两套完全不同的画面。
    """
    t = str(time_env or "")
    if "内" in t:
        return "内"
    if "外" in t:
        return "外"
    return t


def scene_display_name(canon: str, env: str) -> str:
    """输出行名：场景名（内景）/ 场景名（外景）。"""
    return f"{canon}（{env}景）" if env in ("内", "外") else canon


# ---------------------------------------------------------------- 阶段 2：归一

NORMALIZE_SYSTEM_PROMPT = """你是漫剧场景整理专家。你只做一件事：判断哪些场景名其实指同一个空间。

【输入】
场景名清单，每行：场景名 <TAB> 出现过的集号

【要处理的两种「同一空间」】
情况 A：同一片场地里的「位置」被写成了不同名字 → 统一到这个场地的名字。
  环山车道 / 环山车道 起跑线 / 环山车道 大屏幕前 / 环山车道 赛道终点线前
      → 全部统一成「环山车道」
  马术俱乐部-马场 / 马术俱乐部赛道 / 马术俱乐部赛场终点
      → 全部统一成「马术俱乐部」
  邮轮顶层宴会大厅 / 邮轮顶层宴会大厅角落
      → 全部统一成「邮轮顶层宴会大厅」
  判别方法：镜头推过去，是不是还在同一片场地里？
    在   → 是这片场地里的「位置」，可以统一
          （赛道、马场、起终点、看台、大屏幕前、赛车旁、角落、深处、中庭…）
    不在 → 是「另一处空间」，绝对不能统一（见下面 ★★★）

情况 B：只是叫法/写法不同，其实是一处 → 统一到一个名字。
  容晏书房 与 容晏书房内   → 容晏书房
  环山车道 大屏幕前 与 环山车道大屏幕前（有没有空格） → 同一个地方，统一
  马术俱乐部 休息区 与 马术俱乐部休息区（有没有空格） → 同一个地方，统一
  错别字、近形字、近音字写成了不同名字，如果指的是同一处，也要统一：
    马场俱乐部 / 马术俱乐部  → 同一处

分隔符可能是「·」「-」「空格」或什么都不用，都按同一套规则处理。

【绝对不要统一的情况 · 最容易做错】
★★★ 「另一处房间 / 空间」只是名字以场地名开头，绝不能并进场地名：
    「马术俱乐部」与「马术俱乐部休息区」→ 不统一（休息区是另一个房间）
    「马术俱乐部」与「马术俱乐部车库」「马场俱乐部车库」→ 不统一（车库是另一个房间）
    同理，下面这些后缀都表示「换了另一个空间」，一律不并进前面的场地名：
      休息区 / 休息室 / 车库 / 更衣室 / 化妆间 / 包厢 / 洗手间 / 卫生间 /
      办公室 / 会议室 / 房间 / 卧室 / 客厅 / 书房 / 餐厅 / 厨房 / 后台 /
      仓库 / 走廊 / 电梯 / 楼梯 / 阳台 / 天台 / 病房 / 手术室
    判别方法同上：镜头推过去，是不是还在同一片场地里。
★★★ 门口 / 门外 / 走廊 与 里面的空间，是两个不同空间，绝对不要统一：
    「苏晚意房间」与「苏晚意房间门口」→ 不统一
    「手术室」与「手术室门外走廊」→ 不统一
    「邮轮顶层宴会大厅」与「邮轮洗手间外走廊」→ 不统一
★★★ 一个是「车上/车内」，一个是「路/道」本身，不是同一空间，不要统一：
    「环山车道」与「环山车道 车内」→ 不统一
    「郊外公路」与「郊外山路 车内」→ 不统一
★★★ 名字里带了不同限定词、明显是两处的，不要统一：
    「示例场景名」与「容家庄园小楼走廊」→ 不统一
    「公司办公区」与「公司会议室」「公司总裁办公室」「公司门口」→ 都是不同的房间，不统一
    「容家庄园客厅」与「容家庄园客房」→ 不统一
★★★ 只是名字里有一个字重合、或都属于同一栋楼的，不要统一。
★★★ 名字完全相同的，本来就会被分别处理，你不用管。

【拿不准就不要输出】
宁可漏掉一处该统一的，也绝对不要错统一两处不同的空间。错统一会让两个
不同空间共用一份场景提示词，代价远大于漏统一。

【输出格式】
只输出**需要改名**的映射，制表符分隔两列：原名<TAB>统一后的名字
不需要改的名字一律不要出现在输出里。
如果一条都不需要改，只输出两个字：无

不要解释、不要 Markdown 表格、不要编号、不要任何多余文字。

例如：
环山车道 起跑线	环山车道
环山车道 大屏幕前	环山车道
环山车道大屏幕前	环山车道
容晏书房内	容晏书房
"""


def find_candidate_pairs(names: list, ratio_threshold: float = 0.5) -> list:
    """找出「名字相近、必须逐对明确判断」的名字对。

    两类：
      1) 去空格后一个是另一个的子串 —— 典型的「整体与局部」
      2) 相似度高 —— 错别字 / 近形近音，如「马场俱乐部」与「马术俱乐部」

    这些对会被显式塞进 prompt，逼模型逐对表态。
    实测不做这一步会漏统一（环山车道 赛车旁、邮轮顶层宴会大厅角落 都被漏过）。
    """
    pairs = []
    norm = {n: n.replace(" ", "") for n in names}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            na, nb = norm[a], norm[b]
            if na in nb or nb in na:
                pairs.append((a, b))
                continue
            if abs(len(na) - len(nb)) > 3:
                continue
            if difflib.SequenceMatcher(None, na, nb).ratio() >= ratio_threshold:
                pairs.append((a, b))
    return pairs


# ------------------------------- 确定性的「场地 + 局部」归并（代码兜底）
#
# 判断标准不是「有没有墙」，而是：**镜头推过去，是不是还在同一片场地里**。
#
#   ① 同一片场地里的「位置」被写成了不同名字
#        → 并进这个场地的名字（一张空镜覆盖得了）
#        环山车道 + 赛道 / 起跑线 / 终点线前 / 大屏幕前 / 赛车旁 → 环山车道
#        马术俱乐部 + 马场 / 赛道 / 赛场终点                    → 马术俱乐部
#        邮轮顶层宴会大厅 + 角落                                → 邮轮顶层宴会大厅
#
#   ② 另一处「独立空间」被写成了「场地名 + 后缀」
#        → **绝不并进场地名**，各自成行（换一处空间，就是另一张空镜）
#        马术俱乐部 + 休息区   ← 换了一个房间
#        马术俱乐部 + 车库     ← 同理
#
#   ③ 隔了一层的（门外 / 车内）
#        → 连「场地名 + 后缀」的形状都不成立，同样不并
#
# ② 是最容易做错的一类：「马术俱乐部休息区」看着像马术俱乐部的一部分，
# 其实是另一个房间。模型在这一点上不可靠（实测它会主动把休息区并进马术俱乐部），
# 所以这里用词表硬拦 —— 模型判了也拦。

# 同一片场地内的位置/部位：多出来的部分以这些词结尾 → 归到场地名
OPEN_PART_SUFFIXES = (
    "赛道终点线前", "终点线前", "赛场终点", "终点线", "终点区", "终点",
    "大屏幕前", "大屏幕", "赛道", "赛场", "起点", "起跑线", "VIP看台", "看台",
    "赛车旁", "角落", "深处", "深", "马场", "入口", "浅区", "中庭", "中央",
    "一侧", "尽头", "内", "里",
)

# 另一处独立空间：多出来的部分含这些词 → 严禁并进场地名
ENCLOSED_PART_SUFFIXES = (
    "休息区", "休息室", "更衣室", "化妆间", "包厢", "洗手间", "卫生间",
    "办公室", "会议室", "房间", "卧室", "客厅", "书房", "餐厅", "厨房",
    "后台", "仓库", "车库", "储物间", "储藏室", "器材室", "器械室",
    "走廊", "电梯", "楼梯", "阳台", "天台", "病房", "手术室",
)

# 隔了一层：门外 / 车内 之类，既不是同一处，也不构成「场地名 + 后缀」
SEPARATE_SPACE_MARKERS = (
    "门口", "门外", "外走廊", "车内", "车上", "外面", "屋外", "里面", "屋内",
)


def norm_name(name: str) -> str:
    """去掉空格与各种分隔符，专用于比较名字。"""
    return re.sub(r"[\s·•\-—/、]", "", str(name))


def split_local_part(name: str) -> tuple:
    """把名字拆成 (前段, 尾部局部词, 类型)。

    类型：
      open      同一片场地里的位置（赛道 / 角落 / 看台…）→ 可以并进场地名
      enclosed  另一处独立空间（休息区 / 车库 / 包厢…）→ 绝不能并进场地名
      separate  隔了一层（门口 / 车内…）→ 同样不能并
      ""        名字尾部不带局部词
    按最长匹配优先，避免「赛道终点线前」被更短的「终点线前」截短。
    「隔了一层」要排在 open 前面 —— 否则「环山车道 车内」会被当成
    「环山车道车 + 内（里面）」，判成同一处。
    """
    n = norm_name(name)
    for w in sorted(SEPARATE_SPACE_MARKERS, key=len, reverse=True):
        if n.endswith(w) and len(n) > len(w):
            return n[: -len(w)], w, "separate"
    for w in sorted(OPEN_PART_SUFFIXES, key=len, reverse=True):
        if n.endswith(w) and len(n) > len(w):
            return n[: -len(w)], w, "open"
    for w in sorted(ENCLOSED_PART_SUFFIXES, key=len, reverse=True):
        if n.endswith(w) and len(n) > len(w):
            return n[: -len(w)], w, "enclosed"
    return n, "", ""


def scan_local_pairs(names: list, extra_shorts: list = None):
    """枚举所有「场地名 + 后缀」形状的名字对，标出后缀属于哪一类。

    产出 (长名, 短名, 多出来的部分, 类型)，类型见 split_local_part。

    extra_shorts：额外当作「场地名」来比的名字。归一之后才会出现的场地名
    （如「马术俱乐部」本身在剧本里从来没单独出现过，只存在于「马术俱乐部赛道」
    这样的写法里）也要能当短名，否则「马术俱乐部休息区」这类就扫不出来。
    """
    shorts = list(names) + [s for s in (extra_shorts or []) if s not in names]
    norm = {n: norm_name(n) for n in list(names) + shorts}
    seen = set()
    for short in shorts:
        ns = norm[short]
        if any(m in ns for m in SEPARATE_SPACE_MARKERS):
            continue
        for long in names:
            if long == short:
                continue
            nl = norm[long]
            if len(nl) <= len(ns) or not nl.startswith(ns):
                continue
            extra = nl[len(ns):]
            if any(m in extra for m in SEPARATE_SPACE_MARKERS):
                kind = "separate"
            elif any(w in extra for w in ENCLOSED_PART_SUFFIXES):
                kind = "enclosed"
            elif any(extra.endswith(w) for w in OPEN_PART_SUFFIXES):
                kind = "open"
            else:
                continue
            if (long, short) in seen:
                continue
            seen.add((long, short))
            yield long, short, extra, kind


def infer_local_merges(names: list) -> dict:
    """同一片场地里的「位置」→ 并进场地名。只处理 open 这一类。

    环山车道 + 赛车旁      → 环山车道
    马术俱乐部 + 赛场终点   → 马术俱乐部
    邮轮顶层宴会大厅 + 角落 → 邮轮顶层宴会大厅

    反例（**绝不**并，见 ENCLOSED_PART_SUFFIXES）：
    马术俱乐部 + 休息区 → 休息区是另一个房间，是另一处空间
    马术俱乐部 + 车库   → 同理
    苏晚意房间 + 门口   → 门口是门外的另一个空间
    环山车道 + 车内     → 车内是在车里，不是路本身

    加这一步是因为实测纯靠模型既会漏（环山车道 赛车旁、邮轮顶层宴会大厅角落
    连续两轮被漏掉），也会错判（把马术俱乐部休息区并进马术俱乐部）。
    """
    return {long: short for long, short, _extra, kind in scan_local_pairs(names)
            if kind == "open"}


def collect_blocked_pairs(names: list, extra_shorts: list = None) -> list:
    """列出「场地名 + 后缀」形状、但**故意没并**的名字对（供质检报告用）。

    让用户一眼看到哪些归并是被规则拦下的，而不是被漏掉的。
    """
    out = []
    for long, short, extra, kind in scan_local_pairs(names, extra_shorts):
        if kind == "separate":
            out.append((long, short, "多出来的部分是「门外 / 车内」这种隔了一层的空间"))
        elif kind == "enclosed":
            out.append((long, short, f"多出来的「{extra}」是另一处独立空间"))
    return out


def infer_spelling_variants(names: list) -> dict:
    """「去掉分隔符后完全一样」= 同一处空间的两种写法 → 统一到一个名字。

    马术俱乐部 休息区  ↔  马术俱乐部休息区
    环山车道 大屏幕前  ↔  环山车道大屏幕前
    统一到不带分隔符的写法（本剧绝大多数场景名都不带空格）。
    """
    groups = {}
    for n in names:
        groups.setdefault(norm_name(n), []).append(n)
    out = {}
    for key, members in groups.items():
        if len(members) < 2:
            continue
        canon = next((m for m in members if m == key), members[0])
        for m in members:
            if m != canon:
                out[m] = canon
    return out


def drop_illegal_merges(mapping: dict) -> tuple:
    """拦下「把另一处独立空间并进场地名」的映射（模型给的也要过这一关）。

    返回 (保留的映射, 被拦下的条目)。被拦下的不是漏归，而是**错归** ——
    两个不同空间共用一份提示词，代价远大于漏归。
    """
    kept, rejected = {}, []
    for old, new in mapping.items():
        no, nn = norm_name(old), norm_name(new)
        if no == nn or len(no) <= len(nn) or not no.startswith(nn):
            # 不是「场地名 + 后缀」的形状。但「错别字 + 局部词」也要拦：
            # 马场俱乐部车库 → 马术俱乐部（车库照样是另一处空间）
            base, suffix, kind = split_local_part(old)
            if (kind == "enclosed" and suffix and len(base) == len(nn)
                    and sum(1 for a, b in zip(base, nn) if a != b) == 1):
                rejected.append((old, new, f"多出来的「{suffix}」是另一处独立空间"))
            else:
                kept[old] = new
            continue
        extra = no[len(nn):]
        if any(m in extra for m in SEPARATE_SPACE_MARKERS):
            rejected.append((old, new, "多出来的部分是隔了一层的空间（门外 / 车内）"))
        elif any(w in extra for w in ENCLOSED_PART_SUFFIXES):
            rejected.append((old, new, f"多出来的「{extra}」是另一处独立空间"))
        else:
            kept[old] = new
    return kept, rejected


def fix_typo_in_base(names: list, valid: set, canonical_pool=None) -> dict:
    """错别字纠正：**只改名，不并场景**。

    马场俱乐部车库 → 马术俱乐部车库
    （「马场俱乐部」是「马术俱乐部」的错写；但车库仍是另一处空间，不并进去）

    只在下面几条都成立时才动手，避免越权改名：
      · 后缀属于「另一处独立空间」（open 的已经在上一步并掉了，separate 不碰）
      · 前段本身**不是**真实场景名 —— 是的话说明这是正常的「场地 + 局部」，
        不是错别字（例：「容家庄园客厅」的前段「容家庄园」不是场景名，但后面
        那批「容家庄园XX」互相之间是同一个前缀，靠下面的等长+一字之差挡掉）
      · 候选名必须与前段**等长、且只差一个字**（真错别字的样子：马场 ↔ 马术）
        用相似度阈值做不到这么严 —— 「邮轮顶层套房」会被 0.8 的相似度
        错配到「邮轮顶层走廊」上去。

    canonical_pool：归一之后才会出现的场地名（「马术俱乐部」在剧本里从没单独
    出现过，只存在于「马术俱乐部赛道」这类写法里），要用上，否则配不到它。
    """
    pool = []
    for t in list(names) + list(canonical_pool or []):
        nt = norm_name(t)
        if nt and nt not in pool:
            pool.append(nt)

    out = {}
    for n in names:
        base, suffix, kind = split_local_part(n)
        if kind != "enclosed" or not suffix or base in valid or len(base) < 3:
            continue
        best = None
        for nt in pool:
            if len(nt) != len(base) or nt == base:
                continue
            if sum(1 for a, b in zip(base, nt) if a != b) == 1:
                best = nt
        if best:
            new = best + suffix
            if new != n:
                out[n] = new
    return out


def postprocess_map(llm_map: dict, stats: list) -> dict:
    """把模型给的映射与代码推出的确定性映射合并。

    优先级（后面的覆盖前面的）：
        写法变体（代码） < 模型判名（过完拦截） < 场地名 + 位置（代码）
    代码规则优先，因为它是从定义出发的；模型只是概率猜测。

    代码规则（写法变体 / 位置归并 / 错别字）**只在这里算一次**，
    归并结果也在这里汇报，别处不再重复计算。
    """
    names = [n for n, _ in stats]
    valid = set(names)

    llm_map = {k: v for k, v in llm_map.items() if k in valid and v and v != k}
    llm_map, rejected = drop_illegal_merges(llm_map)
    for old, new, why in rejected:
        print(f"  [拦下] {old}  →  {new}：{why}（改为各自成行）")

    spelling = infer_spelling_variants(names)
    local = infer_local_merges(names)
    if local:
        print(f"           代码规则补充 {len(local)} 条「整体+内部局部」归并：")
        for old, new in local.items():
            print(f"             {old}  →  {new}")

    merged = {**spelling, **llm_map, **local}
    merged.update(fix_typo_in_base(names, valid, set(merged.values())))

    return merged


def collect_name_stats(blocks: list) -> list:
    """按首次出现顺序返回 [(场景名, [集号...]), ...]。"""
    order = []
    eps = {}
    for b in blocks:
        loc = b["location"]
        if loc not in eps:
            order.append(loc)
            eps[loc] = []
        if b["episode"] not in eps[loc]:
            eps[loc].append(b["episode"])
    return [(loc, eps[loc]) for loc in order]


def parse_name_map(response: str) -> dict:
    """解析「原名<TAB>新名」映射表。"""
    mapping = {}
    for line in str(response).strip().split("\n"):
        line = line.strip().strip("|").strip()
        if not line or line in ("无", "无。", "None"):
            continue
        if line.startswith("原名") or set(line) <= set("-| :\t"):
            continue
        parts = [p.strip() for p in re.split(r"\t|\s{2,}|\|", line) if p.strip()]
        if len(parts) != 2:
            continue
        old, new = parts
        if old and new and old != new:
            mapping[old] = new
    return mapping


def normalize_scene_names_with_llm(blocks: list, cache_dir: str = None,
                                   use_cache: bool = True) -> tuple:
    """让模型给出「异名归一」映射。返回 (mapping, raw_response)。

    只有名字不同的场景才需要模型判断；名字相同的场次由 group_scenes 直接
    按「名字 + 集号」分组，压根不经过模型。
    """
    stats = collect_name_stats(blocks)
    if len(stats) <= 1:
        return {}, "(场景名不足两个，跳过归一)"

    valid_names = {name for name, _ in stats}
    entries = [f"{name}\t{','.join(eps)}" for name, eps in stats]

    pairs = find_candidate_pairs([n for n, _ in stats])
    pair_block = ""
    if pairs:
        pair_lines = "\n".join(f"  {a}  /  {b}" for a, b in pairs)
        pair_block = (
            f"\n\n【必须逐对明确判断的名字对】\n"
            f"下面这些名字彼此高度相似（互相包含，或只差一两个字，可能是错别字）。\n"
            f"它们**可能**指同一空间，也**可能**不是，请逐对表态，"
            f"不要默认合并、也不要一律跳过：\n\n"
            f"{pair_lines}\n\n"
            f"提醒：判断「局部」时用这把尺子 ——\n"
            f"· 算同一空间，要统一：一个空间**内部**的位置 / 区域 / 部位，\n"
            f"  例如 赛道、起跑线、终点线前、VIP看台、大屏幕前、赛车旁、角落、深处、\n"
            f"  休息区、马场、车库、入口、浅区。\n"
            f"· 不算同一空间，不要统一：\n"
            f"  「门口 / 门外 / 外面的走廊」是门外的另一个空间；\n"
            f"  「车内 / 车上」是在车里，不是路本身。"
        )

    user_prompt = (
        f"以下是全部 {len(stats)} 个场景名（一个都不少），"
        f"请按规则给出需要统一的映射：\n\n" + "\n".join(entries)
        + pair_block
    )

    # 缓存键哈希「system + user 全文」。
    # 只哈希 system 是不够的 —— 候选对清单在 user 里，改了清单就必须重算，
    # 否则会拿旧答案糊弄新问题。
    key = None
    if use_cache and cache_dir:
        import hashlib
        fp = hashlib.md5(
            (NORMALIZE_SYSTEM_PROMPT + "\x00" + user_prompt).encode("utf-8")
        ).hexdigest()[:12]
        key = f"normalize::{fp}"

    if key:
        from cache_utils import load_cache
        cached = load_cache(cache_dir, key)
        if isinstance(cached, dict):
            print("           （命中缓存，未重复调用 API）")
            return postprocess_map(cached.get("mapping", {}), stats), \
                cached.get("raw", "(缓存)")

    raw = call_ds_api(
        system_prompt=NORMALIZE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        temperature=0.0,
        max_tokens=2000,
    )

    llm_map = parse_name_map(raw)

    if key:
        from cache_utils import save_cache
        # 缓存只存模型原始输出；代码规则每次现算，
        # 这样改了 LOCAL_PART_SUFFIXES 之类的规则不必清缓存也能生效。
        save_cache(cache_dir, key, {"mapping": llm_map, "raw": raw})

    # 丢掉幻觉：原名必须是清单里真实存在的
    dropped = {k: v for k, v in llm_map.items() if k not in valid_names}
    if dropped:
        print(f"  [注意] 模型输出里有 {len(dropped)} 条原名不在清单中，已忽略：{dropped}")

    return postprocess_map(llm_map, stats), raw


# ------------------------------------------------------- 阶段 1：确定性分组

def resolve_name(name: str, mapping: dict) -> str:
    """沿映射链走到终点。

    映射可能有多层：环山车道 赛道终点线前 → 环山车道 赛道 → 环山车道。
    只查一次会停在中间层，输出一个没人认识的怪名字。
    """
    seen = set()
    while name in mapping and name not in seen:
        seen.add(name)
        name = mapping[name]
    return name


def group_scenes(blocks: list, name_map: dict = None, group_by: str = "env") -> list:
    """把场次整理成场景行。

    group_by="env"（默认口径）
        键 = 归一后场景名 + 内外。
        同一处空间的内景（各集、各时段）并成一行；外景另成一行。
    group_by="episode"
        键 = 归一后场景名 + 集号。旧口径：同名不同集一律不并。
    """
    name_map = name_map or {}
    buckets = {}
    order = []

    for i, b in enumerate(blocks):
        canon = resolve_name(b["location"], name_map)
        if group_by == "episode":
            key, display = (canon, b["episode"]), canon
        else:
            ek = env_kind(b["time_env"])
            key, display = (canon, ek), scene_display_name(canon, ek)

        if key not in buckets:
            buckets[key] = {"display": display, "codes": [], "first": i}
            order.append(key)
        buckets[key]["codes"].append(block_code(b))

    rows = []
    for key in order:
        item = buckets[key]
        rows.append([item["display"]] + item["codes"])
    return rows


def validate_rows(rows: list, blocks: list) -> tuple:
    """校验编号完整性。返回 (ok, [问题描述...])。"""
    expected = [block_code(b) for b in blocks]
    got = [c for row in rows for c in row[1:]]
    issues = []

    missing = [c for c in expected if c not in got]
    if missing:
        issues.append(f"丢失场次编号 {len(missing)} 个：{missing}")
    dup = [c for c in set(got) if got.count(c) > 1]
    if dup:
        issues.append(f"场次编号重复 {len(dup)} 个：{dup}")
    extra = [c for c in got if c not in expected]
    if extra:
        issues.append(f"出现不存在的编号 {len(extra)} 个：{extra}")

    return (not issues), issues


def build_merge_report(rows: list, blocks: list, name_map: dict,
                       raw_response: str = "", group_by: str = "env") -> str:
    """生成合并质检报告（纯文本，便于直接读）。"""
    loc_of = {block_code(b): b["location"] for b in blocks}
    ep_of = {block_code(b): b["episode"] for b in blocks}

    multi = [r for r in rows if len(r) > 2]

    lines = []
    lines.append("=" * 72)
    lines.append("场景合并质检报告")
    lines.append("=" * 72)
    lines.append("")
    lines.append(f"输入场次块：{len(blocks)} 个")
    lines.append(f"输出场景行：{len(rows)} 行")
    if group_by == "episode":
        lines.append("合并口径：场景名 + 集号（同名不同集一律不并）")
    else:
        lines.append("合并口径：场景名 + 内外")
        lines.append("          同一处空间的内景，跨集、跨时段（日 / 夜 / 晨 / 傍晚）并成一行；")
        lines.append("          内景与外景分成两行。")
    lines.append("")
    lines.append(f"  只含 1 个场次的行：{len(rows) - len(multi)} 行")
    lines.append(f"  并入了多个场次的行：{len(multi)} 行")
    lines.append(f"  场次合并后比逐场成行省掉：{len(blocks) - len(rows)} 行")
    lines.append("")

    lines.append("-" * 72)
    lines.append("一、合并明细（逐行核对：这些场次是否真的同属一处空间？）")
    lines.append("-" * 72)
    if not multi:
        lines.append("  （没有任何场次被并到一起）")
    for r in multi:
        display, codes = r[0], r[1:]
        canon = resolve_name(loc_of.get(codes[0], ""), name_map)
        uniq_eps = []
        for c in codes:
            e = ep_of.get(c, "?")
            if e not in uniq_eps:
                uniq_eps.append(e)
        lines.append("")
        lines.append(f"  ▸ {display}   {len(codes)} 个场次｜第 {','.join(uniq_eps)} 集")
        for c in codes:
            src = loc_of.get(c, "?")
            mark = "" if resolve_name(src, name_map) == canon else f"   ← 原名：{src}"
            lines.append(f"      {c}{mark}")
    lines.append("")

    lines.append("-" * 72)
    lines.append("二、改名映射（右侧是最终归一到的场景名，多层映射已展开）")
    lines.append("-" * 72)
    if not name_map:
        lines.append("  （没有任何场景名需要归一）")
    else:
        for old in name_map:
            lines.append(f"  {old}   →   {resolve_name(old, name_map)}")
    lines.append("")

    lines.append("-" * 72)
    lines.append("三、被规则拦下的归并（这些是**故意没并**的，不是漏并）")
    lines.append("-" * 72)
    lines.append("  判别标准：镜头推过去，是不是还在同一片场地里。")
    lines.append("  只剩「位置」的才并（赛道 / 看台 / 角落…）；换一个房间的一律不并。")
    lines.append("")
    all_names = [n for n, _ in collect_name_stats(blocks)]
    canon_names = sorted({v for v in name_map.values() if v})

    blocked = collect_blocked_pairs(all_names, canon_names)
    if not blocked:
        lines.append("  （无）")
    for long, short, why in blocked:
        lines.append(f"  ✗ {long}")
        lines.append(f"      不并进「{short}」：{why}")
    lines.append("")

    typo = fix_typo_in_base(all_names, set(all_names), canon_names)
    if typo:
        lines.append("  另：下面这些是错别字。**只改了名字，场景仍然各自一行**：")
        for old, new in typo.items():
            lines.append(f"      {old}   →   {new}")
        lines.append("")

    lines.append("-" * 72)
    lines.append("四、漏项自检（归一后仍互为包含关系 —— 逐对确认是不是上面那条规则拦下的）")
    lines.append("-" * 72)
    stats = collect_name_stats(blocks)
    names = [n for n, _ in stats]
    after = {n: resolve_name(n, name_map) for n in names}
    left = []
    for a, b in itertools.combinations(names, 2):
        if after[a] == after[b]:
            continue
        na, nb = re.sub(r"[\s·•\-—]", "", a), re.sub(r"[\s·•\-—]", "", b)
        if na in nb or nb in na:
            left.append((a, b, after[a], after[b]))
    if not left:
        lines.append("  （无 —— 所有含包含关系的名字都已被明确区分或统一）")
    for a, b, ca, cb in left:
        lines.append(f"  {a}  /  {b}")
        lines.append(f"      归一后分别是：{ca} ｜ {cb}")
    lines.append("")

    lines.append("-" * 72)
    if group_by == "episode":
        lines.append("五、原始剧本里的同名场景（同名不同集 —— 本口径下各自独立成行）")
    else:
        lines.append("五、同一个场景名被拆成多行的情况（内外不同 —— 这是应该的）")
    lines.append("-" * 72)
    by_canon = {}
    for r in rows:
        canon = resolve_name(loc_of.get(r[1], ""), name_map)
        by_canon.setdefault(canon, []).append(r)
    split = {k: v for k, v in by_canon.items() if len(v) > 1}
    if not split:
        lines.append("  （无 —— 每个场景名都只对应一行）")
    for canon, rs in split.items():
        lines.append(f"  {canon}")
        for r in rs:
            lines.append(f"      {r[0]}   {len(r) - 1} 个场次")
    lines.append("")

    lines.append("-" * 72)
    lines.append("六、全部输出行")
    lines.append("-" * 72)
    for i, r in enumerate(rows, 1):
        lines.append(f"  {i:3d}. {r[0]:<26} {'  '.join(r[1:])}")
    lines.append("")

    return "\n".join(lines)


# ------------------------------------------------------------------- 入口

def run_merge(blocks: list, start_ep: int, end_ep: int,
              cache_dir: str = None, use_cache: bool = True,
              skip_llm: bool = False, group_by: str = "env") -> dict:
    """把区间内的场次整理成场景行，并返回完整过程详情。

    两阶段：代码分组（保证编号完整） + 模型判名（只解决异名归一）。
    skip_llm=True 时只做阶段 1（零 API）。
    group_by="env" 按「场景名 + 内外」分组（默认）；"episode" 按「场景名 + 集号」。
    """
    filtered = [b for b in blocks if start_ep <= int(b["episode"]) <= end_ep]
    if not filtered:
        return {"rows": [], "filtered": [], "name_map": {}, "raw": "",
                "base_rows": [], "ok": True, "issues": []}

    key_desc = "场景名 + 集号" if group_by == "episode" else "场景名 + 内外"
    print(f"  阶段 1/2：按「{key_desc}」确定性分组（{len(filtered)} 个场次）...")
    base_rows = group_scenes(filtered, group_by=group_by)
    print(f"           不归一的话就是 {len(base_rows)} 行")

    name_map, raw = {}, "(已跳过)"
    if skip_llm:
        print("  阶段 2/2：已跳过模型判名（--no-merge，零 API）")
    else:
        print("  阶段 2/2：请模型判断异名归一（1 次 API 调用）...")
        name_map, raw = normalize_scene_names_with_llm(
            filtered, cache_dir=cache_dir, use_cache=use_cache
        )
        if name_map:
            print(f"           模型判定 {len(name_map)} 个名字需要归一：")
            for old in name_map:
                final = resolve_name(old, name_map)
                mid = "" if final == name_map[old] else f"（经「{name_map[old]}」）"
                print(f"             {old}  →  {final}{mid}")
        else:
            print("           模型判定：无需归一")

    rows = group_scenes(filtered, name_map, group_by)

    ok, issues = validate_rows(rows, filtered)
    if not ok:
        print("  [错误] 合并结果未通过完整性校验：")
        for it in issues:
            print(f"         - {it}")
        print("  [已回退] 改用不归一的确定性分组结果（宁可名字不统一，也不丢场次）")
        rows = base_rows
        name_map = {}
        ok = True
        issues = []

    print(f"  结果：{len(filtered)} 个场次 → {len(rows)} 行")
    return {
        "rows": rows, "filtered": filtered, "name_map": name_map, "raw": raw,
        "base_rows": base_rows, "ok": ok, "issues": issues,
    }
