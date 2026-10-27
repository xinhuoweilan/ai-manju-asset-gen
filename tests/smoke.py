#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""冒烟测试 —— 本地和 CI 都能跑。

    python tests/smoke.py           # 全套（语法 / 导入 / 配置自检 / 密钥扫描）
    python tests/smoke.py --quick   # 只跑语法 + 导入

不联网、不花钱、不需要真实 .env：脚本自己注入一套假配置，
只验证「代码能跑起来 + 缺配置时的提示是对的」。
"""

import argparse
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"

FAKE_ENV = {
    "PLATFORM_BASE_URL": "https://example.invalid",
    "PLATFORM_TOKEN": "eyJfake.token.for.smoke.test",
    "PROJECT_ID": "100001",
    "CANVAS_ID": "200002",
    "LLM_API_KEY": "sk-fake-for-smoke-test",
    "ASSET_ROOT": str(REPO / "_smoke_assets"),
    "WORK_DIR": str(REPO / "_smoke_work"),
}

FAILS = []


def run(args, env_extra=None, timeout=90):
    env = dict(os.environ)
    env.update(FAKE_ENV)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(args, cwd=str(REPO), capture_output=True, timeout=timeout,
                          env=env, text=True, encoding="utf-8", errors="replace")


def check_compile():
    print("[1/4] 语法检查（compileall）")
    r = subprocess.run([sys.executable, "-m", "compileall", "-q", str(SCRIPTS)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        FAILS.append(("语法检查", (r.stdout or "") + (r.stderr or "")))
        print("    ✗ 有文件语法错误")
    else:
        print("    ✓ 全部通过")


def check_imports():
    print("[2/4] 每个脚本可导入（CLI 跑 --help，库文件跑 import）")
    files = sorted(p for p in SCRIPTS.rglob("*.py") if p.name != "paths.py")
    cli = lib = 0
    bad = []
    syspath = ", ".join(
        repr(str(SCRIPTS / s)) for s in
        ("", "core", "core/asset", "core/deriv", "core/ops", "core/stage", "src", "tools")
    )
    for f in files:
        src = f.read_text(encoding="utf-8", errors="ignore")
        if "argparse" in src:
            cli += 1
            r = run([sys.executable, "-X", "utf8", str(f), "--help"], timeout=90)
            if r.returncode != 0 or "Traceback" in (r.stderr or ""):
                bad.append((f.relative_to(REPO), (r.stderr or r.stdout or "")[:300]))
        else:
            lib += 1
            code = f"import sys; sys.path[:0] = [{syspath}]; import {f.stem}"
            r = run([sys.executable, "-X", "utf8", "-c", code], timeout=90)
            if r.returncode != 0 or "Traceback" in (r.stderr or ""):
                bad.append((f.relative_to(REPO), (r.stderr or "")[:300]))
    if bad:
        for name, err in bad:
            last = [x for x in err.strip().splitlines() if x.strip()]
            print(f"    ✗ {name}\n      {last[-1] if last else ''}")
            FAILS.append((f"导入 {name}", err))
    else:
        print(f"    ✓ CLI {cli} 个 + 库 {lib} 个，全部可导入")


def check_selfcheck():
    print("[3/4] 配置自检（假配置，不联网）")
    r = run([sys.executable, "-X", "utf8", str(SCRIPTS / "check_env.py"), "--no-net"])
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode == 0 and "全部通过" in out:
        print("    ✓ 假配置下自检全绿")
    else:
        print("    ✗ 自检未通过")
        print("\n".join("      " + x for x in out.strip().splitlines()[-8:]))
        FAILS.append(("check_env 自检", out))

    # 反向验证：不给配置时必须是「友好报错」而不是崩溃
    bare = {k: "" for k in FAKE_ENV}
    r2 = run([sys.executable, "-X", "utf8", str(SCRIPTS / "check_env.py"), "--no-net"],
             env_extra=bare)
    out2 = (r2.stdout or "") + (r2.stderr or "")
    if "Traceback" in out2:
        print("    ✗ 缺配置时出现未捕获异常")
        FAILS.append(("缺配置场景", out2))
    else:
        print("    ✓ 缺配置时是友好提示（无 Traceback）")


def check_secrets():
    print("[4/4] 密钥 / 隐私扫描")
    patterns = [
        (r"sk-[A-Za-z0-9]{20,}", "疑似真 API Key"),
        (r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}", "疑似 JWT"),
        (r"Bearer\s+[A-Za-z0-9\-._~+/]{24,}", "疑似 Bearer 令牌"),
        (r"[\w.+-]+@[\w-]+\.(?:com|cn|net|org|io)", "疑似邮箱"),
        (r"\b(?:DESKTOP|LAPTOP)-[A-Z0-9]{4,}\b", "疑似主机名"),
        (r"C:[\\/]Users[\\/](?!name\b)[A-Za-z0-9_.-]+", "疑似本机用户目录"),
    ]
    allow = ("example", "fake", "not-configured", "xxxx", "noreply", "test")
    hits = 0
    for p in sorted(REPO.rglob("*")):
        if not p.is_file() or ".git" in p.parts:
            continue
        if p.suffix.lower() not in {".py", ".md", ".json", ".txt", ".example", ".yml", ".yaml"} \
                and p.name not in (".gitignore",):
            continue
        for i, ln in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            low = ln.lower()
            if any(a in low for a in allow):
                continue
            for pat, why in patterns:
                m = re.search(pat, ln)
                if m:
                    hits += 1
                    print(f"    ✗ {p.relative_to(REPO)}:{i}  {why}: {m.group(0)[:24]}")
                    FAILS.append((f"疑似敏感 {p.name}:{i}", m.group(0)))
    if not hits:
        print("    ✓ 未发现敏感信息")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="只跑语法 + 导入")
    a = ap.parse_args()

    print("=" * 66)
    print("冒烟测试　仓库：", REPO)
    print("=" * 66)

    check_compile()
    check_imports()
    if not a.quick:
        check_selfcheck()
        check_secrets()

    print("=" * 66)
    if FAILS:
        print(f"✗ {len(FAILS)} 项失败")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
