# ai-manju-asset-gen

AI 漫剧的**资产出图 + 衍生图**工具包：给一批场景名，就能从剧本自动抽取依据、生成提示词、在画布上批量出图、按批注返工重出，并基于定稿图生成反打视角图或人物换装图。

> 面向：需要批量生产漫剧资产（场景图 / 人物形象图 / 反向视角图）的团队。
> 形态：一个 **Skill**（给 AI 助手读的作业流程）+ 一组**命令行脚本**（实际干活）。

---

## 它能做什么

| 能力 | 输入 | 输出 |
|---|---|---|
| **出资产图** | 剧本 docx + 场景名清单 | 每个场景 3 版提示词 → 画布批量出图 → 归档 PNG |
| **按批注返工** | 批注（哪一张 + 改什么） | 只改要求、其余复用，重出后覆盖 |
| **反打图** | 场景定稿图 | 同空间的反向视角图（用于正反打剪辑） |
| **人物换装** | 角色定妆图 + 服装描述 | 各集造型图，按「人名-服装名」命名 |

---

## 快速开始

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 填配置（照 API-清单.md 逐项拿）
cp .env.example .env

# 3. 自检
python scripts/check_env.py

# 4. 第一次出图（先 dry-run 看依据能不能对上）
python scripts/tools/scene_from_list.py --input 剧本.docx --scenes scenes_我的剧_01.txt --dry-run
```

`scenes_我的剧_01.txt` 的格式（`场景名<TAB>场次号`）：

```
公爵塔楼卧房	5-1,5-3
城镇裁缝铺内厅	9-2
```

---

## 作业流程（简版）

```
你报场景名
   → 抽依据（dry-run 核对）
   → 出提示词（每景 3 版）
   → ★ 闸 A：报「N 场景 × 3 版」等你确认
   → 建画布节点（免费）
   → ★ 闸 B：报估价等你确认
   → 出图 → 下载归档
   → 上传平台（dry-run 匹配 → 确认 → 上传）
```

**两道闸的目的**：批量出图按张计费，节点建错了可以免费改，图出错了就要重花钱。所以「建节点」和「出图」之间一定要停一次。

---

## 目录结构

```
ai-manju-asset-gen/
├─ SKILL.md                 # 作业流程（给 AI 助手读）
├─ README.md                # 你正在看的这份
├─ API-清单.md              # 需要填的账号 / 密钥 / 路径清单
├─ .env.example             # 配置模板
├─ requirements.txt
└─ scripts/
   ├─ paths.py              ← 统一配置入口（所有脚本从它取地址/凭证/目录）
   ├─ check_env.py          配置自检
   ├─ update_node_prompt.py 按批注改原节点提示词并重提
   ├─ core/                 画布与平台操作
   │  ├─ mc.py              平台接口客户端
   │  ├─ submit_canvas2.py  估价 / 提交出图
   │  ├─ set_variant.py     设为变体
   │  ├─ asset/
   │  │  ├─ append_versions.py  建节点
   │  │  ├─ finish.py           等出图 + 下载归档
   │  │  ├─ bind_asset.py       上传到平台资产位
   │  │  ├─ asset_delete.py     删除素材
   │  │  └─ mc_platform.py      只读状态查询
   │  └─ deriv/
   │     ├─ fanda.py        反打图
   │     └─ costume.py      人物换装
   ├─ tools/                提示词侧
   │  ├─ scene_from_list.py 按场景清单出提示词
   │  ├─ scene_revise.py    按批注定向重出
   │  ├─ scene_frontface.py docx 提示词读取
   │  └─ costume_plan.py    换装规划
   └─ src/                  提示词模型调用与文档解析
```

---

## 常见问题

**Q：一定要用某个平台吗？**
脚本面向的是「项目 → 画布 → 素材位」这一套结构。换平台需要改 `scripts/mc.py` 里的接口地址与字段，其余流程不变。

**Q：出图多少钱？**
按张计费，脚本每次提交前都会打印估价并等你确认；`--max-spend` 是硬上限。

**Q：为什么反打图老是像正视图？**
两个高危点：① 参考图没绑上（节点 `imageList` / `mixedList` 必须同时写）；② 锚定点写太长（应 3~20 字、只写左右朝向，不写材质光影）。

**Q：改了脚本会影响别的项目吗？**
不会。所有项目级参数（项目 ID、画布 ID、素材根目录、密钥）都在 `.env` 里，一个文件对应一个项目。
