# -*- coding: utf-8 -*-
"""换装节点规划（v1.1）——「角色 × 服装 × 场次」里**纯代码能算**的那部分。

用户流程的第③~④段（等批准 → 选定人脸 → 换装）落到这里。

【职责边界 —— 一条实测结论定死的】
  · 「场次坐标」= 代码算（剧本块自带 episode/scene_no/location，零幻觉）
  · 「服装归属」= 代码归（确定性规则，见下方口径）
  · 「服装 / 发型设计」= **模型做**，不是抽取 ——
    实测：《示例剧》全本 124 场戏，△ 描写行里只有 15 行涉及服装，
    主角贺景洲 **0 处**。⇒ 主要角色「这场穿什么」剧本根本没写，
    只能按身份 + 场合**设计**。脚本不编造服装，只把「这一场该怎么穿」摆出来。

【八条用户口径（2026-09-26 / 09-27 实锤）】
  ① 只统计**本人到场（实体出场）**的场次。电话出声、仅被提及的场次看不到人，
     更谈不上换装 ⇒ 一律不记。
  ② 场合分类必须按**着装语境**命名，不能按**地点行业**命名。
     反例：贺景洲在医院是「探视/陪护」（穿他自己的日常装），
     若归类叫「医疗」，模型会往白大褂/手术服上想 —— 他不是医生。
  ③ **剧情连贯的场次用同一套服装**。实测：《示例剧》相邻场次间隔 0~2 集占 95%+，
     说明"跨集"根本不等于"换装"—— 只有间隔大的地方才是剧情断点。
     ⇒ 归并的基本单元是「连贯段」：相邻场次间隔 ≥ `--gap-break`（默认 3）集、
       且不是同一场地，才断开。
  ④ **日常私服可以多次出现，但不能整部剧都是**。日常/职场是可复用池：
     一套可跨多场反复出现；但剧情断点处必换新套，碎段并入邻居（`--min-scenes`）。
  ⑤ **特定场合必须专属**（「特定的服装比如宴会就有做礼服这样」）。
     宴会/晚宴、典礼/发布会、展/拍卖、赛事/运动、邮轮/度假 各自成套，互不复用。
  ⑥ ★★ **先看剧情，再看地点**（09-27：「为什么都判定为日常，明明还有很多特殊
     场景的服装，什么病号服啊啥的，你要根据剧情来分」）。
     ⇒ 判据链是三层，**剧情信号优先级最高**：
        剧情信号（角色此刻的处境）> 地点场合 > 日常兜底。
     已落地的剧情信号：
       · **住院/病号服** —— 病房里「躺在病床上的是谁」决定穿什么。
         容晏 28~37 集在安圣医院（被推进手术室、术后醒来、观察三天）⇒ 病号服；
         贺景洲在 1-2/2-1 病房是**探视者**（病床上躺着的是苏晚意/林轻柔）⇒ 日常装。
         **这条是最容易做错的地方：地点一样，身份不同，衣服完全不同。**
       · **手术/无菌服** —— 剧本明写「换上无菌服」的人才有（苏晚意 29-2 是主刀）。
       · **睡衣/家居** —— 剧本明写睡袍/睡裙/醒来/睡梦中的人才有。
     ⇒ 只有**剧情没给特定处境**时，才落回「日常」。
  ⑦ ★ **日常 15 集换一套**（用户原话）。同一场合每天穿同一套是省钱的，
     但跨 50 集不可能只穿一套家居服 ⇒ `--max-span`（默认 15）兜底切。
  ⑧ **出发前场 / 同夜延续并入**：换装与换场次不总同帧。
     容晏 22-1（庄园走廊，还穿着跟礼服同色的领带）其实是**出发去拍卖会**那一场，
     不该算日常；23-2（真丝睡袍）→ 24-1（同一夜）也是同一套。
      ⇒ 加了两个窄口径吸收：出发前场（同集 + 有换装词）、同夜延续（间隔 ≤1 集 + 夜）。

【三条 ★★ 口径（2026-09-27，从用户两张**人工划分表**反推 —— 这条最关键）】
  ⑨ ★★ **一个造型 = 一套；跨集复用，不要求场次连续。**
     用户人工表（苏晚意）铁证：「真丝睡裙」= 2-2、4-2、5-3、6-1、11-3、12-1、23-2、
     24-1、24-2、41-1、47-1 —— **横跨第 2~47 集、11 个场次，仍算同一套**。
     ⇒ v0.9 强制「间隔 <3 集且同场地才绑一起」是**方向性错误**：它按时间窗口把
       同一条睡裙切成 4 套。正确做法是**认出"这是同一件衣服"后全局归并**。
     ⇒ 口径修正：「剧情连贯 ⇒ 同一套」只是**充分条件**，不是必要条件。
  ⑩ **活动型专属 = 一次活动 = 一套**（按**场地群**归并，不按时间窗）。
     人工表：邮轮顶层宴会大厅 ↔ 邮轮顶层展厅 是一次行程 ⇒ 一套「游轮晚宴」；
     贺氏集团发布会后台 ↔ 大厅 ⇒ 一套。v0.9 把它们按场合类别拆成了两套。
  ⑪ **本角色首次出场那一场 = 定妆场（独立一套）**。
     两张人工表的第 1 列都是「定妆服 = 1-1」—— 人设首次亮相产定妆照，
     它是流程第③步的产物，不能混进日常装。
  ⑫ **同剧情形 = 一套**（病号服 / 无菌服 / 睡衣 各自全局一套，跨集不切）。

【输出】
  ① 服装套总表（一套 = 一次出图）
  ② 逐套明细（判定依据 + 场次 + 地点 + 剧本线索）
  ③ 到场场次清单（含所属套号与判定依据，供核对）

用法：
    python tools/costume_plan.py --name 容晏
    python tools/costume_plan.py --name 容晏 --max-span 15 --gap-break 3
    python tools/costume_plan.py --name 容晏 --stages "3:试探期,14:同住期,43:婚后"
"""
import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "src"
sys.path.insert(0, str(SRC))

from file_reader import read_script, find_input_file  # noqa: E402
from scene_extractor import split_into_blocks  # noqa: E402
from character_prompt_builder import (  # noqa: E402
    resolve_aliases, _block_cast, _name_hits, _speaker_hits, _character_evidence,
    is_named_character,
)

# ---------------------------------------------------------------------------
# 服装词表：只认「服装名词 / 材质」，颜色单独命中不算（实测坑：
# 「黑色界面亮起」「黑色旅行袋」都会被颜色词捞上来，全是噪音）。
# ---------------------------------------------------------------------------
COSTUME_WORDS = [
    "睡裙", "睡衣", "浴袍", "晨袍", "长裙", "短裙", "连衣裙", "礼裙", "礼服", "晚礼服",
    "婚纱", "旗袍", "西装", "西服", "衬衫", "衬衣", "T恤", "吊带", "背心", "毛衣",
    "针织", "卫衣", "大衣", "风衣", "外套", "夹克", "皮衣", "羽绒", "马甲", "西装裤",
    "西裤", "长裤", "短裤", "阔腿裤", "牛仔裤", "制服", "军装", "白大褂", "无菌服",
    "手术服", "病号服", "睡袍", "运动服", "球衣", "泳衣", "内衣", "夜行衣", "裙子",
    "高跟鞋", "皮鞋", "靴", "运动鞋", "拖鞋", "凉鞋", "领带", "领结", "袖扣", "胸针",
    "腕表", "手表", "丝巾", "围巾", "帽子", "手套", "墨镜", "项链", "耳环", "披肩",
    "斗篷", "套装", "裙装", "长袍",
]
MATERIAL_WORDS = [
    "真丝", "丝绸", "丝绒", "天鹅绒", "羊绒", "羊毛", "亚麻", "皮革", "蕾丝", "缎面",
    "雪纺", "棉麻", "粗花呢", "亮片", "薄纱", "网纱", "灯芯绒",
]
STRONG_WORDS = COSTUME_WORDS + MATERIAL_WORDS

ROSTER_LINE_RE = re.compile(r"^(?:出场人物|CHARACTERS)\s*[：:]")

