# -*- coding: utf-8 -*-
"""画布工作目录的中间产物清理器（走回收站，可还原）。

两类工作：
  · --once  清「废弃类别」—— 随时可重拉 / 已有更权威的源：
        readback*.json（画布快照）    nodes_after.json（画布快照）
        plan.json / plan_dryrun.json  sent_*.json（写入报文）
        task_status.json              results_manifest.json
        submit_estimate.json
  · 默认    执行「保留策略」—— 带时间戳的留痕文件按「目录 + 类型前缀」分组，
        每组只留最近 N 份（默认 5），其余送回收站。

保留不动的（本脚本永不动它们）：
  token.json / storage_state.json / localstorage.json   ← 登录凭证
  submit_result_*.json   ← 付费凭证（唯一不可再生）
  meta.json              ← 画布身份卡（脚本每次跑都重写）

纳入「保留策略」的（只留最近 N 份）：
  sent_update_*.json / sent_append_*.json   ← 写入报文
  prompt_backup_*.json                      ← 回滚底稿

用法：
    python prune_artifacts.py                 # 保留策略 preview
    python prune_artifacts.py --commit        # 保留策略 执行
    python prune_artifacts.py --once          # 废弃类别 preview
    python prune_artifacts.py --once --commit # 废弃类别 执行
    python prune_artifacts.py --keep 3 --commit
"""
import pathlib as _pl
_HERE = _pl.Path(__file__).resolve().parent
import sys as _sys
_sys.path.insert(0, str(_HERE.parent.parent))   # scripts/（本文件在 scripts/core/ops/）
import paths  # noqa: E402,F401
import argparse
import ctypes
import ctypes.wintypes as wt
import io
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent.parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core/子目录）

# 绝不触碰的文件名（凭证 / 付费凭证 / 身份卡）
PROTECTED = {"token.json", "storage_state.json", "localstorage.json", "meta.json"}
PROTECTED_PREFIX = ("submit_result_",)      # 付费凭证：唯一不可再生
# ⚠️ prompt_backup_*（回滚底稿）不在这里 —— 它虽有用但会无限累积，
#    交给「保留策略」留最近 N 份；--once 的 JUNK_PATTERNS 里也没有它，两边都不会误伤。

# 「废弃类别」匹配（--once 用）
JUNK_PATTERNS = [
    "readback*.json",         # 画布快照（refresh_readback.py 一条命令可重拉）
    "nodes_after.json",       # 画布快照
    "plan.json",              # 建节点前的待发清单
    "plan_dryrun.json",       # 同上
    "sent_create.json",       # 写入报文
    "sent_update_*.json",     # 写入报文
    "sent_append_*.json",     # 写入报文
    "task_status.json",       # 出图状态记录（图已归档）
    "results_manifest.json",  # 下载清单（图已归档）
    "submit_estimate.json",   # 估价结果（零成本可重跑）
]

TS_RE = re.compile(r"^(?P<prefix>.+?)_(?P<ts>\d{8}-\d{6})\.json$")


# ---------------- 回收站 ----------------
class SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wt.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p)]


def _shfo_delete(pfrom):
    """发一次 SHFileOperationW 删除请求。pfrom 需为 \0 分隔、以 \0 结尾（ctypes 再补一个）。

    返回 (rc, 中途放弃)。
    """
    FO_DELETE = 3
    FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 0x40, 0x10, 0x4, 0x400
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = pfrom
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    op.fAnyOperationsAborted = False
    op.hNameMappings = None
    op.lpszProgressTitle = None
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return rc, bool(op.fAnyOperationsAborted)


def recycle(paths):
    """把文件送进回收站。返回 (ok_list, bad_list, rc_list)。

    ⚠️ 实测（2026-09-24）：本机 `SHFileOperationW` 会**返回非零 rc 但操作其实成功**
    —— 文件已消失、回收站里能查到原始完整路径。所以成败一律以「文件是否真的不在了」
    为准，rc 只作记录。**别拿 rc==0 当成功判据**：上一版就因为这个把 21 个全部成功的
    删除报成了「21 个失败」（而文件其实一个不剩）。为减少这种怪返回，改为**一次批量
    提交**而不是逐个调用；批量后仍有残留的再逐个补一刀。
    """
    paths = [pathlib.Path(p) for p in paths]
    if not paths:
        return [], [], []

    rcs = []
    joined = "\0".join(str(p.resolve()) for p in paths) + "\0"
    rc, _ = _shfo_delete(joined)
    rcs.append(rc)

    for p in list(paths):
        if not p.exists():
            continue
        rc2, _ = _shfo_delete(str(p.resolve()) + "\0")     # 兜底：逐个再试
        rcs.append(rc2)

    ok = [p for p in paths if not p.exists()]
    bad = [p for p in paths if p.exists()]
    return ok, bad, rcs


