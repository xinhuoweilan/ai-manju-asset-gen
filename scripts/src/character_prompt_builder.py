"""人物提示词：名单口径 + 版数口径 + 生成。

【名单从哪来】collect_roster(blocks)
  两个来源合并：剧本每场的「出场人物：…」行 + 对白行说话的说话人。
  两个实测踩过的坑：
    · 「出场人物：绑匪司机 / 苏晚意 / 绑匪甲、乙」——缩写「绑匪甲、乙」指的是
      绑匪甲 + 绑匪乙。直接按分隔符切会切出一个孤零零的「乙」，被当成一个新人物。
    · 「苏晚（冷眼）：…」是「苏晚意」的简称（全剧仅此 1 处），不是另一个人。
      靠 _abbr_match 折回本人，不许单独成行。

【版数口径】decide_versions()（2026-09-22 用户定稿）
  · 没有具体人名的（代称 / 称谓 / 群体 / 编号）→ 一律 1 版。
    这类角色每次出现都可能换人、没有长相可言，3 版只会出三张几乎一样的图。
  · 有具体人名的 → 看场次：出场 ≥3 集 或 ≥5 个场次 → 3 版，否则 1 版。
  「有没有具体人名」由 is_named_character() 判：单字名、群体词、通用职业/身份词、
  称谓词尾（夫人/医生/手下…）、编号尾巴（甲/乙/丙、男/女、A/B）、描述性修饰
  （神秘/黑衣/吊带裙…）—— 命中任一即为代称。

【小传与别名】人物小传里写成「苏晚意（Natalia Gordon 22岁）」，用它把中英文名互认。

  ⚠️ 剧本里还有长得一模一样、但根本不是小传的行：
        苏晚意（os）：如果要更深入地调查容家……
        拍卖师（VO）：恭喜容先生拍下公主的王冠！
        屏幕字（King）：【R，你是不是有病？……】
    早期版本会把 os / VO / King 当成英文名收进别名池，后果很硬：
      · 「拍卖师」别名里有 'VO' → 全文命中 91 次、被算成「42 集 / 60 个场次」
        → 一个只有一句台词的拍卖师被判成重要角色、生成 3 版（实测踩过）
      · 「苏晚意」别名里有 'os' → 命中数虚高
      · 「屏幕字」被当成一个人物，还混进了人物小传
    所以下面必须把「对白/旁白标注」和「真英文名」分开。

【生成口径】（2026-09-24 用户定稿 v12）
  · 外貌依据优先级：**有小传 → 以小传为主、剧情为辅；没有小传 → 根据剧情推演**。
  · **性别锚点（v10）**：锚点行从「人种/年龄感/气质阶层/体型」扩为
    「**性别**/人种/年龄感/气质阶层/体型」。实测痛点：苏晚意某一版的描写恰好全是中性词
    （"脸型偏窄长、下巴略尖" + "米灰西装、阔腿裤、白色乐福鞋"），模型就画成了**男性面孔**；
    方案1/方案3 没事只是因为碰巧有女性化线索（"适合涂正红"、蝴蝶结+高跟鞋+珍珠耳钉）。
    ⇒ 凡是模型无法从描写里稳定推出的属性（性别、年龄、族裔），都必须显式写进锚点。
    · **性别判定优先级（v10.1 用户口径：先查小传，再走推定，顺序不可颠倒）**：
      ① 依据里若有【人物小传】→ **先在里头找性别线索**，认「**与本人直接相关的关系称谓**」
         （前妻/前夫、妻子/丈夫、表妹/表哥、师妹/师兄、女儿/儿子、母亲/父亲…），
         **写了什么就是什么，直接采用、不得改判**；
         ⚠️ 小传里的第三人称「她/他」多半在**指代别人**，不能拿来判本人性别
         （实测：容晏小传「后来与**她**成为协议情侣」的「她」是苏晚意，照判会判反）；
      ② 没有小传、或小传没提性别 → 从出场场次的剧情、称谓、人物关系中推；
      ③ 线索仍不足 → 按整体身份与关系推定一个最合理的，**一经判定即锁定**；
      三版严禁换性别；锁定后该版所有细节（发型/妆容/服装/配饰/身形）都要能读出该性别。
    · 代码侧 `BASE_GENDER_RE` 校验锚点里是否真的带性别词，缺了只报警不改产物。
  · **人种硬规则（v9）**：剧本是**中英文对照的欧美背景剧本**（用户口径：这类本子一律默认欧美，
    不必再判世界观；遇到别的类型用户会提前说明）—— 主流/豪门角色**一律白种人**，
    除非剧本明写其他族裔。
    ⚠️ 中文译名 ≠ 亚裔（实测踩坑：Natalia Gordon / Evans Group 只因译名"苏晚意"被判成亚裔）。
    · **族裔落地（v12，2026-09-24 用户定稿：交回给模型，别再堆限制）**：
      v11 曾把这条写成一整套硬清单（锚点必带骨相词 / 瞳色只准浅色系 / 正文禁用一串
      「东亚脸词」/ 外壳再加一大段反漂移），确实压住了亚洲脸，但用户判断
      **「限制太多，模型满脑子只记得要不一样，操作就变形了」**
      ⇒ v12 把清单收回成**一条原则 + 一个反例**，其余交回模型自己拿捏：
        ① 判成白种人时外貌就按白人写 —— 五官骨相立得住、发色瞳色肤色与人种统一
           （白种人瞳色通常偏浅）；
        ② 保留 v11 的真实反例当教训（写「双眼皮褶皱浅/瞳色偏深棕」⇒ 亚洲脸；
           写「灰蓝色眼睛/眼窝深陷/眉骨高耸」⇒ 标准白人）；
        ③ **不再**强制锚点带骨相词、**不再**列禁用词表；固定外壳只留一句「不得漂移」。
      · **三版差异（v12）**：口径收敛成一句「三版不一样即可」——
        「有『让人挑得出差别』这个意识就行，别为了显得不同去动固定设定」。
      · **v12.1（同一天，拿真实样本跑过一遍后补的两刀）**：放松之后又踩了两个坑，
        都是「跑一遍看产物」才发现的 ——
        ① 写了「差异落在发型/服装/妆容这些可变的地方」⇒ 模型把脸当成"三版不变的"，
           方案2/3 直接写成「同是鹅蛋脸…」「脸型骨相同前…」这种**相对上一版**的描述；
           而每个方案是要**单独拿去出图**的 ⇒ 这两版等于没写脸，白扔钱。
        ② 不再要求骨相词之后，脸明显写虚了（「眼窝微深」—— 恰好是 v11 认定的高危写法）。
        ⇒ 补两处：①【任务】写明「**每版独立完整、单独可出图**，不许写「同上」」；
                  ② 人种那条补一句「该写具体就写具体，别用『五官立体』一句含糊带过」。
        ⚠️ 仍然**不**恢复「锚点必带骨相词」的硬性要求、不恢复禁用词表、不恢复长外壳 ——
           放松的方向不变，只是把「独立成篇」这两个字补上。
      · **v12.2（同一天，等 v12.1 整本 97 份产物落盘后逐条体检出来的 4 处）**：
        体检数字（`docs` 之外的一次性脚本，只读产物）：**109 处写姿态/手部动作**、
        **72 处写神情/情绪**、**71 处是画不出来的叙事比喻**、**46/46 个单版角色多吐了
        「方案二/三」两段**。前三类都是**和固定外壳直接打架**——外壳把姿态（双手自然垂放、
        站姿挺拔、手中不拿物品）和表情（全程面无表情）写死了，正文又写「一手插在大衣口袋里」
        「表情是柔弱欲泣的楚楚可怜」，出图只能二选一。
        ⇒ ① system prompt 补一条**分工**：「你只负责静态外观，姿态/手部、表情情绪、
             叙事比喻、自我解释四类由外壳负责，你不要写」，眼睛只写形状（眼型/眼褶/瞳色）；
           ② 段数按 `version_count` 分开措辞：单版明说「只输出 1 段，严禁再写方案二/三」；
           ③ 解析兜底：单版**真出现**方案标注时只保留第 1 段（不能无脑按空行切，会腰斩）；
           ④ `META_NOTE_RE` 删掉模型夹带的自评备注（实测原样混进产物的一处：
              「手杖只是装饰性道具，此处不写。」）；眼型别用「杏眼/丹凤眼」、发色别用「乌黑/深黑」。
        ⚠️ 这四条都**不是族裔约束**，是「别和外壳抢同一件事」的分工问题 ——
           v12「不给族裔堆限制」的方向没有回退。
      · **v12.3（紧跟着 v12.2 整本重跑后的对比，同一天）**：
        四类冲突按预期归零（107→6 / 86→2 / 71→12 / 1→0），**但族裔报警反而涨了**
        （`asian_cues` 17→34 处、被点名角色 15→25 个）。逐词拆开看，涨幅全在两处：
        **「双眼皮褶皱浅」0→14 处、「单眼皮」0→3 处、`眼型细长` 1→5 处**。
        根因不是模型变差，是我自己在 v12.2 里写的两句话把词喂出去了：
          ① 人种第 3 条的**反例原文**里就写着「双眼皮褶皱浅、瞳色偏深棕」——
             模型把反例当样本抄了（对比：「杏眼」写在**明确的「别写」列表**里，反而 4→0）；
          ② 我建议的措辞里带了「狭长 / 细长」，而 `ASIAN_FACE_CUE_RE` 恰好把
             `眼型细长 / 细长眼 / 单眼皮 / 内双 / 双眼皮褶皱浅` 全列成高危词。
        ⇒ 改法：**反例不再给可抄的原文**（改成「按东亚长相写眼睛（眼褶写成软的）」）；
          建议措辞换成**正向**的「眼窝深、上眼睑褶皱要『清晰/明显』」，眼型只给
          「偏长/偏圆/上挑/下垂」；禁止项一律放进**明确的「不要写」列表**里，不放进反例。
        📌 **可复用的判据**：「反例/反面样本」和「禁用清单」在模型眼里**不是一回事** ——
           放在「不要写 X」里安全，放在「曾经写成 Y」里就会被抄成 Y。
           ⚠️ 另外：**改完必须正负两边都量**。只看「四类冲突归零」会以为改好了，
           涨的那一边（族裔词）只有把报警器数字前后对一遍才发现。
      · 代码侧 `check_race_consistency()` 保留并只报警（`race_ok` / `asian_cues` / `dark_iris`），
        `bone_ok` 降为纯观测项、不再告警。
  · **设定图一律站姿全身（v8）**：不写轮椅 / 病床 / 躺卧 / 坐姿 / 手中物品等临时状态与伤病道具，
    剧本里提到也一律不体现（实测踩坑：小传"车祸坐轮椅"被写进描写，与外壳"站姿挺拔"打架）。
  · 出场依据沿**剧情弧线均匀抽 6 个场次**（含首尾）。苏晚意跨 58 集也能看到整条弧线。
  · 依据里的**纯英文译文行一律去掉**（实测占 45%、对推演外貌零价值），
    中英混排行切掉英文尾巴，重复行只留一次。对白保留 —— 说话方式和称呼
    也是判断年龄/身份/气质的线索。
  · **模板与变量分离**（省 token 的关键）：
    - 固定外壳由代码持有（横屏四宫格/影棚布光/真实皮肤纹理/模糊特效规则/
      姿态/表情/标志/四格排列/**族裔反漂移约束 v11**），在所有人物 × 所有版本 ×
      所有调用之间复用，模型不重写。
    - 模型输出 = 一行「固定设定」（性别/人种〔白种人须带骨相词〕/年龄感/气质阶层/体型）
      + N 段完整人物描写（每版 200~400 字）。固定设定是**三版共用的锚点**，由代码接到每版开头，
      从结构上保证三版不会各写各的人种与性别（用户口径：一开始就把人种/性别定死，其余自由发挥）。
    - 差异维度不指定：模型自己决定哪些维度做差异（脸型五官 / 肤色 / 发型 /
      服装 / 配饰…都不限）。v5 的槽位写法把描写限制在「脸型/服装」两行短字段，
      产物里 3 版几乎一样，用户看过 txt 后否掉了。
  · **找不到的人名不再硬生成**：出场名单/对白/正文里都没有这个人时，
    直接跳过、不调 API，并标出来给用户核对（老版本会拿剧本开头 3000 字糊弄过去）。
  · **缓存键带剧本内容指纹**（`_script_signature`）：用 1~5 集的 txt 跑过一轮后，
    再换全本 docx 跑，不会命中旧缓存（否则会拿前 5 集的依据当初成品交付）。
  · **三版雷同检测**：相似度 ≥ `VARIANT_SIMILARITY_THRESHOLD` 报警（只提示，不改写产物）。
  · ⚠️ 改了 system prompt 文本 / 外壳文本 / 解析方式必须同步改 `CONTEXT_VERSION`，
    否则旧缓存会按老格式组装导致插不进去。
"""
import difflib
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from ds_api import call_ds_api
from cache_utils import load_cache, save_cache
from progress import format_duration, progress_line
from scene_context import (CHARACTER_LINE_RE, CJK_RE, EPISODE_MARK_RE, _clean_name,
                           cut_english_tail, is_character_roster)