# 场地归并阈值：公共前缀 ≥ 4 字、且 ≥ 较短串的 40% ⇒ 视为同一场地的子位置
PREFIX_MIN = 4
PREFIX_RATIO = 0.4

# 「同一次活动」的跨度上限（集）—— 活动型造型跨场地桥接用（见 `_venue_clusters`）。
# 实测：容晏 57-1「车库后门」→ 57-2/59-2「环山车道大屏幕前」是同一场赛车（跨度 3 集）
# ⇒ 并成一套；苏晚意「马术俱乐部」48~53 ＋「环山车道」53~59 合起来 12 集，
# 是**两场不同赛事**（人工表：马术服 / 赛车服）⇒ 必须挡掉。
ACTIVITY_SPAN = 4

# 同场地**最长容忍间隔**（集）—— 复用池分段用（见 plan_nodes ④）。
# 同场地（容家庄园客厅）隔几集还算同一次连续生活，隔十几集就不算了。
# 实测坑：容晏 28-2 与 43-2 同为「容家庄园客厅」，靠 same_venue 一直不断，
# 段被拉到 14~43 集（30 集）⇒ 触发等分切 ⇒ 切出「28-2 ＋ 43-2」这种诡异的一套。
VENUE_MAX_GAP = 6

# ---------------------------------------------------------------------------
# 着装语境 —— 决定「怎么穿」的变量不只是地点，但地点仍是主要的场合线索。
#
# 三条实测教训（都写进了规则表，别再踩）：
#  ① 按地点切不行：主角 103 个场次按地点切出 64 段（客厅→卧室→走廊都算切换）。
#  ② 按地点名的**行业**命名不行：「医院」⇒「医疗」⇒ 模型想到白大褂。
#     ⇒ 类别名必须回答「这场该怎么穿」，不是「这地方是干什么的」。
#  ③ 兜底类不能单列：在途 / 公路 / 电梯穿的就是当日外出那套，已并入「日常/便装」。
#
# 顺序敏感：专门的场合排前面，兜底的排后面。
# ---------------------------------------------------------------------------
OCCASION_RULES = [
    # ★ 医院/病房必须先判定 —— 但**具体穿什么由剧情信号决定**（见口径 ⑥）：
    #   患者 ⇒ 病号服；探视者 ⇒ 日常装。这里的兜底说明只是「没查到剧情信号」时的退路。
    ("日常/便装",
     ["医院", "病房", "诊室", "药房", "手术"],
     "日常便装 —— 含外出、通勤、在途、探视/就医（在途与探视穿的就是当日外出那套，"
     "不另置装）。**医院/病房一律先按剧情判定身份（患者 / 探视者），"
     "探视者穿自己的日常装；严禁白大褂、护士装**"),
    ("典礼/发布会",
     ["发布会", "典礼", "颁奖", "签约", "庆功", "剪彩", "记者会", "开幕", "奠基", "表彰"],
     "正式登台装 —— 三件套 / 正装级，剪裁挺括"),
    ("宴会/晚宴",
     ["宴会", "晚宴", "酒会", "舞会", "招待会", "婚礼", "婚宴", "答谢", "庆祝酒"],
     "晚宴礼服级 —— 礼服 / 丝绒 / 缎面，与白昼正装明显区别"),
    # ★ 会展中心 = 拍卖会/展会场地（实测坑：`会展中心二楼包厢` 曾被「包厢」
    #   捞进「日常/便装」，把容晏的慈善拍卖会整场算成了日常装）。
    ("展/拍卖",
     ["展厅", "画展", "会展", "拍卖", "展览", "走秀", "秀场", "沙龙", "艺廊"],
     "艺术/拍卖社交装 —— 拍卖会、画展、沙龙一类场合：男宾深色西装至礼服级，"
     "女宾礼服或精致长裙；切忌休闲装"),
    ("赛事/运动",
     ["马术", "马场", "赛道", "赛场", "球场", "健身房", "泳池", "俱乐部", "跑道", "球馆",
      "环山", "看台", "大屏幕", "发车", "围场", "车库"],
     "赛场装 —— **参赛者**穿专业马术/赛车/运动服；**到场观赛者**穿深色外套或"
     "精致休闲，**严禁运动服**（照角色在剧情里的身份选）"),
    ("职场",
     ["公司", "办公", "会议室", "总裁", "董事长", "前台", "工位", "集团大楼", "写字楼", "商谈"],
     "商务正装 —— 日常职场默认装束"),
    ("日常/便装",
     ["咖啡厅", "餐厅", "包厢", "商场", "购物中心", "超市", "零食区", "酒吧", "广场",
      "公园", "游乐园", "街", "车内", "车里", "车上", "公路", "车道", "路边", "山路",
      "机舱", "电梯", "面包车", "车外"],
     "（说明取本类的首条规则）"),
    # ★ 邮轮/度假必须先于「居家」。实测坑：`邮轮顶层走廊` 被「走廊」捞成
    #   「居家」，再靠「同场地」把容家庄园的居家组和邮轮上的宴会组连成一大块。
    ("邮轮/度假",
     ["邮轮", "游轮", "船舱", "度假村", "酒店", "旅馆", "套房", "甲板"],
     "旅途装 —— 邮轮/度假村等行程场景，按当次活动着装"
     "（船上晚宴按宴会级、逛展或甲板按精致休闲）"),
    ("居家",
     ["客厅", "卧室", "客房", "房间", "书房", "厨房", "阳台", "走廊", "楼梯", "门口",
      "庄园", "别墅", "公寓", "家中", "家里", "宅"],
     "家居服 / 私服 —— 松弛、去正式化"),
]
# 同一语境名可能出现在多条规则里（如「日常/便装」既有医院优先条、又有常规条）
# ⇒ 用 setdefault 只认**首条**的说明，保证报告里那句约束是完整版。
OCCASION_NOTE = {}
for _k, _kws, _note in OCCASION_RULES:
    OCCASION_NOTE.setdefault(_k, _note)
OCCASION_NOTE["其他/室外"] = "剧本未给出明确场合 —— 按前后剧情与角色身份判断"

# ---------------------------------------------------------------------------
# 着装**大类**：相邻语境合并。
# 「居家」与「日常/便装」本就是同一套 —— 在家穿的日常装、出门穿的日常装，
# 是同一天里的一件衣服。拆成两组等于把同一套切成两个节点。
# 实测：容晏 4-1（居家·二楼走廊）与 4-3（日常·小楼餐厅）是同一天前后。
# ---------------------------------------------------------------------------
MAJOR_OF = {
    "居家": "日常",
    "日常/便装": "日常",
}
OCCASION_NOTE["日常"] = (
    "日常装 —— 含居家、外出、通勤、在途、探视/就医（同日外出与在家穿的就是同一套，"
    "不另置装）。**医院/病房一律先按剧情判定身份：患者穿病号服、探视者穿日常装；"
    "严禁白大褂、护士装**"
)

# ---------------------------------------------------------------------------
# 剧情信号 —— 优先级高于地点（口径 ⑥）。
# 只有**剧本里写了物证**或**角色身份能判死**的，才升级成专属服装；否则不猜。
# ---------------------------------------------------------------------------
PLOT_NOTE = {
    "住院/病号服":
        "住院病号服 —— 蓝白条纹或素色住院服（病号服）；术后恢复期可用宽松家居套装。"
        "**严禁西装、礼服、大衣、正装外套等外出装**",
    "手术/无菌服":
        "无菌手术装 —— 无菌手术衣（刷手服）+ 手术帽 + 口罩 + 护目镜。"
        "**严禁日常装、正装**",
    "睡衣/家居":
        "睡衣 —— 真丝睡袍 / 家居睡衣，松弛去正式化。**严禁外出装、正装**",
}
PLOT_KIND = "plot"
MAKEUP_KIND = "makeup"
MAKEUP_LABEL = "定妆服"

# 电话信号 —— 只认「通话进行中」，不认「挂断/放下/电话」这类通用词（见 phone_only 注释）
PHONE_SIGNAL_RE = re.compile(
    r"接听|接通|拨通|打来|来电|铃声|电话那头|电话另一端|电话里传来|免提|视频通话|通话中"
)

