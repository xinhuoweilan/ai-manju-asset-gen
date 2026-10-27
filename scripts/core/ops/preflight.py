# -*- coding: utf-8 -*-
"""preflight.py —— ★ 提交前校验器：把「会忘的红线」变成跑不过去的断言（零扣费）

为什么有这个文件：2026-10-08/09 反复翻车的三处，都写在文档里却照样犯 ——
  · `files[].assetId` 打包错（把整个元组塞进去）⇒ 平台不报错、任务假成功、参考图全丢；
  · `measured` 写死竖框 ⇒ 横图被塞成竖屏；
  · `@图片N` 与参考图数量/序号不一致 ⇒ 参考全串位。
把这三条做成断言，任何提交路径（fanda / canvas_gen / submit）调用前都必须过。

━━ 用法（库）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    import sys; sys.path.insert(0, r"<工作区>/core/ops")
    from preflight import (
        check_assets, check_files_vs_refs, check_at_images,
        check_measured, estimate, PreflightError,
    )
    check_refs_sst(refs)                       # [(assetId, sst), ...]
    check_files_vs_refs(refs, files)           # 逐项核对，不通过抛 PreflightError
    check_at_images(screen_prompt, len(refs))  # @图片1..N 必须齐全
    check_measured(measured, img_w, img_h)     # 宽高比容差 8%
    print(estimate(n=2))                       # ¥0.24

━━ 用法（CLI，校验一份 plan.json）━━━━━━━━━━━━━━━━━━━━━━━
    python core/ops/preflight.py --refs-json refs.json --files-json files.json \
        --prompt-file prompt.txt [--measured 625x350 --image <本地图>] [--count 2]

    refs.json  : {"场景A": {"assetId": "1396615", "sourceSeedanceType": 5}, ...}
                 或 [["1396615", 5], ...]（纯数组也行）
    files.json : [{"assetId": "1396615", "sourceSeedanceType": 5}, ...]
"""
import argparse
import json
import pathlib
import struct
import sys

VALID_SST = {0, 3, 4, 5}          # 0普通上传 / 3道具 / 4角色库 / 5场景
UNIT_PRICE = 0.12                 # GPT Image 2（imgModel=26）¥/张
RATIO_TOL = 0.08                  # 宽高比容差

try:                              # 支持 `from core.ops.preflight import ...`
    from mc import Mobo  # noqa
except Exception:                 # pragma: no cover
    Mobo = None


class PreflightError(Exception):
    """校验不通过 —— 调用方应当**停手**，不要提交。"""


# ---------------------------------------------------------------- 单项断言
def check_asset_id(aid):
    """assetId 必须是纯数字字符串（★ 屡犯：整元组塞进去 ⇒ 参考静默全丢）"""
    if not isinstance(aid, str) or not aid.isdigit():
        raise PreflightError(
            "assetId 必须是纯数字字符串，收到 %r（%s）。\n"
            "  多半是 refs 两层元组少取一层：应取 r[0][0]，不是 r[0]。"
            % (aid, type(aid).__name__))
    return True


def check_sst(sst):
    if sst not in VALID_SST:
        raise PreflightError(
            "sourceSeedanceType=%r 非法；只能是 %s（0普通上传/3道具/4角色库/5场景）"
            % (sst, sorted(VALID_SST)))
    return True


def check_assets(refs):
    """refs 形如 [{"assetId": "1430134", "sourceSeedanceType": 4}, ...] 或 [("1430134", 4), ...]"""
    if not refs:
        raise PreflightError("refs 为空：提交会没有任何参考图")
    for i, r in enumerate(refs, 1):
        if isinstance(r, dict):
            aid, sst = r.get("assetId"), r.get("sourceSeedanceType")
        elif isinstance(r, (list, tuple)) and len(r) >= 2:
            aid, sst = r[0], r[1]
        else:
            raise PreflightError("第 %d 项 refs 形状不对：%r" % (i, r))
        check_asset_id(aid)
        check_sst(sst)
    return True