CHARACTER_SYSTEM_PROMPT = """你是AI漫剧人物设定提示词的「形象顾问」。代码已准备好**通用模板**（横屏四宫格/影棚布光/模糊特效/姿态/表情/标志/四格排列等不变部分）由代码统一拼接——你只输出该人物的人物描写本身，不要重复模板内容，不要加解释，不要写人话。

【人种判定】（先定这条，该人物的所有版本共用）
1. 本剧本是**中英文对照的欧美背景剧本**（人名、机构公司名、地名都是欧美的，中文名字是英文名的译名）
   —— **一律按欧美背景处理，不需要再去判断剧本属于哪个国家/地区**。
   （用户口径：中英文对照的剧本统一默认欧美；若换了别的类型，用户会提前特别说明。）
2. 默认规则：**主流角色与豪门家族一律白种人**
   （核心主角/配角/家族成员/配偶/继承人/管家与下属等所有身份）。
   唯一例外：只有剧本**明确写了**其他族裔时才改判 —— 写明国籍/血统/外貌/出身地是亚裔、黑人、
   拉美裔、混血等（例如"街头混混"、"帮派成员"、"边境人士"、"亚裔面孔/华人血统"）。
   ⚠️ **名字是中文，绝不等于亚裔**：判断人种只看英文名、国籍、公司/组织、家族关系与出身线索，
   **不得因为角色名是中文就判成亚裔**。
   （实测踩过的坑：角色英文名 Natalia Gordon、前夫 Alexander Douglas、家族企业 Evans Group，
   只因中文译名叫"苏晚意"就被判成亚裔——这是错的，必须是白种人。）
   如果用户有单独特殊修改要求（如"发色换成金发"、"改为黑人"），以特殊要求为准。
3. 判成白种人时，**外貌就按白人的样子写**：五官骨相（眉骨、眼窝、鼻梁、下颌）要立得住，
   发色、瞳色、肤色与人种统一 —— 白种人的瞳色通常偏浅（浅蓝、灰绿、浅褐这一类）。
   实测踩坑：锚点写了「白种人」，正文却按东亚长相写眼睛（眼褶写成软的）、瞳色又写成深棕，
   出图就是一张亚洲脸；
   同一批里写成「灰蓝色眼睛、眼窝深陷、眉骨高耸」的那版才是标准白人。
   ⇒ 记住这个教训就行，怎么写由你判断；但**该写具体就写具体**
   （「眼窝深邃、眉骨与眼窝落差明显、鼻梁挺拔」），别用「五官立体」一句含糊带过。
   · 另有两处实测踩到的写法。⚠️ **下面列出的都是「要避开的说法」，不是可以照抄的样本**
     （踩过：把反例原文写进 prompt 后，模型直接把它抄进正文 —— 反例里那句眼褶写法
      一次从 0 处涨到 14 处）：
     ① 眼睛按白人的骨相写：**眼窝要深、上眼睑的褶皱要「清晰 / 明显」**；
        眼型只在「偏长 / 偏圆 / 上挑 / 下垂」里挑一个；不要写「杏眼」「丹凤眼」「单眼皮」「内双」；
     ② 发色走浅金 / 亚麻金 / 栗棕 / 深棕这一路；不要写「乌黑」「深黑」（白种人极少纯黑发）。
4. 人种只判定一次并写进「固定设定」行 —— **三版的性别、人种与五官骨相必须一致，
   严禁某一版漂移成别的族裔**（三版之间怎么做出区别见【任务】第二步）。

【性别判定】（v10.1：**先查小传，再走推定**；这三步的顺序不可颠倒）
1. **第一优先 —— 查【人物小传】**。依据里若有【人物小传】段，**先在这一段里找性别线索**：
   ① **与本人直接相关的关系称谓**最可靠：前妻 / 前夫、妻子 / 丈夫、表妹 / 表哥、师妹 / 师兄、
      女儿 / 儿子、母亲 / 父亲、夫人 / 先生、小姐 / 少爷 等。
      例：小传写「贺景洲前妻」⇒ 本人女性；写「苏晚意的师兄」⇒ 本人男性。
   ② ⚠️ **小传里的第三人称「她 / 他」多半在指代别人，不能拿来判本人的性别**
      （实测踩坑：容晏小传里那句「起初怀疑并试探苏晚意,后来与**她**成为协议情侣」，
      「她」指的是苏晚意；照着判会把容晏判反）。
   **小传写明什么就是什么，直接采用，不得改判**（小传与后文剧情冲突时，以小传为准）。
2. **第二优先 —— 没有小传，或小传里没提性别**：再从出场场次的剧情、称谓、人物关系里推。
3. **第三优先 —— 线索仍然不足**：按人物的整体身份与剧情关系推定一个最合理的性别，**一经判定即锁定**。
4. ⚠️ **性别必须写进「固定设定」行，所有版本必须是同一性别，严禁每版换性别**。
   实测踩坑：女角色的某一版描写恰好全是中性词（"脸型偏窄长、下巴略尖"配"米灰西装、阔腿裤、
   白色乐福鞋"），模型就自由发挥成了一张男性面孔，整版报废只能重跑。
5. ⚠️ **锁定性别后，该版所有细节（发型、妆容、服装、配饰、气质走向、身形）都必须能让人一眼读出该性别**。
   写女性时服装不必生硬地套裙子，但要保留明确的女性信号（明确的女性发型与妆容、收腰或贴合女性
   身形的剪裁等），避免"西装外套+阔腿裤+平底皮鞋"这类看不出性别的搭配；
   写男性时避免裙装、发饰、蕾丝、高跟鞋等。

【任务】
第一步：判定该人物的**固定设定** —— 性别、人种、年龄感、气质阶层、体型。这五样是同一个角色的锚点，一经判定，所有版本完全一致。
第二步：在固定设定之外**自由发挥**，写该版的外貌与造型。**要求就一条：三版不一样** ——
心里有「这三版得让人一眼挑得出差别」这个意识就够了，不必刻意去找差异点，
更不要为了显得不同去改动固定设定（性别、人种、五官骨相三版必须一致）。
⚠️ 但**每一版都必须独立、完整**地写全（每一段都要单独拿去出图），
不许出现「同上」「同是…」「脸型骨相同前」这类**相对上一版**的写法 —— 那样这一版就废了。

【输出要求】
- 只写给定这一个人物，不要写其他人。
- 第一行输出：固定设定：〔性别〕，〔人种〕，〔年龄感〕，〔气质阶层〕，〔体型〕
- 段数以任务里的说明为准：多版角色 3 段、单版角色 1 段。**说几段就写几段** ——
  单版角色写完「方案一：」这一段就**停笔**，**绝对不要再写「方案二：」「方案三：」**
  （多写的段落会被丢弃，等于白花钱）。
- 每段前用「方案一：」「方案二：」「方案三：」开头，段与段之间用空行分隔。
- 每段只写该版自己的外貌细节（脸型五官 / 肤色 / 发型 / 服装 / 配饰等）。**不必重复「固定设定」里的内容**——代码会把固定设定接到每版开头。
- 描写要细致流畅，避免短字段堆砌（不要写成「脸型：鹅蛋脸 / 鼻型：高」这种）。
- ⚠️ **你只负责「静态外观」**（脸型五官 / 肤色 / 发型 / 身型 / 服装 / 配饰）。
  下面四类由代码的外壳统一规定，你一写就是**和外壳自相矛盾**，出图两头不讨好：
  · **姿态与手部动作**：站姿、含胸、侧身、重心、双手垂放 / 插兜 / 交叠 / 抱臂 / 抬手 / 攥衣角…
    （外壳已写死：站姿全身、双手自然垂放于身体两侧、手中不拿任何物品）；
  · **表情与情绪**：神情、表情、委屈 / 无辜 / 楚楚可怜 / 笑意 / 哭意，以及「目光锐利」「眼神沉静」
    这类**带情绪的目光**（外壳已写死：全程面无表情）——眼睛只写**形状**：眼型、眼尾走向、
    双眼皮褶皱、瞳色；
  · **叙事化比喻**：「像随时准备出手」「透着一股被宠坏的纨绔气」「像是常年受冻」这类
    **画不出来**的形容不要写（气质阶层已经写在「固定设定」里了），只写**能拍出来的外观特征**；
  · **自我解释**：不要写「某物只是装饰性道具，此处不写」这种给自己的备注。
- **一律按站姿全身设定图来写：不要写轮椅、病床、躺卧、坐姿、拐杖、输液、绷带等任何临时状态或伤病道具，也不要写角色手中握着/拿着/抱着任何物品。** 剧本里即使提到这些（车祸、住院、拄拐、手持武器/酒杯等），也**一律不体现在外貌描写里** —— 这些是剧情状态，不是人物长相。
- 不要输出模板内容（横屏四宫格/布光/模糊/姿态/表情/标志/四格排列等）。
- 不要解释、不要总结、不要写题外话。
- 不要编造剧本里完全没出现过的具体物品（细节描写要忠于角色身份与场合）。"""