OUTING_MAJORS = {"典礼/发布会", "宴会/晚宴", "展/拍卖", "赛事/运动", "邮轮/度假"}
REUSE_MAJORS = {"日常", "职场"}
EXCLUSIVE_MAJORS = set(OUTING_MAJORS)

BOX_LABEL = {
    "日常": "日常装",
    "职场": "职场装",
    "典礼/发布会": "典礼/发布会装",
    "宴会/晚宴": "宴会/晚宴装",
    "展/拍卖": "展/拍卖装",
    "赛事/运动": "赛场装",
    "邮轮/度假": "邮轮/度假装",
    "住院/病号服": "住院病号服",
    "手术/无菌服": "无菌手术服",
    "睡衣/家居": "睡衣/家居服",
    MAKEUP_LABEL: MAKEUP_LABEL,
}

PLOT_NOTE[MAKEUP_LABEL] = (
    "定妆服 —— 人设首次亮相当天的装束，用于产定妆照；"
    "**它只服务第①~③步（定妆 / 选脸），不是剧情里的换装**。"
    "后续所有换装造型都以这一张的脸为基准（图生图保脸）"
)

# 医疗场景地点
MEDICAL_LOC_KWS = ["医院", "病房", "诊室", "药房", "手术区", "手术室"]

# 换装词（判断「出发前场」用）
DRESS_WORDS = ("换上", "换下", "穿上", "穿了", "衣帽间", "礼服", "长裙", "西装", "领带")
# 就寝时段：当晚 → 次日晨。睡衣状态会跨过午夜延续到第二天早上，
# 所以「晨」要算进来（实测苏晚意 11-3 深夜睡裙 → 12-1 晨内同一间卧室，是同一套）。
# ⚠️ 别写成字符类 `[夜午夜凌晨]` —— 那等于 {夜,午,凌,晨} 四个单字，语义混乱。
SLEEP_PERIOD_RE = re.compile(r"夜|凌晨|清晨|黎明|晨")


def major_of(occasion: str) -> str:
    return MAJOR_OF.get(occasion, occasion)


def occasion_of(location: str) -> str:
    """把地点名压成一个「着装语境」。命中不到任何规则的归入「其他/室外」。"""
    loc = location or ""
    for kind, kws, _note in OCCASION_RULES:
        if any(k in loc for k in kws):
            return kind
    return "其他/室外"


def is_medical(location: str) -> bool:
    loc = location or ""
    return any(k in loc for k in MEDICAL_LOC_KWS)


def costume_hits(line: str) -> list:
    return [w for w in STRONG_WORDS if w in line]


def is_costume_line(line: str) -> bool:
    return bool(costume_hits(line))


DLG_LINE_RE = re.compile(r"^[^：:]{1,20}[：:]")
# 「旁白 / 画外 / VO / 电话」标记的对白 = 人不在画面里（声画分离）。
# ⚠️ 实测坑：林轻柔 28-1 只有一句「林轻柔（旁白）：原来是攀上了陆二少……」，
#    名单里也没有她 —— 靠这条才不会被算成到场。
VOICE_TAG_RE = re.compile(r"旁白|画外|心声|独白|\bV\.?O\b|voice", re.I)


def _is_voice_line(ln: str, name_re) -> bool:
    """这一行是不是「本角色的声画分离台词」（旁白/画外/电话）。"""
    m = DLG_LINE_RE.match(ln.strip())
    if not m or not name_re:
        return False
    head = m.group(0)
    return bool(name_re.search(head)) and bool(VOICE_TAG_RE.search(head))


def _body_no_roster(block: dict) -> list:
    """去掉「出场人物 / CHARACTERS」名单行的正文。"""
    return [ln for ln in block.get("body_lines", [])
            if not ROSTER_LINE_RE.match(ln.strip())]


def classify(block: dict, aliases: list) -> str:
    """出场形式：实体 / 出声 / 提及 —— 修正版（2026-09-27，对照林轻柔人工表时抓出的）。

    实测两处错判，都在这一个函数里（原版只看「出场人物名单」）：
      · **名单里有、但正文里完全没有这个人** ⇒ 不是到场。
        林轻柔 5-1：名单写了 6 个人，正文只有接头人与苏晚意，
        「林轻柔」只出现在名单行本身 ⇒ 剧本作者把整集角色抄进了名单。
        （用户口径①「看不到人就不算」，这正是那条规则的技术落点。）
      · **名单里没有、但 △ 行写了他的动作/神态** ⇒ 是到场。
        林轻柔 39-1：名单漏了她，正文却有「△林轻柔脸色青白交错，一个字也说不出来」
        ⇒ 人在画面里，只是名单漏写。
    """
    name_re = _alias_re(aliases)
    cast = _block_cast(block)
    in_roster = any(_name_hits(c, aliases) for c in cast)
    body = _body_no_roster(block)
    hits = [ln for ln in body if name_re and name_re.search(ln)] if name_re else []
    live = [ln for ln in hits if not _is_voice_line(ln, name_re)]
    if in_roster:
        return "实体" if live else "提及"     # 名单有、正文无 ⇒ 名单多写了
    if name_re:
        for ln in live:
            s = ln.strip()
            # 名单外要判到场，判据必须**很硬**：本角色的名字出现在动作行主语位
            # （`△林轻柔脸色青白交错`），或就是本角色的对白行（`林轻柔：…`）。
            # ⚠️ 不能用宽松的「名字出现在任意行」—— 别人对白里提一句她的名字
            #    （`苏晚意：林轻柔，你…`）不算她到场（实测会虚增十几个场次）。
            if s.startswith("△") and name_re.match(s[1:].lstrip()):
                return "实体"
            m = DLG_LINE_RE.match(s)
            if m and name_re.search(m.group(0)):
                return "实体"
    if _speaker_hits(block, aliases):
        return "出声"
    return "提及"


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return 10 ** 6


def ep_key(block: dict) -> tuple:
    return (_int(block.get("episode")), _int(block.get("scene_no")))


def code_of(block: dict) -> str:
    return f"{block['episode']}-{block['scene_no']}"


def collect_evidence(block: dict) -> tuple:
    """返回 (关键描写行, 剧本原有服装线索行)。

    关键描写 = △ 动作行（去掉名单/译文），取前 3 条 —— 用来判断这是什么场合。
    """
    keys, clothes = [], []
    for ln in _character_evidence(block):
        if ROSTER_LINE_RE.match(ln):
            continue
        if is_costume_line(ln):
            clothes.append(ln)
        if ln.startswith("△"):
            keys.append(ln)
    return keys[:3], clothes


def build_rows(blocks: list, aliases: list) -> list:
    rows = []
    for b in blocks:
        form = classify(b, aliases)
        if form == "提及":
            body = b.get("body_text", "")
            if not any(len(a) >= 2 and a in body for a in aliases):
                continue
        keys, clothes = collect_evidence(b)
        # 只保留**提到本角色**的服装描写 —— 剧本里绝大多数服装描写都是别人的
        # （实测：13-1 只写了张启「身穿高定西装」，挂在贺景洲名下会误导）
        clothes = [ln for ln in clothes if any(a and a in ln for a in aliases)]
        rows.append({
            "block": b, "code": code_of(b), "form": form,
            "episode": b.get("episode", ""),
            "location": b.get("location", ""), "time_env": b.get("time_env", ""),
            "occasion": occasion_of(b.get("location", "")),
            "keys": keys, "clothes": clothes,
            "evidence": _character_evidence(b),
            "cast": _block_cast(b),
            "why": "",
        })
    rows.sort(key=lambda r: ep_key(r["block"]))
    # 按「集-场」去重 —— 剧本里同一个场次号偶尔出现两次（跨页重复、
    # 场次标题被抄了两遍），不去重会把同一场戏算成两场
    seen, uniq = set(), []
    for r in rows:
        if r["code"] in seen:
            continue
        seen.add(r["code"])
        uniq.append(r)
    return uniq


