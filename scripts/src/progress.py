"""进度条 —— 串行跑几十上百个任务时，让你知道「还要多久」。

只用标准库，不引入 tqdm 这种依赖。宽度写死，避免每行长度跳动。
"""


def format_duration(seconds: float) -> str:
    sec = int(max(0, seconds))
    if sec < 60:
        return f"{sec} 秒"
    return f"{sec // 60} 分 {sec % 60:02d} 秒"


def progress_line(done: int, total: int, started: float, label: str = "",
                  now: float = None) -> str:
    """一行进度：`[████░░░░] 3/10  苏晚意  已用 1 分 12 秒｜预计剩余 3 分 05 秒`。

    预计剩余 = 已完成部分的平均耗时 × 剩余个数，只是个粗略估计。
    """
    import time as _time
    now = _time.time() if now is None else now
    total = max(1, int(total))
    done = max(0, min(int(done), total))
    elapsed = now - started
    avg = elapsed / done if done else 0.0
    remain = avg * (total - done)

    width = 20
    filled = int(width * done / total)
    bar = "█" * filled + "░" * (width - filled)
    tail = f"  {label}" if label else ""
    return (f"[{bar}] {done}/{total}{tail}  "
            f"已用 {format_duration(elapsed)}｜预计剩余 {format_duration(remain)}")