# ---------------------------------------------------------------------------
# 中英文名互认
# ---------------------------------------------------------------------------
BIO_RE = re.compile(r"([\u4e00-\u9fa5]{2,8})\s*[（(]\s*([A-Za-z][A-Za-z .'’\-]{1,40})")

# 括号里是这些词的 —— 是对白标注，不是人名
DIALOGUE_TAG_RE = re.compile(r"^(?:os|vo|ov|v\.?o|o\.?s|旁白|画外音|内心|独白)$", re.I)
# 匹配位置后面紧跟「）：」的，是「人名（标注）：台词」
BIO_DIALOGUE_TAIL_RE = re.compile(r"^\s*[)）]?\s*[：:]")

# 【人物小传】小节 与 正文起点（第 N 集）
BIO_SECTION_RE = re.compile(r"^\s*【?\s*人物小传\s*】?\s*$")
EPISODE_START_RE = re.compile(r"^\s*第\s*\d+\s*集")


def iter_bio_pairs(text: str) -> list:
    """抽出「中文名（English Name」配对，并滤掉对白标注造成的假配对。"""
    out = []
    for m in BIO_RE.finditer(text):
        zh, en = m.group(1).strip(), m.group(2).strip()
        if not zh or not en:
            continue
        if DIALOGUE_TAG_RE.match(en):
            continue
        if BIO_DIALOGUE_TAIL_RE.match(text[m.end():]):
            continue
        out.append((zh, en))
    return out


def _bio_section_lines(full_script: str) -> list:
    """取出【人物小传】那一段的行；剧本没这个标题时退回全文。"""
    lines = [ln.strip() for ln in full_script.split("\n")]
    start = next((i for i, ln in enumerate(lines) if BIO_SECTION_RE.match(ln)), None)
    if start is None:
        return lines
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if EPISODE_START_RE.match(lines[j]):
            end = j
            break
    return lines[start:end]


def resolve_aliases(full_script: str, name: str) -> list:
    """把中英文名合并成一组别名，避免只统计到一半语料。"""
    aliases = [name]
    name = name.strip()
    for zh, en in iter_bio_pairs(full_script):
        matched = (
            name == zh
            or name == en
            or (len(name) >= 2 and name in en)
            or (len(en) >= 2 and name in zh)
        )
        if matched:
            for a in (zh, en):
                if a and a not in aliases:
                    aliases.append(a)
    return aliases


def extract_character_bio(full_script: str, aliases: list) -> str:
    """抓人物小传：只认「中文名（English Name」这种行，避免误抓对白里的角色名。

    只在【人物小传】小节里找（正文里同形状的对白行已被 iter_bio_pairs 滤掉，
    这里再收一次范围，双保险）。
    """
    lines = _bio_section_lines(full_script)
    targets = set()
    for zh, en in iter_bio_pairs("\n".join(lines)):
        if any(a == zh or a == en or (len(a) >= 2 and (a in zh or a in en))
               for a in aliases):
            targets.add(zh)
    if not targets:
        return ""
    hits = []
    for i, ln in enumerate(lines):
        if not ln:
            continue
        if any(zh in ln for zh in targets):
            # 小传也是中英对照写的（中文一行、英文译文一行）——
            # 译文对推演外貌零价值，去掉，省一半 token
            window = [x for x in lines[i:i + 3] if x and CJK_RE.search(x)]
            if window:
                hits.append("\n".join(window))
    return "\n\n".join(hits[:4])