# ---------------- 扫描 ----------------
def is_protected(p):
    if p.name in PROTECTED:
        return True
    return p.name.startswith(PROTECTED_PREFIX)


def scan(root):
    return [p for p in root.rglob("*.json")
            if "browser_profile" not in p.parts and p.is_file() and not is_protected(p)]


def pick_junk(root):
    found = []
    for pat in JUNK_PATTERNS:
        found.extend(root.rglob(pat))
    return sorted(set(found))


def pick_by_retention(root, keep):
    """按 (父目录, 前缀) 分组，每组留最新 keep 份；返回 (要删的, 分组明细)。"""
    groups = {}
    for p in scan(root):
        m = TS_RE.match(p.name)
        if not m:
            continue                                   # 不带时间戳的留给 --once 处理
        pref = m.group("prefix")
        if pref.startswith("prompt_backup"):
            pref = "prompt_backup"          # 归并 prompt_backup / prompt_backup_before_restore
        key = (p.parent, pref)
        groups.setdefault(key, []).append((m.group("ts"), p))

    victims, detail = [], []
    for (parent, prefix), items in sorted(groups.items(), key=lambda kv: str(kv[0])):
        items.sort(reverse=True)                       # 时间戳倒序 = 最新在前
        detail.append((parent, prefix, len(items), [p.name for _, p in items[:keep]]))
        victims.extend(p for _, p in items[keep:])
    return victims, detail


# ---------------- 主流程 ----------------
def main():
    ap = argparse.ArgumentParser(description="画布工作目录中间产物清理（走回收站）")
    ap.add_argument("--root", default=str(HERE), help="项目根目录，默认脚本所在目录")
    ap.add_argument("--keep", type=int, default=5, help="保留策略：每组留最近 N 份（默认 5）")
    ap.add_argument("--once", action="store_true", help="清理「废弃类别」（快照/plan/sent_*/状态记录）")
    ap.add_argument("--commit", action="store_true", help="真删；不加则只预览")
    ap.add_argument("--report", default=None, help="把报告写到这个文件（PowerShell 抓不到 stdout 时用）")
    a = ap.parse_args()

    root = pathlib.Path(a.root).resolve()
    buf = io.StringIO()

    def P(*args):
        print(*args)
        print(*args, file=buf)

    P(f"根目录：{root}")
    P(f"模式：{'废弃类别(--once)' if a.once else f'保留策略(--keep {a.keep})'}"
      f"   动作：{'真删（回收站）' if a.commit else '预览（不动文件）'}")
    P("=" * 100)

    if a.once:
        victims = pick_junk(root)
        P(f"命中 {len(victims)} 个文件，共 {sum(p.stat().st_size for p in victims)/1024:.1f} KB：")
        P("")
        for p in victims:
            P(f"  {p.relative_to(root)}   {p.stat().st_size/1024:8.1f} KB")
        detail = []
    else:
        victims, detail = pick_by_retention(root, a.keep)
        P("分组明细（目录 / 类型前缀 / 现有份数 / 保留哪几份）：")
        P("")
        for parent, prefix, n, kept in detail:
            rel = parent.relative_to(root) if parent != root else pathlib.Path(".")
            flag = "  ← 超出保留数" if n > a.keep else ""
            P(f"  {str(rel):<20} {prefix:<32} 共 {n} 份{flag}")
            for k in kept:
                P(f"      保留  {k}")
        P("")
        if victims:
            P(f"将送回收站 {len(victims)} 个（{sum(p.stat().st_size for p in victims)/1024:.1f} KB）：")
            for p in victims:
                P(f"  删  {p.relative_to(root)}")
        else:
            P("没有超出保留数的文件，无需清理。")

    P("")
    if not victims:
        P("结论：本次无需清理。")
    elif not a.commit:
        P("结论：预览完毕。加 --commit 才真删。")
    else:
        ok, bad, rcs = recycle(victims)
        P(f"结论：{len(ok)} 个已进回收站，{len(bad)} 个失败。（SHFileOperationW 返回码：{rcs}）")
        for p in bad:
            P(f"  失败  {p}")
        P("")
        P("回读确认（应已不存在）：")
        still = [p for p in victims if p.exists()]
        P(f"  仍存在 {len(still)} 个" + ("" if not still else "：" + str(still)))

    if a.report:
        pathlib.Path(a.report).write_text(buf.getvalue(), encoding="utf-8")


if __name__ == "__main__":
    main()