def phone_only(rows: list, aliases: list) -> list:
    """从「实体」里剔掉**只在电话里出声、人不在场**的场次。

    实测坑：10-1 的「出场人物」名单把贺景洲写了进去，但他全程只在电话里说话
    （「△苏晚意关上门，接听电话」）—— 名单是剧本作者手写的，会写错。

    判据（三条全中才算，宁可漏不可错杀）：
      · 本场有**通话中**的电话信号（见 PHONE_SIGNAL_RE）；
      · 该角色**没有任何 △ 行**提到他（人不在画面里）；
      · 他的对白**全部出现在电话信号之后**。
    反例（必须保住）：10-1 的容晏对白在「手机铃声响起」之前 —— 他是当面说完
    才走的，属于真人到场；43-2 的容晏有 △ 行（「电话那头，容晏翻文件的动作一顿」）。

    ⚠️ 信号词**不能收「挂断 / 放下 / 电话」这种通用词**（2026-09-27 实测）：
    26-2「△路秋挂断电话，就看见一个不该出现在这里的人」—— 挂电话的是**别人**，
    林轻柔是走进来当面说话的，却被旧词表误杀成一整个场次（她因此掉了 26-2）。
    """
    name_re = _alias_re(aliases)
    phone_re = PHONE_SIGNAL_RE
    dlg_re = DLG_LINE_RE
    out = []
    for r in rows:
        ev = r["evidence"]
        idx = next((i for i, ln in enumerate(ev) if phone_re.search(ln)), None)
        if idx is not None and name_re:
            has_delta = any(ln.startswith("△") and name_re.search(ln) for ln in ev)
            dlg = [i for i, ln in enumerate(ev)
                   if not ln.startswith("△") and dlg_re.match(ln) and name_re.search(ln)]
            if not has_delta and dlg and all(i > idx for i in dlg):
                r["form"] = "出声"
                continue
        out.append(r)
    return out


def visible_only(rows: list) -> list:
    """只留**本人实体出场**的场次（口径 ①：看不到人的场次不产生换装）。"""
    return [r for r in rows if r["form"] == "实体"]


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def same_venue(a: str, b: str) -> bool:
    """两处地点名是不是「同一个场地里的不同位置」。"""
    L = _common_prefix_len(a, b)
    if L < PREFIX_MIN:
        return False
    return L >= min(len(a), len(b)) * PREFIX_RATIO


# ---------------------------------------------------------------------------
# 连贯段（口径 ③）：剧情连贯的场次绑成一段
# ---------------------------------------------------------------------------
def split_runs(rows: list, gap_break: int = 3) -> list:
    """把到场场次切成「剧情连贯段」。

    断点条件（两者同时满足才断）：
      · 与上一场的**集数间隔 ≥ gap_break**（默认 3）—— 中间好几集没这人，
        多半剧情跳过了时间；
      · 且**不是同一场地**—— 同场地（邮轮顶层宴会大厅 → 邮轮顶层展厅）虽隔几集，
        仍是同一次行程，不断（否则会把一次邮轮拆成两套）。

    实测依据：容晏 41 场里 40 个间隔只有 6 处 ≥ 3，若"跨集就断"会切出 30+ 段。
    """
    out, cur, prev_ep, prev_loc = [], [], None, ""
    for r in rows:
        ep = _int(r["episode"])
        if cur and ep - prev_ep >= gap_break and not same_venue(prev_loc, r["location"]):
            out.append(cur)
            cur = []
        cur.append(r)
        prev_ep, prev_loc = ep, r["location"]
    if cur:
        out.append(cur)
    return out


def _is_break(prev_ep: int, ep: int, prev_loc: str, loc: str,
              gap_break: int) -> bool:
    """复用池（日常/职场）分段判据：这一套日常装到此为止了吗？

    断点条件：
      · 间隔 ≥ `VENUE_MAX_GAP`（默认 6）—— **同场地也照样断**。同场地隔十几集
        必然不是同一次连续生活（实测坑见 VENUE_MAX_GAP 注释）；
      · 否则：间隔 ≥ `gap_break`（默认 3）**且不是同一场地**。
    """
    if ep - prev_ep >= VENUE_MAX_GAP:
        return True
    return ep - prev_ep >= gap_break and not same_venue(prev_loc, loc)


def parse_stages(spec: str) -> list:
    """`3:试探期,14:同住期` → [(3, '试探期'), (14, '同住期')]，按集号升序。"""
    out = []
    if not spec:
        return out
    for part in spec.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            k, v = part.split(":", 1)
        elif "：" in part:
            k, v = part.split("：", 1)
        else:
            k, v = part, part
        k = k.strip()
        if k.isdigit():
            out.append((int(k), v.strip()))
    out.sort()
    return out


def stage_of(ep: int, stages: list) -> str:
    if not stages:
        return ""
    name = ""
    for start, nm in stages:
        if ep >= start:
            name = nm
        else:
            break
    return name


def split_by_stage(rows: list) -> list:
    """在连贯段内部再按已标注的 stage 切（用户/模型给定阶段时生效）。"""
    out, cur, last = [], [], None
    for r in rows:
        st = r.get("stage", "")
        if last is not None and st != last:
            out.append(cur)
            cur = []
        cur.append(r)
        last = st
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# 剧情信号探测（口径 ⑥）
# ---------------------------------------------------------------------------
def _alias_re(aliases: list):
    pats = [re.escape(a) for a in aliases if a and len(a) >= 2]
    return re.compile("|".join(pats)) if pats else None


# 患者判定：谁躺在病床上 / 谁被推着进手术室
#
# ⚠️ 别用「万能正则」去抓名字（`([\u4e00-\u9fa5]{2,4})躺在?病床上`）——
#    实测会把「苏晚意躺在病床上」抓成 `苏晚意躺`，还会把「生气」这种词捞进来。
#    正确做法：拿**该场实际在场的演员名**去匹配上下文模板。
OTHER_PATIENT_TPLS = [
    "{who}.{0,3}躺在?病床上",
    "{who}.{0,3}在病床上",
    "为{who}.{0,2}(?:掖|盖|拉)(?:着)?被",
    "病床上.{0,12}{who}",
]
# 我方患者证据：「推着容晏」「铅制大门…将容晏隔绝在内」
# ⚠️ 别把「麻醉」「观察」放进词表：实测 29-2「苏晚意：麻醉剂量？」被误判成
#    她自己是病人（她其实是主刀）。词表只留**只有病人才会有的处境**。
ME_PATIENT_PATS = [
    r"推着{me}",
    r"扶着{me}",
    r"{me}[^。]{0,12}(?:病床|病号服|术后|住院|躺在病床)",
    r"(?:病床|病号服|手术区|手术室|铅制大门|铅门)[^。]{0,12}{me}",
]
SLEEP_WORDS = ("睡袍", "睡衣", "睡裙", "浴袍", "晨袍")
WAKE_WORDS = ("醒来", "睡梦中", "一躺下", "躺进被")
STERILE_WORDS = ("无菌服", "手术衣", "刷手服", "无菌手术")


def _chunk_lines(text: str, words) -> list:
    return [w for w in words if w in text]


def _worn_by_other(line: str, words, aliases: list, cast: set) -> bool:
    """物证词前面 12 字内出现**别的角色名** ⇒ 这件衣服不是本角色穿的。"""
    for w in words:
        i = line.find(w)
        while i >= 0:
            head = line[max(0, i - 12):i]
            for other in cast:
                if len(other) >= 2 and other in head and not _name_hits(other, aliases):
                    return True
            i = line.find(w, i + 1)
    return False


def _medical_blocks(run: list) -> list:
    """把一段里的**医疗场次**按「连续相邻」切成块（中间夹了非医疗场次就断开）。

    为什么要按块而不是按段传播：容晏的 28-3~37-1 是连续的住院戏，患者身份
    可以从 28-3（「推着容晏」）传给后面几场；但苏晚意的 25-1 病房和 28-3
    手术区之间夹着 5 场别的戏，不是同一次住院，不能互传。
    """
    blocks, cur = [], []
    for r in run:
        if is_medical(r["location"]):
            cur.append(r)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    return blocks


def _line_scope(r: dict) -> list:
    """判「谁是病人」时只看**△ 行与对白行**，把场次头行排除掉。

    ⚠️ 实测坑：场次头行形如「30-1 手术室门外走廊 日 内」，本身带「手术室」
    这种词。把整场文本拼成一段再匹配，`手术室[^。]{0,12}苏晚意` 会跨行命中，
    把苏晚意（那一场她是**主刀**）误判成患者。改成逐行匹配即解。
    """
    return [ln for ln in r["evidence"]
            if ln.startswith("△") or "：" in ln or ":" in ln]