# ---------------------------------------------------------------------------
# 名单：从剧本里抽人物
# ---------------------------------------------------------------------------
# 同一场戏里的编号/性别标记：「围观者甲」和「围观者」是两个不同的人，
# 但「苏晚」和「苏晚意」是同一个人 —— 差别就在被截掉/多出来的那截是不是这些字。
INSTANCE_MARKER_RE = re.compile(r"^[甲乙丙丁戊己庚辛壬癸ABCDEFGH1-9一二三四五六七八九十男女老少]+$")
# 同一个字符集，但没有 ^$ 锚点 —— 用来从「绑匪甲」剥出词干「绑匪」
# （⚠️ 别拿上面那个带锚点的正则做 sub，带 ^$ 的图案在 sub 里只匹配「整串就是编号」的情况）
INSTANCE_TAIL_STRIP_RE = re.compile(r"[甲乙丙丁戊己庚辛壬癸ABCDEFGH1-9一二三四五六七八九十男女老少]+$")


def split_cast_names(raw_value: str) -> list:
    """把「苏晚意 / 绑匪甲、乙」拆成 ['苏晚意', '绑匪甲', '绑匪乙']。

    「绑匪甲、乙」这种缩写是剧本里的常态（还见过「绑匪丙/甲」）。
    直接按分隔符切会切出一个孤零零的「乙」——它既不是名字，也不是人物，
    后面会被当成一个出场 1 场的龙套去生成一张图。
    规则：遇到纯编号 token，就把它并进**上一个 token 去掉编号后的词干**。
    """
    out = []
    for raw in re.split(r"[/、,，]", raw_value):
        nm = _clean_name(raw)
        if not nm:
            continue
        if out and INSTANCE_MARKER_RE.match(nm):
            stem = INSTANCE_TAIL_STRIP_RE.sub("", out[-1])
            cand = stem + nm
            if stem and cand not in out:
                out.append(cand)
            continue
        if nm not in out:
            out.append(nm)
    return out


def _iter_cast_lists(blocks: list):
    for b in blocks or []:
        for line in b.get("body_lines", []):
            m = CHARACTER_LINE_RE.match(line.strip())
            if m:
                yield m.group(1)


def _iter_speakers(blocks: list):
    for b in blocks or []:
        for line in b.get("body_lines", []):
            s = line.strip()
            if s.startswith("△"):
                continue
            m = re.match(r"^([^：:]{1,20})[：:]", s)
            if not m:
                continue
            nm = _clean_name(m.group(1))
            if nm:
                yield nm


def _abbr_match(short: str, full: str) -> bool:
    """short 是不是 full 的简称（「苏晚」→「苏晚意」）。

    「围观者甲」「围观者男」不算 —— 那是同场戏里的另一个人，不是简称。
    """
    if len(short) < 2 or len(full) < 2 or not full.startswith(short):
        return False
    return not INSTANCE_MARKER_RE.match(full[len(short):])


def collect_roster(blocks: list) -> list:
    """全剧人物名单：出场人物行 + 对白说话人，按首次出现排序，简称折回本人。"""
    cast = []
    for value in _iter_cast_lists(blocks):
        for nm in split_cast_names(value):
            if nm not in cast:
                cast.append(nm)

    roster = list(cast)
    for nm in _iter_speakers(blocks):
        if nm in roster:
            continue
        # 「苏晚」是「苏晚意」的简称 —— 折进本人，不单独成行
        if any(_abbr_match(nm, known) for known in roster):
            continue
        roster.append(nm)
    return roster


# ---------------------------------------------------------------------------
# 「有没有具体人名」—— 决定版数的唯一判据
# ---------------------------------------------------------------------------
# 群体 / 集合
GROUP_NAME_RE = re.compile(r"^(?:众|群)|(?:们|人群|众人)$")

# 通用职业 / 身份词（可以独立存在的完整名字）
GENERIC_ROLE_WORDS = {
    "保镖", "保安", "警卫", "管家", "女佣", "佣人", "下人", "服务生", "服务员",
    "侍者", "经理", "助理", "秘书", "司机", "医生", "护士", "裁判", "教练",
    "讲解员", "负责人", "主持人", "播报", "记者", "专家", "律师", "法官",
    "警察", "警员", "手下", "接头人", "路人", "围观者", "工作人员", "摄影师",
    "化妆师", "拍卖师", "老师", "教授", "院长", "校长", "会长",
}

# 称谓 / 身份词尾 —— 命中即「这不是个名字，是个称呼」
GENERIC_ROLE_TAILS = (
    "夫人", "老夫人", "太太", "老爷", "老太爷", "少爷", "小姐", "先生", "女士",
    "老板", "总裁", "董事长", "总经理", "经理", "主管", "主任", "部长",
    "医生", "大夫", "护士", "律师", "法官", "警察", "警官", "保安", "保镖",
    "管家", "女佣", "佣人", "下人", "司机", "秘书", "助理", "侍者", "服务生",
    "服务员", "教练", "裁判", "讲解员", "负责人", "主持人", "专家", "记者",
    "摄影师", "化妆师", "拍卖师", "播报", "头目", "老大", "手下", "杀手",
    "刺客", "绑匪", "劫匪", "混混", "成员", "部下", "下属", "旧部", "枪手",
    "打手", "跟班", "保镖", "男人", "女人", "女生", "男生", "老人", "老者",
    "女孩", "男孩", "孕妇", "者", "女", "男", "父", "母", "爸", "妈",
    "叔", "伯", "姨", "舅", "哥", "姐", "弟", "妹", "爷", "奶",
)

# 描述性修饰 —— 出现即说明这是「描述一个人」而不是「一个人的名字」
DESCRIPTIVE_MARKERS = (
    "神秘", "黑衣", "白衣", "红衣", "西装", "吊带裙", "年轻", "中年", "青年",
    "老年", "高个", "瘦高", "矮胖", "蒙面", "头套", "墨镜", "大胡子",
)

# 编号尾巴：绑匪甲 / 围观者男 / 绑匪A / 记者2
INSTANCE_TAIL_RE = re.compile(r"(?:[甲乙丙丁戊己庚辛壬癸]|[ABCDEFGH]|[一二三四五六七八九十]|[男女老少])$")


def is_named_character(name: str) -> bool:
    """这个名字里有没有「具体人名」？没有的一律 1 版。"""
    nm = (name or "").strip()
    if len(nm) < 2:                       # 「甲」「乙」这种纯编号
        return False
    if GROUP_NAME_RE.search(nm):          # 众千金 / 记者们 / 逃散人群 / 众人
        return False
    if nm in GENERIC_ROLE_WORDS:          # 保镖 / 医生 / 拍卖师
        return False
    if nm.endswith(GENERIC_ROLE_TAILS):   # 容老夫人 / 黑衣手下 / 年轻医生 / 贺父
        return False
    if any(mk in nm for mk in DESCRIPTIVE_MARKERS):
        return False
    if INSTANCE_TAIL_RE.search(nm):       # 绑匪甲 / 围观者男
        return False
    return True


# ---------------------------------------------------------------------------
# 戏份统计（只用于「有具体人名」的角色）
# ---------------------------------------------------------------------------
def _block_cast(block: dict) -> set:
    """一个场次块的「出场人物」名单（已清洗，缩写已展开）。"""
    out = set()
    for line in block.get("body_lines", []):
        m = CHARACTER_LINE_RE.match(line.strip())
        if not m:
            continue
        out.update(split_cast_names(m.group(1)))
    return out


def _name_hits(cand: str, aliases: list) -> bool:
    """cand 是不是我们要找的那个人。

    只认「完全相同」或「两边都 ≥2 字的简称关系」，避免三类陷阱：
      · 子串：「甲」命中「绑匪甲」、「手下」命中「黑衣手下」
        （实测踩过：「甲」被算出 5 集 6 场 11 次提及）
      · 编号尾巴：「围观者甲」「围观者男」被当成「围观者」的简称
    真正的简称（「苏晚」=「苏晚意」）仍然认。
    """
    for a in aliases:
        if not a or not cand:
            continue
        if cand == a:
            return True
        if _abbr_match(a, cand) or _abbr_match(cand, a):
            return True
    return False


def _speaker_hits(block: dict, aliases: list) -> int:
    """这个块里该角色说了几句话（用来补名单没写全的群众角色）。"""
    n = 0
    for line in block.get("body_lines", []):
        s = line.strip()
        if s.startswith("△"):
            continue
        m = re.match(r"^([^：:]{1,20})[：:]", s)
        if not m:
            continue
        nm = _clean_name(m.group(1))
        if nm and _name_hits(nm, aliases):
            n += 1
    return n


