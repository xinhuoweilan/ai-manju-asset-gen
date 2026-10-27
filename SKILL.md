---
name: ai-manju-asset-gen
description: AI 漫剧「资产出图 + 衍生图」专项技能。用户只报场景名字（"出 A、B、C 三个场景的资产图"）即可跑完整条链路：读剧本抽取场景依据 → 生成提示词（每景 3 版）→ 画布建节点 → 报估价确认 → 出图 → 下载归档 → 上传到平台资产位；并支持「按批注改图重出」与「衍生图」（反打视角图 / 人物换装）。所有账号、密钥、路径均从 .env 读取，不写死在脚本里；两道人工闸（报数量 / 报估价）必须保留。触发语：出资产图、出这几个场景、按批注改一下再出、出反打图、人物换装、变装。
agent_created: true
---

# AI 漫剧 · 资产出图 & 衍生图

> **一句话**：你报场景名 → 出资产图 → 按批注改图 → 出衍生图（反打 / 换装）→ 归档 + 上传平台。
> **适用范围**：只处理场景 / 人物的资产图与衍生图。

---

## 〇、安装与配置（第一次用必读）

```bash
pip install -r requirements.txt
cp .env.example .env      # 然后按 API-清单.md 逐项填写
python scripts/check_env.py   # 自检：配置是否齐全、token 是否能通
```

脚本统一从 **`.env`** 读配置，**不改脚本**就能换项目、换账号、换目录。
变量清单与获取方式见 `API-清单.md`。

---

## 一、触发语

| 你说 | 走哪段 |
|---|---|
| 「出 A、B、C 三个场景的资产图」/「XX、YY 出图」 | §三 A |
| 「这几张按批注改一下再出」/「这个上面改」/「这三张都要改」 | §四 B |
| 「这批出反打图」/「XX、YY 出反打」 | §五 C-1 |
| 「XX 换成这套衣服」/「变装」 | §五 C-2 |

**输入粒度**：只认用户报的场景名（文字或截图清单）。不要自作主张扩成「第 N 集全部场景」，除非用户明说。

---

## 二、四条铁律

1. 🚦 **两道闸绝不越过**：① 提示词生成完 → 报「本次 N 个场景 × 3 版」等确认；② 节点填完 → 报估价等确认。
   **用户没说"出"，一个付费接口都不碰。**
2. 🚦 **任何 retry 都要问**（网络抖动 / 5xx / 401 一律不自动重试）。付费接口无幂等键，重试可能双倍扣费。
3. **只删本次该删的**：删素材前先读该位基线、把「改前 N 条」报出来；「三张全改删三张、一张改删一张」，不顺手扩大。
4. **素材目录只放 png**（不落元数据 json）；中间稿落 `tmp_<剧名>_<批次>/`。

---

## 三、A · 出资产图

| 步 | 动作 | 命令 |
|---|---|---|
| A1 | 把用户报的场景名落成清单 `scenes_<剧名>_<批次>.txt`（`场景名<TAB>场次号`） | — |
| A2 | **前置校验**：剧本 docx 存在、`.env` 里的项目/画布可读、token 有效 | `python scripts/check_env.py` |
| A3 | 抽依据（零成本核对每个场景能否对上剧本） | `python scripts/tools/scene_from_list.py --input <docx> --scenes <清单> --dry-run` |
| A4 | 生成提示词（每景 3 版） | 去掉 `--dry-run`，加 `--label <批次名>` |
| A5 | 🚦 **闸 A**：报「N 个场景 × 3 版 = 3N 张」+ 清单，等确认 | — |
| A6 | 建画布节点 + 写提示词（一列一场景、方案竖排） | `python scripts/core/asset/append_versions.py --kind scene --names "A,B" --from-docx <docx> --commit` |
| A7 | 🚦 **闸 B**：只跑估价（不加 `--commit`）报给用户 | `python scripts/core/submit_canvas2.py --node "A-方案1" ...` |
| A8 | 出图 → 下载归档 | 加 `--commit --max-spend N` 提交；`python scripts/core/asset/finish.py` 下载 |

**命名约定**：节点名 `<场景名>-方案1/2/3`（续号不覆盖，绝不删旧节点）。

---

## 四、B · 按批注改图重出

**核心口径**：批注说「这一张 / 这个上面改 / 这个基础上改」→ **只改那一张**；说「这三张 / 都」→ **三版都改**。

| 步 | 动作 | 命令 |
|---|---|---|
| B1 | 把批注落成 `revise_<剧名>_<序号>.txt`（`场景名<TAB>场次<TAB>修改要求`） | — |
| B2 | 定向重出提示词（只注入修改要求，其余复用缓存） | `python scripts/tools/scene_revise.py --input <docx> --scenes <清单> --label 返工01 --jobs 3` |
| B3 | 🚦 报「改哪几版、各改什么」等确认 | — |
| B4 | **改原节点提示词 + 重提（覆盖，不新建方案号）** | `python scripts/update_node_prompt.py --node "A-方案1" --from-docx <docx> --commit` |
| B5 | 重出后覆盖下载 | `python scripts/core/asset/finish.py` |