def detect_plot(rows: list, runs: list, aliases: list) -> None:
    """给每一行打上剧情信号（r['plot'] / r['why']）。没有信号就留空。"""
    me_re = _alias_re(aliases)
    me_re_pats = [re.compile(p.replace("{me}", f"(?:{me_re.pattern})"))
                  for p in ME_PATIENT_PATS] if me_re else []

    def _other_patients(r: dict) -> set:
        """本场「别人是病人」的证据：拿在场演员名代入模板逐行匹配。"""
        names = {c for c in r["cast"]
                 if len(c) >= 2 and not _name_hits(c, aliases)}
        hits = set()
        for ln in _line_scope(r):
            for who in names:
                # ⚠️ 用 str.replace 而不是 str.format —— 模板里有 `{0,3}`，
                #    format 会把它当占位符抛 KeyError: '0,3'（实测踩过）。
                for tpl in OTHER_PATIENT_TPLS:
                    if re.search(tpl.replace("{who}", re.escape(who)), ln):
                        hits.add(who)
                        break
        return hits

    # ---- ① 医疗：先判「谁是病人」，再决定穿病号服还是日常装 ----
    for run in runs:
        for block in _medical_blocks(run):
            decided = {}
            for r in block:
                lines = _line_scope(r)
                me_patient = bool(me_re) and any(p.search(ln)
                                                 for ln in lines for p in me_re_pats)
                others = _other_patients(r)
                if me_patient:
                    decided[r["code"]] = ("住院/病号服",
                                          "剧情·患者本人在院（被推入手术区/术后/住院）")
                elif others:
                    decided[r["code"]] = ("", "剧情·探视者（同场病床上躺着的是%s）"
                                              % "、".join(sorted(others)))
                else:
                    decided[r["code"]] = None      # 未决，靠本块其它场次兜
            # 未决场次：本块内已有「住院」结论就跟上
            # （实测容晏 30-2/31-1/34-1/34-3/37-1 靠这条判成住院）
            if any(v and v[0] == "住院/病号服" for v in decided.values()):
                for code, v in decided.items():
                    if v is None:
                        decided[code] = ("住院/病号服",
                                         "剧情·同段落住院（本块已判定患者=本人）")
            for r in block:
                v = decided.get(r["code"])
                if not v:
                    continue
                if v[0]:
                    r["plot"] = v[0]
                # 探视者那一路的结论也要写进依据（v[0] 为空但 v[1] 有效）——
                # 它正是「为什么这里不是病号服」的答案，写出来才好核对。
                r["why"] = v[1]

    # ---- ② 无菌手术服（剧本明写谁换上了才算） ----
    # 判据：本场出现无菌服类物证词 + 不是别人穿的（`_worn_by_other`）。
    # 实测：「她换上无菌服」（苏晚意 29-2）用代词，靠角色名锚不住；
    # 但无菌服只出现在手术场景，本角色在场且物证没有归属他人 ⇒ 就是本角色。
    for r in rows:
        if r.get("plot"):
            continue
        for ln in r["evidence"]:
            if _chunk_lines(ln, STERILE_WORDS) and not _worn_by_other(
                    ln, STERILE_WORDS, aliases, r["cast"]):
                r["plot"] = "手术/无菌服"
                r["why"] = "剧情·剧本明写换上无菌服"
                break

    # ---- ③ 睡衣 / 就寝 ----
    # 物证词（睡袍/睡裙…）场景级即可 + `_worn_by_other` 排除「别人穿的那件」
    # （实测 11-3「她穿着真丝睡裙走进他的卧室」，两个角色同场，靠排除法才不串）。
    # 但「醒来 / 睡梦中 / 一躺下」这类**动作词**必须角色名锚定 ——
    # 实测 16-2「她喝了半杯牛奶，几乎一躺下就立刻睡了过去」说的是苏晚意，
    # 不锚定就会把容晏也判成睡衣。
    for r in rows:
        if r.get("plot"):
            continue
        for ln in r["evidence"]:
            if _chunk_lines(ln, SLEEP_WORDS) and not _worn_by_other(
                    ln, SLEEP_WORDS, aliases, r["cast"]):
                r["plot"] = "睡衣/家居"
                # 依据文案要分清「物证属于本角色」还是「本角色在同场就寝」——
                # 后者（实测容晏 11-3：「她穿着真丝睡裙走进他的卧室」，穿的人其实是
                # 苏晚意）结论没错（他在自己卧室后半夜醒来 ⇒ 睡衣），但若写成
                # 「剧本明写睡衣/睡袍」会让人误以为那是给他写的衣物描写。
                if me_re and me_re.search(ln):
                    r["why"] = "剧情·剧本明写本角色睡衣/睡袍"
                else:
                    r["why"] = "剧情·同场就寝（剧本有睡衣物证，本角色在场就寝推定）"
                break
            if (_chunk_lines(ln, WAKE_WORDS)
                    and me_re and me_re.search(ln)
                    and not _worn_by_other(ln, WAKE_WORDS, aliases, r["cast"])):
                r["plot"] = "睡衣/家居"
                r["why"] = "剧情·剧本明写本角色就寝/醒来"
                break


# ---------------------------------------------------------------------------
# 归属与吸收（口径 ⑥⑦⑧）
# ---------------------------------------------------------------------------
def assign_costume(rows: list) -> None:
    for r in rows:
        if r.get("plot"):
            r["costume_major"] = r["plot"]
            r["costume_kind"] = PLOT_KIND
            continue
        m = major_of(r["occasion"])
        if m in EXCLUSIVE_MAJORS:
            r["costume_major"] = m
            r["costume_kind"] = "exclusive"
            r["why"] = r["why"] or f"地点·{r['location']}"
        else:
            r["costume_major"] = m if m in REUSE_MAJORS else "日常"
            r["costume_kind"] = "reuse"
            r["why"] = r["why"] or f"日常兜底（地点·{r['location'] or '未标'}）"


def mark_makeup(rows: list) -> None:
    """口径 ⑪：**本角色最早到场的那个场次 = 定妆场**（独立一套）。

    依据：两张人工表（苏晚意 / 林轻柔）第 1 列都是「定妆服 = 1-1」，
    而 1-1 也正是两人各自的首次出场场次 ⇒ 取「最早到场场次」比写死 1-1 更通用。
    流程上也自洽：第③步选人脸定妆，之后所有换装都拿它当基准（图生图保脸）。
    """
    if not rows:
        return
    first = min(rows, key=lambda r: ep_key(r["block"]))
    first["costume_major"] = MAKEUP_LABEL
    first["costume_kind"] = MAKEUP_KIND
    first["why"] = "定妆场（本角色首次出场）—— 产定妆照，后续换装以此脸为基准"


def absorb(runs: list) -> None:
    """口径 ⑧：出发前场 + 同夜延续并入（两个都很窄，避免误吸）。"""
    for run in runs:
        for i in range(1, len(run)):
            cur, prev = run[i], run[i - 1]
            if (cur["costume_kind"] == "exclusive"
                    and cur["costume_major"] in OUTING_MAJORS
                    and prev["costume_kind"] == "reuse"
                    and _int(prev["episode"]) == _int(cur["episode"])
                    and any(w in "".join(prev["evidence"]) for w in DRESS_WORDS)):
                prev["costume_major"] = cur["costume_major"]
                prev["costume_kind"] = "exclusive"
                prev["why"] = f"并入{cur['code']}（同集·出发换装）"
        for i in range(len(run) - 1):
            cur, nxt = run[i], run[i + 1]
            if (cur["costume_major"] == "睡衣/家居"
                    and nxt["costume_kind"] == "reuse"
                    and _int(nxt["episode"]) - _int(cur["episode"]) <= 1
                    and SLEEP_PERIOD_RE.search(nxt.get("time_env", ""))):
                nxt["costume_major"] = "睡衣/家居"
                nxt["costume_kind"] = PLOT_KIND
                nxt["why"] = f"并入{cur['code']}（同夜就寝延续）"
        # 夹心吸收：同一场活动中间插进来的一场（洗手间/走廊/角落）不该另置装。
        # 实测：苏晚意 36-2「邮轮洗手间外走廊」按地点落到「邮轮/度假」，
        # 但它夹在 36-1 与 36-3（宴会大厅）之间，同一夜的同一场晚宴 ⇒ 就是宴会装。
        for i in range(1, len(run) - 1):
            prev, cur, nxt = run[i - 1], run[i], run[i + 1]
            if (prev["costume_major"] == nxt["costume_major"]
                    and prev["costume_kind"] != "reuse"
                    and cur["costume_major"] != prev["costume_major"]
                    and _int(cur["episode"]) - _int(prev["episode"]) <= 1
                    and _int(nxt["episode"]) - _int(cur["episode"]) <= 1
                    and cur.get("time_env") == prev.get("time_env")):
                cur["costume_major"] = prev["costume_major"]
                cur["costume_kind"] = prev["costume_kind"]
                cur["why"] = f"并入{prev['code']}／{nxt['code']}（同一场活动夹心）"