def count_appearances(full_script: str, blocks: list, aliases: list) -> dict:
    """统计戏份：优先用「出场人物」名单 + 对白说话人，最后才退回全文提及。

    为什么不在全文字符串里裸数人名：
    「甲」「乙」「手下」「众人」「拍卖师」这类名字（或别名里混进了 OS/VO）
    会在整本里满世界命中，把一场戏的龙套算成几十集的重要角色。
    按场次名单统计则是「这个人真的出现在这一场里」的硬证据。
    """
    scenes, eps, mentions = set(), set(), 0
    for b in blocks or []:
        cast = _block_cast(b)
        spoke = _speaker_hits(b, aliases)
        on_cast = any(_name_hits(c, aliases) for c in cast)
        if not (on_cast or spoke):
            continue
        scenes.add(f"{b['episode']}--{b['scene_no']}")
        try:
            eps.add(int(b["episode"]))
        except (TypeError, ValueError):
            pass
        mentions += (1 if on_cast else 0) + spoke

    if not scenes:
        # 名单和对白里都没出现 —— 可能只在 △ 动作行被提到。
        # 退回全文计数，但只数长度 ≥2 的完整名字，单字名一律不参与。
        for b in blocks or []:
            for a in aliases:
                if len(a) >= 2:
                    mentions += b["body_text"].count(a)
        return {"scenes": -1, "episodes": 0, "mentions": mentions, "via": "正文提及"}

    return {"scenes": len(scenes), "episodes": len(eps), "mentions": mentions,
            "via": "出场名单/对白"}


# 有具体人名的角色，出场到这个量级才给 3 版
IMPORTANT_EPISODES = 3
IMPORTANT_SCENES = 5


def decide_versions(name: str, full_script: str, blocks: list = None,
                    aliases: list = None) -> tuple:
    """决定生成几版，返回 (版数, 理由)。

      · 没有具体人名（代称 / 称谓 / 群体 / 编号）→ 一律 1 版，不看场次
      · 有具体人名 → 出场 ≥3 集 或 ≥5 个场次 给 3 版，否则 1 版
    """
    if aliases is None:
        aliases = resolve_aliases(full_script, name)

    if not is_named_character(name):
        return 1, f"「{name}」没有具体人名（代称/称谓/群体/编号），一律 1 版"

    st = count_appearances(full_script, blocks, aliases)
    scenes, episodes, occ, via = st["scenes"], st["episodes"], st["mentions"], st["via"]

    if scenes < 0:
        if occ >= 30:
            return 3, f"未进名单，正文提及 {occ} 次，按重要角色处理"
        return 1, f"未进名单，正文提及 {occ} 次，1 版"
    if episodes >= IMPORTANT_EPISODES or scenes >= IMPORTANT_SCENES:
        return 3, f"出场 {episodes} 集 / {scenes} 个场次（{via}）"
    return 1, f"仅出场 {episodes} 集 / {scenes} 个场次（{via}），1 版"