⚠️ **改节点数据时 `imageList` 与 `mixedList` 必须写同一份**——只写 `imageList` 会被平台在后续更新时清空，参考图静默丢失（表现为出图和参考图毫无关系）。

---

## 五、C · 衍生图

### C-1 反打图（场景反向视角）

```bash
python scripts/core/deriv/fanda.py --drama <剧名> --plan --scenes "A,B"          # 只读清单
python scripts/core/deriv/fanda.py --drama <剧名> --nodes --scenes "A,B"         # 建节点（免费）
python scripts/core/deriv/fanda.py --drama <剧名> --commit --yes --scenes "A,B"  # 出图（付费）
python scripts/core/deriv/fanda.py --drama <剧名> --wait --scenes "A,B"          # 归档
```

- **取图**：该场景**定稿区**那张（`makeupStatus==0`）作为 `@图片1`
- **锚定点**（存 `fanda_scenes.json`）：**3~20 字，只写「什么在左/在右/朝哪」+ 门规则结论**；
  **严禁写材质 / 光影 / 机位 / 景别**——写长了模型会照抄正视图，反打就白做了
- **门规则**：正视图出现过的门，反打不再出现；小空间必现 / 室外无门 / 大空间不强制；
  正视图机位在门边朝内 ⇒ 反打时门正对镜头（画面正中）
- **归档**：`<ASSET_ROOT>/<剧名>/资产图/反打图/第N次提交/`

### C-2 人物换装

```bash
python scripts/tools/costume_plan.py --input <docx>        # 产出「角色 × 造型套 × 场次」明细
python scripts/core/deriv/costume.py --plan ... --commit --yes  # 按确认结果出图
```

- 参考 = 该角色**定妆图** + 服装描述；命名 **`人名-服装名`**；标 `appearanceEpisode`（覆盖哪几集）
- 定妆基线保持 `makupStatus=0`，各套造型挂 `makupStatus=2`（变体）

---

## 六、D · 收尾（归档 → 上传平台）

**「上传平台」= 把出的图传回平台的资产页**，挂到对应场景/角色名下（成为该位一个素材）。

| 步 | 动作 | 命令 |
|---|---|---|
| D1 | 归档（按批次分子目录） | `finish.py` / `fanda.py --wait` |
| D2 | 上传前 **dry-run 看匹配**（名字三级匹配；不唯一一律停） | `python scripts/core/asset/bind_asset.py --dir <目录>` |
| D3 | 真上传 | 同上 `--commit` |
| D4 | **删被替换的旧图**（删前读基线、报条数） | `python scripts/core/asset/asset_delete.py --scene "<名>" --asset-id <id> --commit` |
| D5 | 额外版本归「变体」（用户要才做） | `python scripts/core/set_variant.py --scenes "A,B" --commit --max-changes N` |

---

## 七、异常处理

| 症状 | 处理 |
|---|---|
| 接口 401 / 集体失败 | token 过期 → 重新登录拿 token 写回 `.env`，续期后**只补失败的** |
| 出图失败（status=4） | 不自动重试；报用户，同意后只重提失败节点 |
| 风格跑飞 / 和参考图无关 | **先查参考图是否丢失**（节点 `imageList` / `mixedList` 任一为空即丢），别急着改提示词 |
| 上传匹配失败 | 用 `--file <图> --target "<平台里的准确名>"` 显式指路，dry-run 确认「命中方式=精确」再 `--commit` |

---

## 八、命令速查

```bash
python scripts/check_env.py                                        # 配置自检
python scripts/tools/scene_from_list.py --input <docx> --scenes <txt> [--dry-run] [--label 批次]
python scripts/tools/scene_revise.py --input <docx> --scenes <txt> --label 返工01 [--jobs 3]
python scripts/core/asset/append_versions.py --kind scene --names "A,B" --from-docx <docx> --commit
python scripts/core/submit_canvas2.py --node "A-方案1" --commit --max-spend N
python scripts/core/asset/finish.py
python scripts/core/asset/bind_asset.py --dir <目录> [--commit]
python scripts/core/asset/asset_delete.py --scene "<名>" --asset-id <id> [--commit]
python scripts/core/set_variant.py --scenes "A,B" --commit --max-changes N
python scripts/core/deriv/fanda.py --drama <剧名> --plan|--nodes|--commit --yes|--wait --scenes "A,B"
python scripts/tools/costume_plan.py --input <docx> && python scripts/core/deriv/costume.py ...
```
