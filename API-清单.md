# API / 配置清单

> 把这份清单填进 `.env`（从 `.env.example` 复制一份）。**只填自己那一份，不要提交到 git**。
> 填完跑 `python scripts/check_env.py` 自检，全绿再开工。

---

## 一、平台凭证（必需）

脚本的一切画布操作都靠这四项。

| 配置项 | 变量名 | 怎么拿 | 示例 |
|---|---|---|---|
| 平台地址 | `PLATFORM_BASE_URL` | 浏览器里打开平台首页，复制域名 | `https://www.example-platform.com` |
| 登录 Token | `PLATFORM_TOKEN` | 登录后按 `F12` → **Network** → 任意一条接口请求 → 请求头里的 `authorization: Bearer xxx`，复制 `Bearer ` 后面那一段 | `eyJhbGciOiJIUzI1NiIs...` |
| 项目 ID | `PROJECT_ID` | 打开项目详情页，网址里的 `projectId=`（或 `detail/` 后那串数字） | `123456` |
| 画布 ID | `CANVAS_ID` | 打开画布页，网址里的 `canvasId=` | `12345` |

**Token 失效的表现**：接口报 `401` 或「项目不存在或已删除」。重新登录取一次 token，写回 `.env` 即可；续期后**只补跑失败的那几张**，不要整批重出。

> Token 有效期通常 3 天左右，批量跑之前先看一眼。

---

## 二、提示词模型（必需）

资产图的提示词由文本模型生成（每条场景 3 版，需要稳定的结构化输出）。

| 配置项 | 变量名 | 怎么拿 | 示例 |
|---|---|---|---|
| API Key | `LLM_API_KEY` | 到模型服务商控制台 → API Keys → 新建 | `sk-xxxxxxxx` |
| 接口地址 | `LLM_BASE_URL` | 服务商文档里的 OpenAI 兼容地址 | `https://api.deepseek.com/v1` |
| 模型名 | `LLM_MODEL` | 同上，填模型标识 | `deepseek-chat` |
| 并发数 | `LLM_CONCURRENCY` | 按账号限速填，没有限制填 `3` | `3` |

> 任何兼容 OpenAI 协议的服务都能用（DeepSeek / 通义 / Moonshot / 自建网关）。
> **提示词质量直接决定出图质量**，建议先拿 1 个场景试跑，看产物再批量。

---

## 三、本地路径（必需）

| 配置项 | 变量名 | 说明 | 示例 |
|---|---|---|---|
| 素材根目录 | `ASSET_ROOT` | 所有下载归类的落点，按 `<剧名>/资产图/` 自动建子目录 | `D:/AI漫剧` |
| 工作目录 | `WORK_DIR` | 中间稿（提示词缓存、临时清单、日志）放这里 | `D:/AI漫剧/_work` |

目录约定（脚本自动创建）：

```
<ASSET_ROOT>/<剧名>/资产图/            # 场景 / 人物资产图
<ASSET_ROOT>/<剧名>/资产图/反打图/第N次提交/   # 反打图按批次分目录
<WORK_DIR>/tmp_<剧名>_<批次>/          # 提示词、清单、中间产物
```

---

## 四、可选配置

| 配置项 | 变量名 | 说明 | 默认 |
|---|---|---|---|
| 单张价格 | `PRICE_PER_IMAGE` | 估价用，按平台实际资费填 | `0.12` |
| 出图并发 | `SUBMIT_PARALLEL` | 同时提交几个节点 | `3` |
| 轮询间隔 | `POLL_INTERVAL` | 等出图时几次一查（秒） | `20` |
| 出图超时 | `POLL_TIMEOUT` | 单批最长等多久（秒） | `2400` |
| 素材根 URL 前缀 | `ASSET_URL_PREFIX` | 平台素材 CDN 域名（上传时拼地址用） | 平台文档 |

---

## 五、上传相关（只有要「传回平台」时才需要）

平台素材上传走对象存储（COS）通道，需要一组存储凭证。

| 配置项 | 变量名 | 怎么拿 |
|---|---|---|
| 存储 SecretId | `COS_SECRET_ID` | 平台侧获取上传凭证的接口返回（`GetUploadCredential` 类接口） |
| 存储 SecretKey | `COS_SECRET_KEY` | 同上 |
| 存储桶 | `COS_BUCKET` | 同上 |
| 存储区域 | `COS_REGION` | 同上 |

> 如果平台的上传接口会**动态下发**临时凭证（多数是这样），脚本每次上传前自动取，`.env` 里留空即可。
> 只做「出图 + 归档」、不往平台上挂图的话，这一组可以完全不填。

---

## 六、自检通过的标准

```bash
python scripts/check_env.py
```

应输出类似：

```
[✓] PLATFORM_BASE_URL / PLATFORM_TOKEN   可连通
[✓] PROJECT_ID / CANVAS_ID               已定位（项目名：xxx，画布名：xxx）
[✓] LLM_API_KEY                          可调用（模型：xxx）
[✓] ASSET_ROOT / WORK_DIR                可写
[ ] COS_*                                未配置（跳过上传功能）
```

`[ ]` 项若不影响你要用的功能，可以留空。

---

## 七、安全提醒

- `.env` **不要提交到 git**（`.gitignore` 已排除）；团队内共享用密码管理器或私聊发。
- Token / Key 一旦外泄，立刻在控制台作废重发。
- 别人给的剧本、素材属客户资产，别传公共仓库。
