#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置自检 —— 第一次用、换机器、换账号后跑一次。

    python scripts/check_env.py            # 全量检查（含连通性）
    python scripts/check_env.py --no-net   # 只查配置与目录，不联网

绿灯才能开工；红灯会告诉你怎么补。
"""

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from paths import CFG  # noqa: E402

OK, WARN, ERR = "[✓]", "[!]", "[✗]"
fails = 0


def line(tag, name, detail=""):
    global fails
    if tag == ERR:
        fails += 1
    print(f"{tag} {name:<34} {detail}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-net", action="store_true", help="跳过网络连通性检查")
    a = ap.parse_args()

    print("=" * 70)
    print("配置自检　仓库根：", CFG.repo_root)
    print("=" * 70)

    # ① 必需配置
    for key, val, hint in [
        ("PLATFORM_BASE_URL", CFG.platform_base_url, "平台地址"),
        ("PLATFORM_TOKEN", CFG.token or "(将回退读 token 文件)", "登录令牌"),
        ("PROJECT_ID", CFG.project_id, "项目 ID"),
        ("CANVAS_ID", CFG.canvas_id, "画布 ID"),
        ("LLM_API_KEY", CFG.llm_api_key, "提示词模型 Key"),
    ]:
        if val and not str(val).startswith("("):
            line(OK, key, str(val)[:26] + ("…" if len(str(val)) > 26 else ""))
        else:
            line(ERR, key, f"未配置 —— {hint}（见 API-清单.md）")

    # ② 令牌可用性
    try:
        tok = CFG.require_token()
        line(OK, "令牌可读取", f"{len(tok)} 字符")
    except SystemExit as e:
        line(ERR, "令牌可读取", str(e).replace("!! ", ""))

    # ③ 目录可写
    for name, path in [("ASSET_ROOT", CFG.asset_root), ("WORK_DIR", CFG.work_dir)]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            probe = path / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            line(OK, name, str(path))
        except Exception as e:
            line(ERR, name, f"{path} 不可写（{e}）")

    # ④ 脚本完整性
    need = [
        "core/mc.py", "core/submit_canvas2.py", "core/set_variant.py",
        "core/asset/append_versions.py", "core/asset/finish.py", "core/asset/bind_asset.py",
        "core/asset/asset_delete.py", "core/asset/mc_platform.py",
        "core/deriv/fanda.py", "core/deriv/costume.py",
        "core/ops/preflight.py", "core/ops/canvas_fetch.py",
        "core/stage/canvas_gen.py", "core/mc_upload.py", "core/prompt_tmpl.py",
        "tools/scene_from_list.py", "tools/scene_revise.py",
        "tools/scene_frontface.py", "tools/costume_plan.py",
        "src/config.py", "src/ds_api.py", "src/docx_utils.py",
    ]
    missing = [n for n in need if not (CFG.scripts_root / n).exists()]
    if missing:
        line(ERR, "脚本完整性", "缺：" + "、".join(missing))
    else:
        line(OK, "脚本完整性", f"{len(need)} 个文件齐全")

    # ⑤ 可选：上传凭证
    if CFG.cos_secret_id and CFG.cos_secret_key:
        line(OK, "COS 上传凭证", "已配置（静态）")
    else:
        line(WARN, "COS 上传凭证", "未配置 —— 不影响出图，只影响「传回平台」")

    # ⑥ 网络连通
    if not a.no_net and CFG.platform_base_url and CFG.token:
        try:
            import requests
            r = requests.post(
                CFG.platform_base_url.rstrip("/") + "/api/Project/GetPageList",
                headers={"authorization": "Bearer " + CFG.token, "content-type": "application/json",
                         "origin": CFG.platform_base_url, "referer": CFG.platform_base_url + "/"},
                json={"pageIndex": 1, "pageSize": 1}, timeout=15,
            )
            if r.status_code == 200 and '"isSuccess":true' in r.text.replace(" ", ""):
                line(OK, "平台连通", f"HTTP {r.status_code}")
            elif r.status_code in (401, 403):
                line(ERR, "平台连通", f"HTTP {r.status_code} —— 令牌失效，重新取一次 PLATFORM_TOKEN")
            else:
                line(WARN, "平台连通", f"HTTP {r.status_code}（能连上，但接口返回异常，检查地址是否正确）")
        except Exception as e:
            line(ERR, "平台连通", f"{type(e).__name__}: {e}")
    else:
        line(WARN, "平台连通", "已跳过（--no-net 或缺少地址/令牌）")

    print("=" * 70)
    if fails == 0:
        print(f"{OK} 全部通过，可以开工。")
        return 0
    print(f"{ERR} 有 {fails} 项必须修复，请按上面的提示补 .env 后重跑。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