def check_files_vs_refs(refs, files):
    """files 必须与 refs **逐项一致**（数量 + assetId + sst）"""
    check_assets(refs)
    check_assets(files)
    if len(files) != len(refs):
        raise PreflightError("files 数量 %d ≠ refs 数量 %d" % (len(files), len(refs)))
    for i, (r, f) in enumerate(zip(refs, files), 1):
        ra = r.get("assetId") if isinstance(r, dict) else r[0]
        rs = r.get("sourceSeedanceType") if isinstance(r, dict) else r[1]
        fa, fs = f.get("assetId"), f.get("sourceSeedanceType")
        if str(ra) != str(fa) or rs != fs:
            raise PreflightError(
                "第 %d 项不一致：refs=(%s,%s) vs files=(%s,%s)" % (i, ra, rs, fa, fs))
    return True


def check_at_images(prompt, n_refs):
    """提示词里的 @图片N 必须恰好覆盖 1..n_refs（缺号/越界都拦）"""
    import re
    if not prompt:
        raise PreflightError("提示词为空")
    found = {int(m.group(1)) for m in re.finditer(r"@图片(\d+)", prompt)}
    want = set(range(1, n_refs + 1))
    missing, extra = want - found, found - want
    if missing or extra:
        raise PreflightError(
            "@图片N 与参考图不匹配：缺 %s，越界 %s（参考图共 %d 张）"
            % (sorted(missing) or "无", sorted(extra) or "无", n_refs))
    return True


def _png_size(head):
    return struct.unpack(">II", head[16:24])


def _jpeg_size(data):
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB,
                      0xCD, 0xCE, 0xCF):
            h = struct.unpack(">H", data[i + 5:i + 7])[0]
            w = struct.unpack(">H", data[i + 7:i + 9])[0]
            return w, h
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", data[i + 2:i + 4])[0]
        i += 2 + seg
    return 0, 0


def image_size(path):
    """标准库解析 PNG / JPEG 尺寸（不依赖 PIL）"""
    data = pathlib.Path(path).read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_size(data[:33])
    if data[:2] == b"\xff\xd8":
        return _jpeg_size(data)
    return 0, 0


def parse_measured(measured):
    """measured 支持 {"width":625,"height":350} 或 "625x350" 或 (625,350)"""
    if isinstance(measured, dict):
        return float(measured.get("width")), float(measured.get("height"))
    if isinstance(measured, str):
        w, _, h = measured.lower().partition("x")
        return float(w), float(h)
    if isinstance(measured, (list, tuple)) and len(measured) == 2:
        return float(measured[0]), float(measured[1])
    raise PreflightError("measured 形状不认识：%r" % (measured,))


def check_measured(measured, img_w, img_h, tol=RATIO_TOL):
    """节点框宽高比必须贴近图片真实比例（★ 横图塞竖框的坑）"""
    mw, mh = parse_measured(measured)
    if img_w <= 0 or img_h <= 0:
        raise PreflightError("图片尺寸读不出来（%sx%s）" % (img_w, img_h))
    try:
        r_box, r_img = mw / mh, img_w / img_h
    except ZeroDivisionError:
        raise PreflightError("measured 高度为 0：%r" % (measured,))
    diff = abs(r_box - r_img) / r_img
    if diff > tol:
        raise PreflightError(
            "measured 比例 %.3f 与图片真实比例 %.3f 偏差 %.1f%%（容差 %.0f%%）\n"
            "  ⇒ 按真实比例改：宽统一 625，高 = round(625 / %.3f) = %d"
            % (r_box, r_img, diff * 100, tol * 100, r_img, round(625 / r_img)))
    return True


def estimate(n, unit=UNIT_PRICE, model="GPT Image 2 (imgModel=26)"):
    """估价（提交前必须打印给用户看）"""
    return "估价：%s × %d 张 = **¥%.2f**" % (model, n, unit * n)