def _merge_small(units: list, min_scenes: int = 2) -> list:
    """把「场次过少」的段落并入相邻段落（口径 ④）。

    并入方向取**间隔更近**的那侧 —— 剧情上更可能还是同一套衣服。
    """
    keep = [[rid, list(rs)] for rid, rs in units]
    guard = 0
    while len(keep) > 1 and guard < 100:
        guard += 1
        target = next((i for i, (_r, rs) in enumerate(keep) if len(rs) < min_scenes), None)
        if target is None:
            break
        _rid, rs = keep[target]
        left = keep[target - 1] if target > 0 else None
        right = keep[target + 1] if target + 1 < len(keep) else None
        if left is None:
            right[1][:0] = rs
        elif right is None:
            left[1].extend(rs)
        else:
            gl = _int(rs[0]["episode"]) - _int(left[1][-1]["episode"])
            gr = _int(right[1][0]["episode"]) - _int(rs[-1]["episode"])
            if gl <= gr:
                left[1].extend(rs)
            else:
                right[1][:0] = rs
        keep.pop(target)
    return [(rid, rs) for rid, rs in keep]


def _cut_oversized(seg: list, max_span: int) -> list:
    """跨度超限的复用段，按**等分跨度**再切（口径 ⑦ 的兜底）。

    为什么需要它：主角每集都出现，相邻场次间隔全是 0~2 集，
    `split_runs` 一个断点都找不到；套用「日常 15 集一套」就能控制住。
    切点吸附到最接近等分点的场次边界（不切开同集场次）。
    """
    if len(seg) < 2 or max_span <= 0:
        return [seg]
    eps = [_int(x["episode"]) for x in seg]
    width = eps[-1] - eps[0] + 1
    if width <= max_span:
        return [seg]
    n = (width + max_span - 1) // max_span
    step = width / n
    targets = [eps[0] + step * k for k in range(1, n)]
    cands = [(i, (eps[i] + eps[i - 1]) / 2.0)
             for i in range(1, len(seg)) if eps[i] != eps[i - 1]]
    picked = []
    for t in targets:
        best = None
        for i, mid in cands:
            if i in picked:
                continue
            d = abs(mid - t)
            if best is None or d < best[0]:
                best = (d, i)
        if best:
            picked.append(best[1])
    picked.sort()
    pieces, prev = [], 0
    for i in picked:
        pieces.append(seg[prev:i])
        prev = i
    pieces.append(seg[prev:])
    return [p for p in pieces if p]


def _venue_common(locs: list) -> str:
    """一组地点的最长公共前缀（≥2 字），用来给活动型造型命名，如「邮轮顶层」。"""
    if not locs:
        return ""
    pre = locs[0]
    for s in locs[1:]:
        i = 0
        while i < len(pre) and i < len(s) and pre[i] == s[i]:
            i += 1
        pre = pre[:i]
    return pre if len(pre) >= 2 else ""


def _make_node(seg: list, kind: str, major: str, run_ids: list) -> dict:
    eps = sorted({_int(x["episode"]) for x in seg if _int(x["episode"]) < 10 ** 6})
    occs, locs, whys = [], [], []
    for x in seg:
        if x["occasion"] not in occs:
            occs.append(x["occasion"])
        if x["location"] not in locs:
            locs.append(x["location"])
        if x.get("why") and x["why"] not in whys:
            whys.append(x["why"])
    label = BOX_LABEL.get(major, f"{major}装")
    if kind == "exclusive":
        # 活动型造型带上场地名（用户人工表的命名风格：「游轮晚宴（游轮顶层展厅）」）
        vc = _venue_common(locs)
        if vc:
            label = f"{label}（{vc}）"
    return {
        "kind": kind,                 # makeup / plot / exclusive / reuse
        "major": major,
        "occasions": occs,
        "label": label,
        "codes": [x["code"] for x in seg],
        "eps": eps,
        "span": (eps[-1] - eps[0] + 1) if eps else 0,
        "locations": locs,
        "whys": whys,
        "rows": seg,
        "runs": run_ids,
    }


def _venue_clusters(rows: list) -> list:
    """把场次按「同一场地」连成簇（并查集）。

    用于**活动型专属造型**（口径 ⑩）：一次活动的场地群 = 一套服装。
    实测依据（人工表）：邮轮顶层宴会大厅 ↔ 邮轮顶层展厅 是一次行程 ⇒ 一套；
    贺氏集团发布会后台 ↔ 大厅也是同一次活动 ⇒ 一套。
    v0.9 按「场合类别」拆（宴会/晚宴 vs 展/拍卖）会把一次行程切成两套。
    """
    n = len(rows)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(n):
        for j in range(i + 1, n):
            if find(i) == find(j):
                continue
            a, b = rows[i], rows[j]
            p = _common_prefix_len(a["location"], b["location"])
            close = abs(_int(a["episode"]) - _int(b["episode"])) <= 1
            absorbed = (a["why"].startswith("并入") or b["why"].startswith("并入"))
            if (same_venue(a["location"], b["location"])   # 前缀 ≥4 且占比够
                    or p >= 2                              # 同一次行程（邮轮… ↔ 邮轮…）
                    or (close and a["costume_major"] == b["costume_major"]
                        and (p >= 1 or absorbed))):        # 邻接 + 同大类（含出发换装场）
                union(i, j)

    # ---- 跨场地桥：同一次活动分处两个场地群（口径 ⑩ 的补丁）----
    # 实测：容晏 57-1「车库后门」→ 57-2/59-2「环山车道大屏幕前」是同一场赛车
    # （他去赛车场找表少爷），但两个地点名毫无公共前缀 ⇒ 上面的 venue 规则连不上，
    # 会被拆成两套「赛场装」。
    # 判据：两个簇**合起来的集数跨度 ≤ ACTIVITY_SPAN** 才算同一次活动。
    # ⚠️ 不能只看「同集 + 同大类」：苏晚意 53-1「马术俱乐部 休息区」与
    #    53-3「环山车道」同在第 53 集、同属赛事/运动，却是两场不同赛事
    #    （人工表：马术服 / 赛车服），跨度守卫（48~53 ＋ 53~59 = 12 集）挡掉它。
    def _eps_of(idxs: list) -> list:
        return [_int(rows[i]["episode"]) for i in idxs]

    while True:
        groups = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)
        roots = list(groups)
        merged = False
        for x in range(len(roots)):
            for y in range(x + 1, len(roots)):
                e = _eps_of(groups[roots[x]] + groups[roots[y]])
                if max(e) - min(e) + 1 <= ACTIVITY_SPAN:
                    union(roots[x], roots[y])
                    merged = True
                    break
            if merged:
                break
        if not merged:
            break

    out = {}
    for i, r in enumerate(rows):
        out.setdefault(find(i), []).append(r)
    return [sorted(v, key=lambda r: ep_key(r["block"])) for v in out.values()]


