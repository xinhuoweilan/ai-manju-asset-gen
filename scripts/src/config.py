"""配置。

【Key 怎么配】本文件不存放明文 Key，按优先级读取：
  1. 仓库 `.env` 的 `LLM_API_KEY`（推荐）
  2. 环境变量 `LLM_API_KEY` / `DS_API_KEY`（后者兼容旧名）
  3. 同目录（src/）下的本地文件 `.ds_key` —— 内容为 base64 编码的一行
都读不到时，程序会在发请求**之前**给出明确提示，而不是白跑一次 401。
"""
import base64
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_KEY_FILE = _HERE / ".ds_key"

try:                                    # 统一配置（仓库根 .env）
    sys.path.insert(0, str(_HERE.parent))
    import paths as _paths
    _CFG = _paths.CFG
except Exception:                       # 单独拷走该模块时也能跑
    _CFG = None


def _decode_key(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    if raw.startswith("sk-"):
        return raw                       # 兼容：直接写明文也能跑
    try:
        return base64.b64decode(raw.encode("utf-8")).decode("utf-8").strip()
    except Exception:
        return raw


def load_api_key() -> str:
    """环境变量优先，其次 .env，其次本地 .ds_key 文件；都没有则返回空串。"""
    for k in ("LLM_API_KEY", "DS_API_KEY"):
        v = os.environ.get(k, "").strip()
        if v:
            return v
    if _CFG is not None and getattr(_CFG, "llm_api_key", ""):
        return _CFG.llm_api_key
    if _KEY_FILE.exists():
        return _decode_key(_KEY_FILE.read_text(encoding="utf-8"))
    return ""


DS_API_BASE = (os.environ.get("LLM_BASE_URL")
               or (getattr(_CFG, "llm_base_url", "") if _CFG else "")
               or "https://api.deepseek.com/v1")
DS_API_KEY = load_api_key()
DS_MODEL = (os.environ.get("LLM_MODEL")
            or (getattr(_CFG, "llm_model", "") if _CFG else "")
            or "deepseek-chat")

_OUT = (_CFG.work_dir / "prompt_output") if _CFG else (_HERE.parent / "output")
OUTPUT_DIR = str(_OUT)
INPUT_DIR = str(_OUT.parent)

# 限速窗口：任意 REQUEST_INTERVAL 秒内，最多发起 SCENE_CONCURRENCY 次请求。
# 官方 API 用 3；走 ds-free-api 本地代理时调到 10 左右（反代串行 + 风控）。
REQUEST_INTERVAL = 3

REQUEST_TIMEOUT = 120

# 提示词生成的默认并发数（场景与人物通用）。
# 用户明确要求默认串行：防止短时高并发触发反代 API 的风控/封禁。
# 每个场景/人物只依赖自己的剧本块，彼此之间没有共享状态 —— 并发不会改变任何
# 一份提示词的内容，也不会改变它们在 docx 里的顺序，只是把「等 API 返回」的时间重叠起来。
#   1 = 默认，一个接一个，3 秒一次（老行为）
#   4 = 想开并发时用 --jobs 4（不改这里也行），墙钟时间约为串行的 1/4
SCENE_CONCURRENCY = 1

# 单次请求失败后的重试次数（429 / 5xx / 连接抖动都会重试）
MAX_RETRIES = 4
# 退避基数（秒），实际等待 = RETRY_BACKOFF * 2^(已失败次数 - 1)
RETRY_BACKOFF = 5

# 场景块是否连英文翻译行一起喂给模型。
#   True  = 信息最全（能拿到英文标识原文），token 约翻倍
#   False = 只保留含中文的行，省一半 token
INCLUDE_BILINGUAL = True

# 缓存子目录名（建在输出目录下），用于断点续跑，避免重复烧 API
CACHE_DIR = "cache"
