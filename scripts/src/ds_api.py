"""DeepSeek 调用封装：重试 + 滑动窗口限速（并发安全）。

【为什么不用「每次请求前睡满 3 秒」】
老实现是串行的：记一个 last_request_time，下次请求前睡到距它满 REQUEST_INTERVAL 秒。
串行时没问题，但它把并发彻底焊死 —— N 个线程会排队各睡自己的 3 秒，
开了线程等于没开。而且那个时间戳是在**响应返回之后**才更新的，
于是实际间隔是「延迟 + 3 秒」（实测一次生成 ~20 秒，等于每个场景白等 23 秒）。

这里换成一个滑动窗口：保证**任意** REQUEST_INTERVAL 秒内最多发起
SCENE_CONCURRENCY 次请求。
  SCENE_CONCURRENCY = 1 → 严格串行限速，和老行为等价（起止间隔 ≥ REQUEST_INTERVAL）
  SCENE_CONCURRENCY = N → 平均速率不超，但线程不再空等，并发真正生效
被上游限流（429）时，用「全局暂停」把**所有**线程一起降速，而不是各重各的。
"""
import threading
import time
from collections import deque

from openai import OpenAI

from config import (
    DS_API_BASE, DS_API_KEY, DS_MODEL,
    REQUEST_INTERVAL, REQUEST_TIMEOUT,
    MAX_RETRIES, RETRY_BACKOFF, SCENE_CONCURRENCY,
)

# key 为空时给个占位串：让客户端能构造成功（不阻塞 --help / 配置自检），
# 真正发请求前由下面显式校验并给出「去填 LLM_API_KEY」的提示。
client = OpenAI(base_url=DS_API_BASE, api_key=DS_API_KEY or "sk-not-configured",
                timeout=REQUEST_TIMEOUT)

# 这些状态码值得重试：限流 / 上游过载 / 网关抖动
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# --------------------------------------------------------------- 滑动窗口限速

_window_lock = threading.Lock()
_window = deque()          # 最近几次请求的发起时刻（升序）
_pause_until = 0.0         # 全局暂停到这一刻（被限流时抬高，所有线程一起等）
_capacity = max(1, int(SCENE_CONCURRENCY))


def set_concurrency(n: int):
    """按 --jobs 调整限速窗口容量，让限速与实际并发保持一致。"""
    global _capacity
    _capacity = max(1, int(n))


def _acquire_slot():
    """取一个请求额度，取不到就在窗口外等 —— 并发安全。"""
    while True:
        with _window_lock:
            now = time.time()
            if now < _pause_until:
                wait = _pause_until - now
            else:
                while _window and now - _window[0] >= REQUEST_INTERVAL:
                    _window.popleft()
                if len(_window) < _capacity:
                    _window.append(now)
                    return
                wait = REQUEST_INTERVAL - (now - _window[0])
        time.sleep(max(wait, 0.05))


def _penalize(seconds: float):
    """被限流时把全局暂停往后推 —— 让所有线程一起降速，避免连环 429。"""
    global _pause_until
    with _window_lock:
        _pause_until = max(_pause_until, time.time() + seconds)


def _is_retryable(err: Exception) -> bool:
    status = getattr(err, "status_code", None)
    if status is None:
        status = getattr(getattr(err, "response", None), "status_code", None)
    if status is not None:
        return status in RETRYABLE_STATUS
    # 没有状态码说明是连接层错误（超时、断连），同样值得重试
    return True


def call_ds_api(system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tokens: int = 2000) -> str:
    if not DS_API_KEY:
        raise RuntimeError(
            "未配置 DeepSeek API Key：请设置环境变量 LLM_API_KEY，"
            "或把 Key（base64 一行）写入 src/.ds_key"
        )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        _acquire_slot()
        try:
            response = client.chat.completions.create(
                model=DS_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            if attempt < MAX_RETRIES and _is_retryable(e):
                wait = RETRY_BACKOFF * (2 ** (attempt - 1))
                _penalize(wait)
                print(f"    调用失败（{type(e).__name__}），{wait}s 后重试"
                      f"（第 {attempt + 1}/{MAX_RETRIES} 次尝试）...")
                time.sleep(wait)
                continue
            break

    raise RuntimeError(f"API 调用失败（已重试 {MAX_RETRIES} 次）: {last_err}")