def plan_nodes(rows: list, aliases: list, stages: list = None,
               gap_break: int = 3, min_scenes: int = 2,
               max_span: int = 15, do_absorb: bool = True,
               do_makeup: bool = True) -> list:
    """最终产物：服装套列表（一套 = 一次出图）。

    v1.0 的归并单位从「时间窗连贯段」换成了「造型」——
      · 剧情型（病号服/无菌服/睡衣）：**同剧情形 = 一套**，全局归并（口径 ⑫）
      · 定妆：本角色首场 = 一套（口径 ⑪）
      · 活动型专属（发布会/宴会/展/赛事/邮轮）：**同场地群 = 一套**（口径 ⑩）
      · 日常/职场（可复用）：才按「剧情断点 + 跨度」分段（口径 ③④⑦）

    为什么必须改：v0.9 一律按时间窗切，会把横跨第 2~47 集的同一条睡裙切成 4 套。
    「剧情连贯 ⇒ 同一套」只是充分条件，不是必要条件（口径 ⑨）。
    """
    runs = split_runs(rows, gap_break)
    if stages:
        runs = [seg for run in runs for seg in split_by_stage(run)]
    for i, run in enumerate(runs):
        for r in run:
            r["run"] = i

    detect_plot(rows, runs, aliases)
    assign_costume(rows)
    if do_absorb:
        absorb(runs)
    if do_makeup:
        mark_makeup(rows)

    nodes = []
    # ① 剧情型：同剧情形 = 一套（跨集不切，口径 ⑫）
    by_plot = {}
    for r in rows:
        if r["costume_kind"] == PLOT_KIND:
            by_plot.setdefault(r["costume_major"], []).append(r)
    for major, seg in by_plot.items():
        seg.sort(key=lambda r: ep_key(r["block"]))
        nodes.append(_make_node(seg, PLOT_KIND, major, [seg[0]["run"]]))

    # ② 定妆（口径 ⑪）
    mk = [r for r in rows if r["costume_kind"] == MAKEUP_KIND]
    if mk:
        nodes.append(_make_node(mk, MAKEUP_KIND, MAKEUP_LABEL, [mk[0]["run"]]))

    # ③ 活动型专属：同场地群 = 一套（口径 ⑩）
    acts = [r for r in rows if r["costume_kind"] == "exclusive"]
    for seg in _venue_clusters(acts):
        nodes.append(_make_node(seg, "exclusive", seg[0]["costume_major"],
                                [seg[0]["run"]]))

    # ④ 日常/职场（可复用）：全局排序后按「剧情断点 + 跨度」分段（口径 ③④⑦）
    by_major = {}
    for r in rows:
        if r["costume_kind"] == "reuse":
            by_major.setdefault(r["costume_major"], []).append(r)
    for major, seg in by_major.items():
        seg.sort(key=lambda r: ep_key(r["block"]))
        units, cur, prev_ep, prev_loc = [], [], None, ""
        for r in seg:
            ep = _int(r["episode"])
            if cur and _is_break(prev_ep, ep, prev_loc, r["location"], gap_break):
                units.append([len(units), cur])
                cur = []
            cur.append(r)
            prev_ep, prev_loc = ep, r["location"]
        if cur:
            units.append([len(units), cur])
        for rid, s in _merge_small(units, min_scenes):
            for piece in _cut_oversized(s, max_span):
                nodes.append(_make_node(piece, "reuse", major, [rid]))

    nodes.sort(key=lambda n: ep_key(n["rows"][0]["block"]))
    return nodes


def render(name: str, aliases: list, rows: list, nodes: list,
           script_path: str, total_blocks: int, script_costume_lines: int,
           stages: list = None, gap_break: int = 3, min_scenes: int = 2,
           max_span: int = 15) -> str:
    """rows 已经是「实体到场」场次（调用方过滤过）。"""
    eps = sorted({_int(r["episode"]) for r in rows if _int(r["episode"]) < 10 ** 6})
    own_costume = [(r["code"], ln) for r in rows for ln in r["clothes"]]
    n_reuse = sum(1 for n in nodes if n["kind"] == "reuse")
    n_plot = sum(1 for n in nodes if n["kind"] == "plot")
    n_makeup = sum(1 for n in nodes if n["kind"] == "makeup")
    n_excl = len(nodes) - n_reuse - n_plot - n_makeup

    set_of = {}
    for i, nd in enumerate(nodes, 1):
        for c in nd["codes"]:
            set_of[c] = i

    L = []
    L.append(f"# {name} · 出场场次与换装方案（v1.1）")
    L.append("")
    L.append(f"- 剧本：`{script_path}`（全本 {total_blocks} 个场次）")
    L.append(f"- 别名：{' / '.join(aliases)}")
    if eps:
        L.append(f"- **到场场次：{len(rows)} 个**｜覆盖第 {eps[0]} ~ {eps[-1]} 集（共 {len(eps)} 集）")
    else:
        L.append(f"- 到场场次：{len(rows)} 个")
    L.append(f"- **造型数：{len(nodes)} 套**（定妆 {n_makeup} · 剧情 {n_plot} ·"
             f" 活动 {n_excl} · 日常/职场 {n_reuse}）")
    L.append("")
    L.append("> **归并单位是「造型」，不是「时间窗」** ——")
    L.append("> ① **一个造型 = 一套；跨集复用，不要求场次连续**。"
             "同一条睡裙出现在第 2 集和第 47 集，仍是同一套。")
    L.append("> ② **活动型专属 = 一次活动 = 一套**（按场地群归并）："
             "邮轮上的晚宴大厅与展厅是一次行程 ⇒ 一套；发布会后台与大厅 ⇒ 一套。"
             "两个场地群若合起来跨度 ≤ "
             f"{ACTIVITY_SPAN} 集，也并成一套（同一次活动换了场地，"
             "如赛车场的「车库后门 → 大屏幕前」）。")
    L.append("> ③ **同剧情形 = 一套**：病号服 / 无菌服 / 睡衣 各自全局一套，跨集不切。")
    L.append("> ④ **本角色首次出场的场次 = 定妆场**（独立一套，产定妆照用）。")
    L.append("> ⑤ **日常/职场**才按时间分段：剧情断点（间隔 ≥ "
             f"{gap_break} 集且换场地）处换新套，**同一场地最多容忍 {VENUE_MAX_GAP} 集"
             "（隔得更久就不是同一次连续生活了）**，单套覆盖超 {max_span} 集再切，"
             f"碎段（少于 {min_scenes} 场）并入邻居。")
    L.append("> **出场形式**：只统计**本人到场**的场次；电话出声、仅被提及不计。"
             "名单里写了但正文里没这个人的（剧本常把整集角色抄进名单）也不算。")
    L.append("> **服装依据**：全本 △ 描写行里只有 **{n} 行**涉及服装，"
             "其中提到本角色的 **{m} 行**。⇒ 只有「病历 / 睡衣 / 无菌服」这类"
             "**剧本明写**的才由代码定死，"
             "**其余款式由模型按身份 + 场合设计**。".format(
                 n=script_costume_lines, m=len(own_costume)))
    L.append("> **命名说明**：这里的造型名是**占位名**（场合依据 + 场地），"
             "不是最终服装。最终要落成「真丝睡裙」「贵妇连衣裙」这种**具体款式**，"
             "这一步交给模型。")
    if stages:
        L.append("> **剧情阶段（人工给定）**：" + "；".join(f"第 {s} 集起「{n}」" for s, n in stages)
                 + "。跨阶段必另置一套。")
    L.append("")

    # ---- 总表 -------------------------------------------------------------
    L.append("## 一、服装套总表")
    L.append("")
    L.append("| 套 | 类型 | 造型（占位名） | 覆盖集 | 场次 |")
    L.append("|---:|:---|:---|:---|:---|")
    for i, nd in enumerate(nodes, 1):
        kind = {"reuse": "日常/职场（可复用）",
                "plot": "**剧情（专属）**",
                "exclusive": "活动（专属）",
                "makeup": "**定妆（基准）**"}.get(nd["kind"], nd["kind"])
        span_txt = f"第 {nd['eps'][0]} ~ {nd['eps'][-1]} 集" if nd["eps"] else "—"
        codes = "、".join(nd["codes"])
        if len(codes) > 90:
            codes = codes[:90] + f"…（共 {len(nd['codes'])} 场）"
        L.append(f"| {i} | {kind} | {nd['label']} | {span_txt} | {codes} |")
    L.append("")

    # ---- 逐套明细 ---------------------------------------------------------
    L.append("## 二、逐套明细")
    L.append("")
    L.append("> **着装约束**是给模型的提示（这类场合该怎么穿、别穿成什么）；"
             "具体款式与发型仍由模型按角色身份设计。")
    L.append("")
    for i, nd in enumerate(nodes, 1):
        span_txt = f"第 {nd['eps'][0]} ~ {nd['eps'][-1]} 集" if nd["eps"] else "—"
        tag = {"reuse": "可复用（可跨多场反复出现）",
               "plot": "**剧情专属**（此段剧情里只穿这一套）",
               "exclusive": "活动专属（一次活动一套，不复用）",
               "makeup": "**定妆服**（流程第③步产物，不是剧情换装）"}.get(nd["kind"], nd["kind"])
        wide = nd["kind"] == "reuse" and nd["span"] > max_span
        L.append(f"### 套{i}：{nd['label']}　｜　{span_txt}　｜　{len(nd['codes'])} 场"
                 + (f"　⚠️ 跨度 {nd['span']} 集偏大，建议按剧情阶段再拆" if wide else ""))
        L.append("")
        L.append(f"- 类型：{tag}")
        L.append(f"- 场次：**{'、'.join(nd['codes'])}**")
        note = PLOT_NOTE.get(nd["major"]) or OCCASION_NOTE.get(nd["major"], "")
        L.append(f"- 着装约束（{nd['major']}）：{note}")
        if nd["kind"] == "reuse" and nd["occasions"]:
            L.append(f"- 场景构成：{' / '.join(nd['occasions'])}")
        if nd["whys"]:
            L.append("- 判定依据：")
            L.extend(f"  - {w}" for w in nd["whys"][:6])
        L.append(f"- 地点：{' / '.join(nd['locations'][:8])}"
                 + ("…" if len(nd["locations"]) > 8 else ""))
        clue = [f"`{c}` {ln}" for c, ln in own_costume if c in nd["codes"]]
        if clue:
            L.append("- 剧本原有服装线索：")
            L.extend(f"  - {x}" for x in clue)
        L.append("")

    # ---- 场次清单 ---------------------------------------------------------
    L.append("## 三、到场场次清单（按剧情顺序，供核对分组）")
    L.append("")
    L.append("| 序 | 集-场 | 地点 | 时段 | 着装语境 | 套 | 判定依据 |")
    L.append("|---:|:---|:---|:---|:---|---:|:---|")
    for i, r in enumerate(rows, 1):
        why = (r.get("why") or "").replace("|", "／")
        L.append(f"| {i} | {r['code']} | {r['location']} | {r['time_env']} | "
                 f"{r['occasion']} | {set_of.get(r['code'], '')} | {why} |")
    L.append("")
    return "\n".join(L)


