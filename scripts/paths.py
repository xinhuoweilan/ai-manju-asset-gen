# -*- coding: utf-8 -*-
"""paths.py —— 统一配置入口。

所有脚本从 `CFG` 拿地址 / 凭证 / 目录，**不写死在代码里**，换人、换项目、换机器只改 `.env`。

优先级：**环境变量 > 仓库根目录的 .env > 默认值**
"""

import os
import pathlib
import sys

SCRIPTS_ROOT = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_ROOT.parent

# ★ 统一模块搜索路径：任何脚本只要 `import paths`，就能 `import mc / preflight / ds_api ...`
for _sub in ("", "core", "core/asset", "core/deriv", "core/ops", "core/stage", "src", "tools"):
    _p = SCRIPTS_ROOT / _sub if _sub else SCRIPTS_ROOT
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _load_env_file():
    """极简 .env 解析（不引入第三方依赖）。"""
    data = {}
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return data
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        data[key.strip()] = val.strip().strip('"').strip("'")
    return data


_ENV = _load_env_file()


def get(key: str, default: str = "") -> str:
    return os.environ.get(key) or _ENV.get(key) or default


class _Config:
    # ---- 平台 ----
    platform_base_url: str = get("PLATFORM_BASE_URL")
    token: str = get("PLATFORM_TOKEN")
    project_id: str = get("PROJECT_ID")
    canvas_id: str = get("CANVAS_ID")

    # ---- 目录 ----
    asset_root: pathlib.Path = pathlib.Path(get("ASSET_ROOT", str(pathlib.Path.home() / "AImanju")))
    work_dir: pathlib.Path = pathlib.Path(get("WORK_DIR", str(asset_root / "_work")))
    scripts_root: pathlib.Path = SCRIPTS_ROOT
    repo_root: pathlib.Path = REPO_ROOT
    token_file: pathlib.Path = pathlib.Path(get("TOKEN_FILE", str(SCRIPTS_ROOT / "token.json")))

    # ---- 出图参数 ----
    price_per_image: float = float(get("PRICE_PER_IMAGE", "0.12"))
    submit_parallel: int = int(get("SUBMIT_PARALLEL", "3"))
    poll_interval: int = int(get("POLL_INTERVAL", "20"))
    poll_timeout: int = int(get("POLL_TIMEOUT", "2400"))

    # ---- 文本模型（生成提示词）----
    llm_api_key: str = get("LLM_API_KEY")
    llm_base_url: str = get("LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_model: str = get("LLM_MODEL", "deepseek-chat")
    llm_concurrency: int = int(get("LLM_CONCURRENCY", "3"))

    # ---- 上传（可选）----
    cos_secret_id: str = get("COS_SECRET_ID")
    cos_secret_key: str = get("COS_SECRET_KEY")
    cos_bucket: str = get("COS_BUCKET")
    cos_region: str = get("COS_REGION")

    def drama_dir(self, drama: str) -> pathlib.Path:
        """某部剧的素材目录：<ASSET_ROOT>/<剧名>/资产图/"""
        return self.asset_root / drama / "资产图"

    def require_platform(self) -> str:
        if not self.platform_base_url:
            raise SystemExit(
                "!! 未配置平台地址。请在仓库根目录的 .env 里填 PLATFORM_BASE_URL（见 .env.example / API-清单.md）"
            )
        return self.platform_base_url

    def require_token(self) -> str:
        tok = (self.token or "").strip()
        if tok:
            return tok
        if self.token_file.exists():
            try:
                import json
                return str(json.loads(self.token_file.read_text(encoding="utf-8")).get("token") or "").strip()
            except Exception:
                pass
        raise SystemExit(
            "!! 未配置登录令牌。请在 .env 里填 PLATFORM_TOKEN（怎么拿见 API-清单.md 第一节）"
        )


CFG = _Config()