# ---------------------------------------------------------------- 一把梭
def check_generation_task(refs, files, screen_prompt, measured=None, image=None, count=None):
    """提交前一次性过全部断言；返回估价字符串。任何一条不过 → 抛 PreflightError"""
    check_files_vs_refs(refs, files)
    check_at_images(screen_prompt, len(refs))
    if measured is not None and image is not None:
        w, h = image_size(image)
        check_measured(measured, w, h)
    n = count if count is not None else len(refs)
    return estimate(n)


# ---------------------------------------------------------------- CLI
def _flatten_refs(obj):
    """把各种 refs 形状拍平成 [{"assetId":..,"sourceSeedanceType":..}, ...]"""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict) and "assetId" in v:
                out.append({"assetId": v.get("assetId"),
                            "sourceSeedanceType": v.get("sourceSeedanceType")})
            elif isinstance(v, (list, tuple)) and len(v) >= 2:
                out.append({"assetId": v[0], "sourceSeedanceType": v[1]})
            elif isinstance(v, dict):
                # 形如 {"assetId":..,"sst":..} 或嵌套 refs
                for kk in ("refs", "list", "items"):
                    if isinstance(v.get(kk), list):
                        out.extend(_flatten_refs(v[kk]))
                        break
    elif isinstance(obj, list):
        for it in obj:
            if isinstance(it, dict):
                out.append({"assetId": it.get("assetId"),
                            "sourceSeedanceType": it.get("sourceSeedanceType")})
            elif isinstance(it, (list, tuple)) and len(it) >= 2:
                out.append({"assetId": it[0], "sourceSeedanceType": it[1]})
    return out


def main():
    ap = argparse.ArgumentParser(description="提交前校验器（零扣费）")
    ap.add_argument("--refs-json", required=True, help="参考图来源 JSON")
    ap.add_argument("--files-json", default=None, help="将要提交的 files JSON（不给则只校验 refs）")
    ap.add_argument("--prompt-file", default=None, help="提示词文件（校验 @图片N）")
    ap.add_argument("--measured", default=None, help="节点框，如 625x350")
    ap.add_argument("--image", default=None, help="本地图片路径（配合 --measured 校验比例）")
    ap.add_argument("--count", type=int, default=None, help="本次生成张数（默认=参考图数）")
    a = ap.parse_args()

    refs = _flatten_refs(json.loads(pathlib.Path(a.refs_json).read_text(encoding="utf-8")))
    print("refs %d 项" % len(refs))
    try:
        check_assets(refs)
        print("  ✓ assetId 均为纯数字 / sst 合法（%s）" % sorted({r["sourceSeedanceType"] for r in refs}))
        if a.files_json:
            files = _flatten_refs(json.loads(pathlib.Path(a.files_json).read_text(encoding="utf-8")))
            check_files_vs_refs(refs, files)
            print("  ✓ files 与 refs 逐项一致（%d 项）" % len(files))
            for i, f in enumerate(files, 1):
                print("      %d) assetId=%-9s sst=%s" % (i, f["assetId"], f["sourceSeedanceType"]))
        if a.prompt_file:
            prompt = pathlib.Path(a.prompt_file).read_text(encoding="utf-8")
            check_at_images(prompt, len(refs))
            print("  ✓ 提示词 @图片1..%d 齐全" % len(refs))
        if a.measured and a.image:
            w, h = image_size(a.image)
            check_measured(a.measured, w, h)
            print("  ✓ measured %s 与图片 %dx%d 比例一致" % (a.measured, w, h))
        print()
        print(estimate(a.count if a.count is not None else len(refs)))
        print("→ 全部通过。可以提交（仍需用户点头）。")
        return 0
    except PreflightError as e:
        print()
        print("!! preflight 不通过，**停手**：")
        print("   " + str(e).replace("\n", "\n   "))
        return 3


if __name__ == "__main__":
    sys.exit(main())
