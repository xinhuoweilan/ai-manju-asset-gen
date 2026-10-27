"""极简 JSON 结果缓存 —— 用于断点续跑。

一次全本 59 集会跑掉几百次 API 调用，任何一次中断都不该让已花的钱白费。
缓存键是场景名 + 场次编号，命中就直接复用，不再调 API。
"""
import hashlib
import json
import re
from pathlib import Path


def _slug(text: str, limit: int = 40) -> str:
    keep = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", str(text)).strip("_")
    return keep[:limit] or "item"


def cache_path(cache_dir, key: str) -> Path:
    digest = hashlib.md5(str(key).encode("utf-8")).hexdigest()[:10]
    return Path(cache_dir) / f"{_slug(key)}__{digest}.json"


def load_cache(cache_dir, key: str):
    if not cache_dir:
        return None
    p = cache_path(cache_dir, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_cache(cache_dir, key: str, value):
    if not cache_dir:
        return None
    p = cache_path(cache_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    # 不缩进：缓存是给机器读的中间产物，缩进会让文件白胖一倍
    p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return p