def build_roster_report(names: list, full_script: str, blocks: list = None) -> str:
    """把名单 + 版数判定落成一份可读的表（也是给你手改后回喂 --character 的底稿）。"""
    rows = []
    total = 0
    for name in names:
        aliases = resolve_aliases(full_script, name)
        versions, reason = decide_versions(name, full_script, blocks, aliases=aliases)
        total += versions
        if not find_evidence_blocks(blocks or [], aliases) and not extract_character_bio(
                full_script, aliases):
            reason = "⚠ 剧本里找不到这个名字（出场名单/对白/正文都没有），请核对是否写错"
        rows.append((name, "有" if is_named_character(name) else "—",
                     "、".join(aliases[1:]) or "—", versions, reason))

    out = [f"人物名单（共 {len(names)} 人，合计 {total} 份提示词）",
           "口径：没有具体人名的（代称/称谓/群体/编号）一律 1 版；有具体人名的看场次，"
           f"出场 ≥{IMPORTANT_EPISODES} 集 或 ≥{IMPORTANT_SCENES} 个场次 给 3 版。",
           "",
           f"{'序':>3} {'人物':<10} {'人名':<4} {'别名':<28} {'版数':<5} 依据",
           "-" * 110]
    for i, (name, named, alias, versions, reason) in enumerate(rows, 1):
        out.append(f"{i:>3} {name:<10} {named:<4} {alias:<28} {str(versions) + ' 版':<5} {reason}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------
# 依据口径版本号。改动「system prompt 文本 / 固定外壳文本 / 解析方式」时必须一起改，
# 否则旧缓存（按老格式存的结构化字段）会被错误地喂给新模板。
CONTEXT_VERSION = "v12.3-eye-shape-fix"

# 人物出场依据最多取几个场次
MAX_EVIDENCE_BLOCKS = 6


def _sample_evenly(items: list, k: int) -> list:
    """沿剧情弧线均匀抽 k 个（含首尾）。

    「取前 k 个」的问题：跨 58 集的角色模型只看得到第 1~2 集的她，
    后面的服装、状态、身份变化一概看不到。均匀抽样让模型看到整条弧线。
    """
    n = len(items)
    if k <= 1:                 # k=1 时下面的 step 会除零
        return list(items[:1])
    if n <= k:
        return list(items)
    step = (n - 1) / (k - 1)
    idx = sorted({round(i * step) for i in range(k)})
    return [items[i] for i in idx]


def _character_evidence(block: dict) -> list:
    """从场次块里抽出「对人物有用的行」。

    保留：△ 动作行（外貌/动作/环境描写）、对白行（说话方式、别人怎么称呼他，
          是判断年龄/身份/气质的线索）、SFX / 屏幕字。
    剔除：纯英文译文行（实测占 45%，对推演外貌零价值）、分集标记、出场人物名单。
          中英混排行切掉英文尾巴，中文部分保留。
    """
    out = []
    for raw in block.get("body_lines", []):
        line = cut_english_tail(raw.strip())
        if not line:
            continue
        if EPISODE_MARK_RE.match(line):
            continue
        if not CJK_RE.search(line):
            continue
        if is_character_roster(line):
            continue
        out.append(line)
    return out


def find_evidence_blocks(blocks: list, aliases: list) -> list:
    """挑出该角色真正出场的场次块。

    优先「出场人物名单 + 对白说话人」——这是「他真的在这一场里」的硬证据；
    都没有时退回「正文提及」（只在 △ 动作行/旁白里被提到的角色）。
    两者皆空 = 剧本里没有这个人（大概率名字写错了）。
    """
    hits = [b for b in blocks or []
            if any(_name_hits(c, aliases) for c in _block_cast(b))
            or _speaker_hits(b, aliases)]
    if hits:
        return hits
    return [b for b in blocks or []
            if any(len(a) >= 2 and a in b.get("body_text", "") for a in aliases)]


def collect_character_context(full_script: str, blocks: list, aliases: list,
                              max_blocks: int = MAX_EVIDENCE_BLOCKS) -> tuple:
    """人物依据 = 人物小传 + 出场场次的剧本原文（沿弧线均匀抽 max_blocks 个）。

    返回 (依据文本, 命中场次数, 有没有小传)。
    命中场次为 0 且没有小传 = 剧本里根本没有这个人 —— 调用方必须停下、标出来，
    绝不能拿一段无关的剧本正文硬生成（那会让你花冤枉钱还拿到来路不明的图）。
    """
    parts = []

    bio = extract_character_bio(full_script, aliases)
    if bio:
        parts.append("【人物小传】\n" + bio)

    hit_count = 0
    if blocks:
        hits = find_evidence_blocks(blocks, aliases)
        hit_count = len(hits)
        if hits:
            picked = _sample_evenly(hits, max_blocks)
            # 同一处场景反复出现时，同一句描写会重复很多遍 —— 只留首次
            seen = set()
            chunks = []
            for b in picked:
                lines = []
                for ln in _character_evidence(b):
                    key = ln.strip()
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    lines.append(ln)
                if lines:
                    chunks.append(f"（第 {b['episode']} 集 {b['scene_no']} 场　{b['location']}）\n"
                                  + "\n".join(lines))
            if chunks:
                parts.append(
                    f"【出场场次】（共 {hit_count} 个，沿剧情弧线均匀抽取 {len(picked)} 个，"
                    f"覆盖第 {picked[0]['episode']} ~ {picked[-1]['episode']} 集）\n"
                    + "\n\n".join(chunks)
                )

    return "\n\n".join(parts), hit_count, bool(bio)


# 固定外壳（代码持有，所有人物 × 所有版本共用；模型不输出这部分）。
# 把每段「方案N：…」的人物描写夹在 PROLOGUE 与 EPILOGUE 之间即得一份完整提示词。
# 这样模型不必每次重写这些在所有人/版/调用之间完全相同的不变部分。
FIXED_PROLOGUE = (
    "四格人物设定参考图（横屏四宫格布局，1行4列横向排列）：正面、侧面、背面、脸部特写。"
    "白色背景，影棚布光，照片级写实风格，写实真人质感（真实的皮肤纹理），"
    "并给正面以及侧面的人物面部加上模糊特效，严禁在特写的人物正面视图中添加模糊特效，"
    "无任何字体出现。\n"
    # v12：反漂移从句压回一句。v11 在这里堆了四句（骨相落点 / 虹膜色系 / 高加索骨相 /
    # 不得画成东亚面孔），用户判断「限制太多反而让模型操作变形」⇒ 只留最短的一句，
    # 点名失败模式本身，不展开该怎么做。写法与族裔无关，写亚裔角色时同样成立。
    "严格按下文设定的性别与人种出图，不得自行把人种画成别的族裔。\n"
    "同一角色，"
)

FIXED_EPILOGUE = (
    "\n"
    "姿态：站姿全身像（一律站立、全身出镜），双手自然垂放于身体两侧，手中不拿任何物品；"
    "不出现轮椅、病床、坐姿、躺卧、拐杖等任何临时状态或伤病道具；"
    "头部保持中正平视，不歪头、不乱晃，站姿挺拔。\n"
    "表情：全程没有任何表情，面无表情，神情完全平静，无任何情绪波动。\n"
    "服装无任何文字、标志。\n"
    "四格排列：左侧第一格正面全身，面部加模糊特效；左起第二格侧面全身，面部加模糊特效；"
    "左起第三格背面全身，清晰；最右侧第四格脸部正面特写，清晰锐利，严禁模糊，面无表情。"
    "白色背景，影棚布光，照片级写实，写实真人质感，无任何文字、字母、数字、水印、标志。"
)

# 缓存指纹 = system prompt + 固定外壳 + 依据口径版本。
# ⚠️ 只哈希 system prompt 是不够的：FIXED_PROLOGUE / FIXED_EPILOGUE 也是产物的一部分
#    （每份提示词都由它们包起来）。改了外壳却忘了升 CONTEXT_VERSION，
#    旧缓存会被静默复用成「按旧外壳组装好的成品」—— 产物错了还看不出来。
#    定义位置必须在两个外壳之后。
CACHE_FINGERPRINT = hashlib.md5(
    (CHARACTER_SYSTEM_PROMPT + "|" + FIXED_PROLOGUE + "|" + FIXED_EPILOGUE + "|"
     + CONTEXT_VERSION).encode("utf-8")
).hexdigest()[:8]


# 模型输出的第一行：「固定设定：女性，白种人，22岁，欧洲贵族名媛，纤细苗条」
# 简称/别名也认（固定属性 / 基线 / 基础设定），模型偶尔会换个写法。
BASE_LINE_RE = re.compile(r"^\s*(?:固定设定|固定属性|基线|基础设定)\s*[:：]\s*(.+?)\s*$", re.M)

# 锚点里必须出现的性别词。缺了只报警，不改产物（同 find_similar_variants 的口径）。
BASE_GENDER_RE = re.compile(r"男|女|男性|女性|少年|少女")

# --- v11 族裔三重校验（全部只报警，不改产物）-----------------------------------
# 锚点里的族裔词。
BASE_RACE_RE = re.compile(r"白种|白人|高加索|欧裔|雅利安|亚裔|黄种|蒙古|黑人|非裔|拉美|拉丁|混血|棕色")
# 白人的骨相特征词（要求锚点里至少出现一个）。
BASE_BONE_RE = re.compile(r"眉骨|眼窝|骨相|高鼻|鼻梁|下颌|轮廓|立体|深目")
# 「白种人设定 + 这些词」⇒ 几乎必定画成亚洲脸（实测踩坑）。
ASIAN_FACE_CUE_RE = re.compile(
    r"双眼皮褶皱浅|双眼皮(?:很|较|偏)?浅|内双|单眼皮|眼型细长|细长眼|"
    r"眼距(?:偏|较|略)?宽|鼻头圆钝|鼻翼(?:偏|较|略)?宽|扁平眉骨|眉骨平|"
    r"面部扁平|脸部扁平|面部线条扁平|脸型扁平")
# 深色虹膜。白种人设定里出现就是最高危信号（南欧/地中海裔例外，那种剧本会明写）。
DARK_IRIS_RE = re.compile(r"深棕|深褐|深黑|墨色|漆黑|黑褐|深咖|近乎黑|接近黑")

# ⚠️ 深色词要分清楚说的是「瞳」还是别的 —— 实测误报两类：
#   ①「发型是齐肩的**深棕色**微卷发」「脚踩**深棕色**短靴」——那是发色/鞋色；
#   ②「瞳色是罕见的浅灰绿，在暗处几乎呈**墨色**」——明明写了浅色瞳，深色只是修辞。
IRIS_CTX_RE = re.compile(r"瞳|眸|虹膜|眼睛|眼珠|眼神")
HAIR_CTX_RE = re.compile(r"发|鬓|眉")
# 明显在说衣物/配饰的颜色。
# ⚠️ 2026-09-24 补：实测漏报过的两种「不是眼睛」的深色 ——
#   ①「手腕上一只**深棕色**鳄鱼皮表带的正装腕表」⇒ 必须补「腕表/手表/表带/钟表」；
#   ② 别只写一个「表」字：那会把「**表**情」也当成衣物语境，把真的深色瞳漏放行。
GARMENT_CTX_RE = re.compile(
    r"鞋|靴|衣|裤|裙|衫|外套|大衣|西装|夹克|包|皮带|腰带|帽|手套|袜|皮革|面料|"
    r"腕表|手表|表带|钟表|怀表|眼镜|镜框")
# 窗口里已写明是「浅色瞳」⇒ 同窗口的深色词按修辞处理。
LIGHT_IRIS_RE = re.compile(r"浅灰蓝|灰蓝|蓝灰|灰绿|绿灰|浅蓝|淡蓝|蓝绿|浅绿|浅褐|淡褐|琥珀|碧|浅灰|冷灰")


def _dark_iris_hits(body: str) -> list:
    """挑出**确实在说眼睛**的深色词。

    落在头发/眉毛/衣物语境里的不算；窗口里已经写明是浅色瞳的（深色只是修辞）也不算。
    语境仍看不出来的，按保守处理报出来 —— 宁可多报，也不要漏掉真的亚洲脸信号。
    """
    hits = []
    for m in DARK_IRIS_RE.finditer(body):
        win = body[max(0, m.start() - 16): m.end() + 16]
        # ⚠️ 三步的**顺序**都是踩过坑定的：
        #   ① 窗口里已写明浅色瞳（「瞳色是罕见的**浅灰绿**，在暗处几乎呈墨色」）
        #      ⇒ 深色词是修辞，放行。必须独立成立：那句的「瞳」字可能落在窗口外。
        #   ② 再看是不是在说眼睛。若反过来先看「发/眉」，
        #      「眉尾稍挑，眼睛是**深褐**色的杏眼」会因为窗口里有个「眉」字被误放行。
        #   ③ 没提眼睛、且明显在说头发/眉毛/衣物 ⇒ 放行。
        if LIGHT_IRIS_RE.search(win):
            continue
        if IRIS_CTX_RE.search(win):
            hits.append(m.group(0))
        elif HAIR_CTX_RE.search(win) or GARMENT_CTX_RE.search(win):
            continue
        else:
            hits.append(m.group(0))           # 看不出在说哪儿 ⇒ 宁可报警
    return sorted(set(hits))


def check_race_consistency(base: str, body: str, race_is_white: bool) -> dict:
    """检查「锚点人种 ⇄ 正文五官」是否自洽。只返回结论，不改文本。

    动机：白种人设定下正文写「双眼皮褶皱浅 + 瞳色偏深棕」曾经真的出成亚洲脸，
    而模型自己看不出问题 —— 所以在代码侧留一道可观测的疤，跑到最后汇总点名，
    而不是等人看图才发现。

    返回 {"race_ok", "bone_ok", "asian_cues", "dark_iris"}。
    v12 起 `bone_ok` 只作观测、不再告警（「锚点必带骨相词」这条硬性要求已取消）；
    `race_ok` 与 `asian_cues` / `dark_iris` 仍在主流程末尾点名，但**只在判定为白人时**
    才要求/上报 —— 剧本明写亚裔的角色写成「单眼皮、眼型细长」是**对的**，不能报警。
    """
    base = base or ""
    body = body or ""
    plain = base.replace("（", "(").replace("）", ")")
    race_ok = bool(BASE_RACE_RE.search(plain)) if race_is_white else True
    bone_ok = bool(BASE_BONE_RE.search(plain)) if race_is_white else True
    return {
        "race_ok": race_ok,
        "bone_ok": bone_ok,
        # ⚠️ v12 修正：`asian_cues` 之前是无条件上报的，会把剧本明写的亚裔角色（单眼皮/细长眼）
        #    当成「漂移风险」误报。和其他三项一样，只有判定为白人时才查。
        "asian_cues": (sorted(set(ASIAN_FACE_CUE_RE.findall(body)))
                       if race_is_white else []),
        "dark_iris": _dark_iris_hits(body) if race_is_white else [],
    }


# 白种人角色的判定口径：脚本一进来就只知道「本剧本默认欧美」，
# 但个别角色可能被模型改判成别的族裔，所以这里只做**保守**判断 ——
# 锚点里出现"白/高加索/欧裔"才算白人，看不出来就不校验（避免误报）。
WHITE_RACE_RE = re.compile(r"白种|白人|高加索|欧裔|雅利安")


def parse_base_line(text: str) -> str:
    """抽出「固定设定」那一行。

    这一行是**同一角色三版共用的锚点**：性别/人种/年龄感/气质/体型在这里定死一次，
    代码把它接到每版描写开头 —— 从结构上保证三版不会各写各的人种、性别。
    用户口径：一开始就把人种（v10 起还有性别）定好，其余让模型自由发挥。
    """
    m = BASE_LINE_RE.search(str(text))
    return m.group(1).strip() if m else ""


def assemble_prompt(base: str, body: str) -> str:
    """把「固定设定（锚点）+ 该版描写」塞进固定外壳，拼成一份完整提示词。

    base 是模型给的一行固定设定，body 是该版 200~400 字的完整人物描写。
    结果形如：`…同一角色，白种人，22岁，欧洲贵族名媛，纤细苗条。鹅蛋脸，…`
    """
    body = (body or "").strip()
    anchor = (base or "").strip()
    if anchor and anchor[-1] not in "。，；：,.;:":
        anchor += "。"
    if not body:
        body = "（人物描写缺失）"
    return FIXED_PROLOGUE + anchor + body + FIXED_EPILOGUE


# 两版相似度到这条线就报警。模型偶尔会偷懒把三段写成近似文字 ——
# 那样你会拿到几张几乎一样的图，白跑一轮。只报警、不改写产物。
VARIANT_SIMILARITY_THRESHOLD = 0.9


def find_similar_variants(bodies: list,
                          threshold: float = VARIANT_SIMILARITY_THRESHOLD) -> list:
    """找出过于相似的版本对，返回 [(方案序号 a, 方案序号 b, 相似度), ...]。"""
    pairs = []
    for i in range(len(bodies)):
        for j in range(i + 1, len(bodies)):
            ratio = difflib.SequenceMatcher(None, bodies[i], bodies[j]).ratio()
            if ratio >= threshold:
                pairs.append((i + 1, j + 1, round(ratio, 3)))
    return pairs


def build_character_user_prompt(name: str, script_context: str,
                                special_requirements: str = "",
                                has_bio: bool = False,
                                version_count: int = 1) -> str:
    if has_bio:
        basis = ("该人物有小传：**性别**、人种、年龄、气质、体型一律以【人物小传】为主、剧情场次为辅"
                 "（性别尤其**先看小传**，小传明写就以小传为准；小传没写的细节才从剧情里推）。")
    else:
        basis = ("该人物**没有小传**：性别请从下面的剧情场次、称谓与人物关系中推演，"
                 "再定人种、年龄、气质与体型。")

    prompt = f"""人物姓名：{name}

相关剧本内容：
{script_context}

外貌依据优先级：{basis}
"""
    if special_requirements:
        prompt += f"\n特殊修改要求：{special_requirements}\n"
    if version_count > 1:
        seg_req = "再输出 3 段（每段用对应中文「方案一/二/三：」开头）"
    else:
        # 单版角色：原来这里也写「每段用对应的中文『方案一/二/三：』开头」，
        # 模型读到「一/二/三」就当成 3 段全写了 —— 2026-09-24 实测 **46/46 个单版角色
        # 全都多吐了「方案二：」「方案三：」**，docx 里就成了「唯一方案」下面挂着三份
        # 互不相干的描写，下游填图脚本整块当一条提示词用（里面还带着「方案二：」这几个字）。
        seg_req = ("再**只输出 1 段**（以「方案一：」开头）；"
                   "写完这一段就结束，**严禁再写「方案二：」「方案三：」**")
    prompt += (
        "\n请先写一行「固定设定：性别，人种，年龄感，气质阶层，体型」，"
        f"{seg_req}"
        "完整、连贯的人物描写（脸型五官/肤色/发型/服装/配饰等细节均可写）；"
        "每版都要独立完整、单独可出图，不要写「同上」这类相对上一版的写法，"
        "三版之间能一眼看出差别就行。"
        "通用规则（横屏布局/布光/模糊/姿态/表情/标志/四格排列）由代码补充，不要重复；"
        "姿态与手部动作、表情情绪、叙事化比喻、自我解释也都不要写 —— 外壳已经把它们写死了。"
    )
    return prompt


# 多版标注：方案一/二/三：…、版本一/二/三：…
LEGACY_VARIANT_RE = re.compile(r"(?:方\s*案|版\s*本)\s*[一二三123]\s*[:：]")


def _split_variants_by_marker(text: str) -> list:
    """按「方案一/二/三：」「版本一/二/三：」标注切多版。

    主路径：模型老老实实按【方案N：…】开头写出 N 段。这里只切，不动内容。
    兜底：模型忘了标注时，按空行切（每段必须 ≥30 字才认，避免把单行连成一片）。
    """
    raw = str(text)
    parts = [p.strip() for p in LEGACY_VARIANT_RE.split(raw)]
    parts = [p for p in parts if len(p) >= 30]
    if len(parts) >= 2:
        return parts
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if len(c.strip()) >= 30]
    return chunks if len(chunks) >= 2 else parts