def default_script() -> str:
    """默认剧本：**优先 docx**。

    ⚠️ 实测坑：`input/` 里的 `剧本.txt` 往往只是**前几集节选**，
    而 `file_reader.find_input_file` 是 txt 优先 ⇒ 静默只跑 5 集、还不报错。
    这里改成 docx 优先，拿不到再退回原函数。
    """
    d = HERE.parent / "input"
    for pat in ("*.docx", "*.doc", "*.txt", "*.md"):
        hits = sorted(d.glob(pat))
        if hits:
            return str(hits[0])
    return find_input_file(str(d))


def main():
    ap = argparse.ArgumentParser(description="换装节点规划（v1.1）")
    ap.add_argument("--name", required=True, help="角色名，如 贺景洲")
    ap.add_argument("--input", default=None, help="剧本路径（默认取 input 目录第一个）")
    ap.add_argument("--out", default=None, help="输出 md 路径")
    ap.add_argument("--gap-break", type=int, default=3,
                    help="剧情连贯段断点：与上一场间隔 ≥ 该集数且换了场地就断（默认 3）")
    ap.add_argument("--min-scenes", type=int, default=2,
                    help="日常/职场类：场次少于该数的段并入邻居（默认 2）")
    ap.add_argument("--max-span", type=int, default=15,
                    help="日常/职场类：单套覆盖集数上限，超了就等分再切（默认 15）")
    ap.add_argument("--stages", default=None,
                    help='剧情阶段边界（可选），如 "3:试探期,14:同住期,43:婚后"')
    ap.add_argument("--no-absorb", action="store_true",
                    help="关闭「出发前场 / 同夜延续」吸收")
    ap.add_argument("--no-makeup", action="store_true",
                    help="不把首次出场场次拆成「定妆服」")
    args = ap.parse_args()

    script_path = args.input or default_script()
    if not script_path:
        print("[错误] 没找到剧本，用 --input 指定")
        return 2

    stages = parse_stages(args.stages)
    full_script = read_script(script_path)
    blocks = split_into_blocks(full_script)
    aliases = resolve_aliases(full_script, args.name)
    rows = build_rows(blocks, aliases)
    if not rows:
        print(f"[错误] 剧本里找不到「{args.name}」")
        return 1
    n_on_before = sum(1 for r in rows if r["form"] == "实体")
    rows = visible_only(phone_only(rows, aliases))
    if not rows:
        print(f"[错误]「{args.name}」没有到场场次（只有电话/被提及）")
        return 1
    for r in rows:
        r["stage"] = stage_of(_int(r["episode"]), stages)
    nodes = plan_nodes(rows, aliases, stages, args.gap_break, args.min_scenes,
                       args.max_span, not args.no_absorb, not args.no_makeup)

    script_costume_lines = 0
    for b in blocks:
        for ln in b.get("body_lines", []):
            if ln.strip().startswith("△") and is_costume_line(ln.strip()):
                script_costume_lines += 1

    report = render(args.name, aliases, rows, nodes, script_path,
                    len(blocks), script_costume_lines, stages,
                    args.gap_break, args.min_scenes, args.max_span)
    if args.out:
        out_path = Path(args.out)
    else:
        out_path = HERE.parent / "output" / "换装节点" / f"{args.name}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    eps = sorted({_int(r["episode"]) for r in rows if _int(r["episode"]) < 10 ** 6})
    print(f"角色：{args.name}（别名 {' / '.join(aliases)}）")
    print(f"有具体人名：{'是' if is_named_character(args.name) else '否'}")
    if n_on_before != len(rows):
        print(f"到场场次：{len(rows)} 个"
              f"（已剔除 {n_on_before - len(rows)} 个「名单写了但人只在电话里」）")
    else:
        print(f"到场场次：{len(rows)} 个")
    if eps:
        print(f"覆盖第 {eps[0]} ~ {eps[-1]} 集（{len(eps)} 集）")
    n_reuse = sum(1 for n in nodes if n["kind"] == "reuse")
    n_plot = sum(1 for n in nodes if n["kind"] == "plot")
    n_mk = sum(1 for n in nodes if n["kind"] == "makeup")
    print(f"造型数：{len(nodes)} 套（定妆 {n_mk} · 剧情 {n_plot} · "
          f"活动 {len(nodes) - n_reuse - n_plot - n_mk} · 日常/职场 {n_reuse}）")
    for i, g in enumerate(nodes, 1):
        eps_txt = f"第{g['eps'][0]}~{g['eps'][-1]}集" if g["eps"] else ""
        tag = {"reuse": "复用", "plot": "剧情", "exclusive": "活动",
               "makeup": "定妆"}.get(g["kind"], g["kind"])
        warn = (" ⚠️跨度大" if g["kind"] == "reuse" and g["span"] > args.max_span else "")
        print(f"  套{i:>2}  [{tag}] {g['label']:<20} {eps_txt:<14} ({len(g['codes']):>2} 场)"
              f"{warn}  {'、'.join(g['codes'])}")
    print(f"报告已写入：{out_path}")
    dec = re.search(r"集数[：:]\s*(\d+)\s*集", full_script)
    blk_max = max((_int(b.get("episode")) for b in blocks
                   if _int(b.get("episode")) < 10 ** 6), default=0)
    if dec and blk_max and blk_max < int(dec.group(1)):
        print(f"[警告] 剧本只解析到第 {blk_max} 集，剧头声明 {dec.group(1)} 集"
              f" —— 很可能读到的是节选，这份结论别用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