def _strip_marker(body: str) -> str:
    """剥掉段首的「方案一：」之类标注，让描写本身从首字开始。"""
    return re.sub(r"^\s*(?:方\s*案|版\s*本)\s*[一二三123]\s*[:：]\s*", "", body).strip()


# 模型偶尔会把自己的**取舍过程**写进正文。实测原样出现在产物里的那句：
#     「…左腕戴一只腕表，右手握着手杖——不，手杖只是装饰性道具，此处不写。双手交叠在身前…」
# 这种自我备注对出图毫无用处，还会让画图模型把「不写」当成内容去理解 ⇒ 直接删掉。
# 只在行内匹配（不跨句），命中不了正常描写里的「不必写实」这类词。
META_NOTE_RE = re.compile(
    r"[^。；\n]*(?:此处不写|此处省略|不再赘述|不予体现|不作描写|不作描述|略去不写)[^。；\n]*[。；]?")


def _clean_meta_notes(body: str, name: str = "") -> str:
    """删掉正文里模型夹带的自我备注。只删命中那一句，不动别的。"""
    cleaned, n = META_NOTE_RE.subn("", str(body))
    if n:
        print(f"    [警告] {name} 的正文里夹带了模型的自我备注（「此处不写」之类），已删除 {n} 处")
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _script_signature(full_script: str) -> str:
    """剧本内容的短指纹 —— 塞进人物缓存键，防止「换了剧本却命中旧缓存」。

    场景缓存键里带场次编号，天然区分不同剧本；人物键没有编号，所以必须靠这个指纹：
    先用只有 1~5 集的 txt 跑过一轮，再换全本 docx 跑，旧缓存**不会**命中
    （否则会拿前 5 集依据的产物当全本成品交付）。
    """
    return hashlib.md5(str(full_script).encode("utf-8")).hexdigest()[:10]


def generate_character_prompts(name: str, full_script: str, blocks: list = None,
                               special_requirements: str = "",
                               cache_dir: str = None, use_cache: bool = True) -> dict:
    aliases = resolve_aliases(full_script, name)
    # 指纹跟着 system prompt + 依据口径走；剧本指纹跟着剧本内容走
    cache_key = (f"character::{name}::{special_requirements}::"
                 f"{_script_signature(full_script)}::{CACHE_FINGERPRINT}")

    if use_cache:
        cached = load_cache(cache_dir, cache_key)
        if cached:
            print(f"  命中缓存：{name}")
            return cached

    named = is_named_character(name)
    script_context, hit_count, has_bio = collect_character_context(
        full_script, blocks, aliases)

    if hit_count == 0 and not has_bio:
        # 剧本的出场名单/对白/正文里都找不到这个人 —— 大概率名字写错了。
        # 老版本会拿剧本开头 3000 字糊弄过去：照样调 API、照样进 docx，
        # 你拿到一张来路不明的图还不知道。现在直接停下、标出来让你核对，一分钱不花。
        print(f"    [未匹配] 剧本里找不到「{name}」—— 已跳过，不调 API。请核对名字写法")
        return {
            "name": name, "aliases": aliases, "named": named,
            "status": "unmatched", "role_label": "未匹配",
            "reason": "剧本的出场名单 / 对白 / 正文里都找不到这个名字",
            "version_count": 0, "scene_hits": 0, "has_bio": False,
            "degraded": False, "base": "", "similar_pairs": [],
            "bodies": [], "prompts": [],
        }

    version_count, reason = decide_versions(name, full_script, blocks, aliases=aliases)

    user_prompt = build_character_user_prompt(
        name, script_context, special_requirements,
        has_bio=has_bio, version_count=version_count,
    )

    # 模型输出 1 行固定设定 + N 段（约 200-400 字/段）；max_tokens 给充足余量
    max_tokens = 3000 if version_count > 1 else 1500
    text = call_ds_api(CHARACTER_SYSTEM_PROMPT, user_prompt,
                       temperature=0.7, max_tokens=max_tokens)

    # 固定设定那一行先摘出来，否则它会被当成「方案一」前面的一版，把后面全部错位
    base = parse_base_line(text)
    text_body = BASE_LINE_RE.sub("", str(text), count=1)
    # 夹带的自我备注（「…此处不写。」）先清掉，两个分支都受益
    text_body = _clean_meta_notes(text_body, name)

    if version_count > 1:
        bodies_raw = _split_variants_by_marker(text_body)
        bodies = [_strip_marker(b) for b in bodies_raw[:version_count]]
        prompts = [assemble_prompt(base, b) for b in bodies]
        if len(prompts) < version_count:
            print(f"    [警告] {name} 只解析出 {len(prompts)} 版"
                  f"（期望 {version_count} 版），已原样保留、不复制充数，建议重跑")
            if not prompts:
                # 模型输出空到连分隔都切不出来 —— 原样保整段
                bodies = [text_body.strip()]
                prompts = [assemble_prompt(base, text_body.strip())]
        degraded = len(prompts) < version_count
        role_label = "重要角色"
    else:
        # 单版：剥掉可能的「方案N：」前缀。
        # ⚠️ 只在**真的出现「方案N：」标注**时才切。不能无脑按空行切 —— 模型老老实实
        #    写了一段、只是段中空了一行的话，无脑切会把这张脸腰斩，只剩半张。
        #    （2026-09-24 实测：46 个单版角色 100% 都多写了「方案二：」「方案三：」，
        #      原实现只剥最前面那个「方案一：」，后两段原样进了 docx 和画布节点。）
        if LEGACY_VARIANT_RE.search(text_body):
            parts = _split_variants_by_marker(text_body)
            # ⚠️ 命中「方案一：」是**正常**的（模型本来就该这么开头），切出来就是 1 段 ——
            #    只有真切出 >1 段才是"多写了"。一开始没加这个判断，46 个单版角色每人
            #    刷一行「模型却写了 1 段」，把日志淹了。
            if len(parts) > 1:
                print(f"    [警告] {name} 是单版角色，模型却写了 {len(parts)} 段"
                      f"—— 只保留第 1 段，其余丢弃")
            text_body = parts[0]
        stripped = _strip_marker(text_body.strip())
        bodies = [stripped]
        prompts = [assemble_prompt(base, stripped)]
        degraded = False
        role_label = "次要角色" if named else "代称角色"

    similar_pairs = find_similar_variants(bodies) if len(bodies) > 1 else []
    for a, b, ratio in similar_pairs:
        print(f"    [警告] {name} 的方案{a} 与 方案{b} 过于相似（相似度 {ratio}）——"
              f"可能拿到几张几乎一样的图，建议 --no-cache 重跑")

    # v10：锚点里必须带性别词（性别/人种/年龄/气质/体型 五件套）。
    # 模型漏写时只报警、不改产物 —— 少了性别，某一版恰好全用中性词就会被画成反性别
    # （实测：苏晚意某版出成男性面孔）。
    gender_ok = bool(BASE_GENDER_RE.search(base))
    if not gender_ok:
        print(f"    [警告] {name} 的「固定设定」锚点里没有性别词"
              f"（当前：{base or '空'}）——跨性别画错的风险很高，建议 --no-cache 重跑")

    # 族裔自洽性校验。动机：锚点写了「白种人」，正文却写「双眼皮褶皱浅 + 瞳色偏深棕」，
    # 出图就是一张亚洲脸 —— 模型自己看不出问题，所以在代码侧留一道可观测的疤，
    # 跑到最后汇总点名。全部只报警、不改产物（v12 起不再校验锚点骨相词）。
    race_is_white = bool(WHITE_RACE_RE.search(base))
    race_report = check_race_consistency(base, "\n".join(bodies), race_is_white)
    # v12：只留「真出现漂移信号」的两条告警。`bone_ok`（锚点有没有骨相词）不再报 ——
    # v12 已取消「锚点必带骨相词」的硬性要求，再报就是噪声。
    if race_is_white and not race_report["race_ok"]:
        print(f"    [警告] {name} 的锚点里没有族裔词（当前：{base or '空'}）")
    if race_report["asian_cues"]:
        print(f"    [警告] {name} 的正文里出现容易画成东亚面孔的词 "
              f"{race_report['asian_cues']} —— 出图前留意一眼")
    if race_report["dark_iris"]:
        print(f"    [警告] {name} 是白种人设定，正文里却写了深色瞳 "
              f"{race_report['dark_iris']} —— 白种人瞳色通常偏浅，出图前留意一眼")

    item = {
        "name": name,
        "aliases": aliases,
        "named": named,
        "status": "ok",
        "role_label": role_label,
        "reason": reason,
        "version_count": version_count,
        "scene_hits": hit_count,
        "has_bio": has_bio,
        "degraded": degraded,
        "base": base,
        "gender_ok": gender_ok,
        "race_is_white": race_is_white,
        "race_ok": race_report["race_ok"],
        "bone_ok": race_report["bone_ok"],
        "asian_cues": race_report["asian_cues"],
        "dark_iris": race_report["dark_iris"],
        "similar_pairs": similar_pairs,
        "bodies": bodies[:version_count],
        "prompts": prompts[:version_count],
    }
    if use_cache:
        save_cache(cache_dir, cache_key, item)
    return item


def generate_multiple_characters(names: list, full_script: str, blocks: list = None,
                                 cache_dir: str = None, use_cache: bool = True,
                                 jobs: int = None) -> list:
    """逐人生成。默认串行（一个人物生成完才开始下一个 —— 用户明确要求，
    防止短时高并发触发反代 API 的风控/封禁）；要并发时传 jobs=N。

    并发时每个人物只读自己的依据、只写自己的缓存，结果按 index 写回，
    产物与串行逐个跑完全一致（场景侧同款并发模式）。
    限速由 ds_api 的滑动窗口统一管，并发不会突破速率限制。
    """
    total = len(names)
    results = [None] * total
    jobs = max(1, int(jobs or 1))

    def _one(i, nm):
        print(f"\n正在处理人物 {i + 1}/{total}: {nm}")
        try:
            r = generate_character_prompts(nm, full_script, blocks,
                                           cache_dir=cache_dir, use_cache=use_cache)
        except Exception as e:
            # 一个人失败不该拖垮整批 —— 63 人的名单跑到第 40 个被一次超时/限流打断，
            # 前面 39 个人白等、后面 23 个人没机会跑。改成标记失败、继续往下走。
            # （已跑完的人都在缓存里，重跑会自动跳过。）
            print(f"    [失败] {nm} 生成出错（{type(e).__name__}: {e}）—— 已跳过，继续后面的人物")
            return {
                "name": nm, "aliases": [nm], "named": is_named_character(nm),
                "status": "error", "role_label": "生成失败", "reason": str(e),
                "version_count": 0, "scene_hits": 0, "has_bio": False,
                "degraded": False, "base": "", "similar_pairs": [],
                "bodies": [], "prompts": [],
            }
        print(f"  {r['role_label']}，{r['reason']}，生成 {r['version_count']} 版")
        return r

    if jobs <= 1 or total <= 1:
        # 串行：带进度条（几十号人排队跑，得知道还要多久）
        started = time.time()
        for i, nm in enumerate(names):
            results[i] = _one(i, nm)
            print(f"  {progress_line(i + 1, total, started, nm)}")
        print(f"\n全部完成：{total} 个人物，总用时 {format_duration(time.time() - started)}")
        return results

    workers = min(jobs, total)
    print(f"{total} 个人物待生成，{workers} 路并发")
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(_one, i, nm): i for i, nm in enumerate(names)}
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    except BaseException:
        # 已跑完的人物都已落盘，重跑会直接跳过 —— 不浪费已经花掉的钱
        pool.shutdown(wait=False, cancel_futures=True)
        print("\n  [中断] 已完成的人物已写入缓存，重新运行会自动跳过它们")
        raise
    pool.shutdown(wait=True)
    return results
