# -*- coding: utf-8 -*-
"""set_variant.py —— 把场景素材里落在「其他」区的图，批量设为「变体」。

▍为什么是这些字段（2026-09-27 零成本静态考古得到，全程没发一次 API）
  前端 `969.*.js` 的 `FR()` 把每个场景的图按 `makeupStatus` 分三区：
      0            → 定稿区（i18n: mc_default_area / 角色侧 mc_makeup_area）
      2            → 变体区（mc_variant_materials）
      其余(1/3/…)  → 「其他」区（mc_other_materials）  ← 本工具的处理对象
  ⚠️ **走 COS 上传的新素材一律落「其他」**，上传**不会**自动变体；若读到某场景已经是
     变体，那是人工在平台上点过「设为变体」（别当成"后端状态不一致"）。
  「设为变体」按钮 = i18n key `mc_set_as_variant`，实现在 `r4t()` hook 里，调：
      POST /api/SeedanceScreenPlay/UpdateSeedanceSceneImageList
      body = {id:场景id, imageId:素材id, imageUrl, isDefault:false,
              prompt, makeupStatus:2, name[, dayType, inOutType]}
  （角色侧同构：`POST /api/SeedanceScreenPlay/UploadSeedanceRoleImage`，
    参数包装是 WC()，比场景侧少 dayType/inOutType。）

▍用法（`--project` 不传就读 `current_canvas.json`，换剧不用改命令）
  python set_variant.py --scenes "旧仓库,北角墓园"                      # dry-run
  python set_variant.py --scenes auto                                  # 全部场景（dry-run）
  python set_variant.py --scenes "旧仓库" --commit --max-changes 2
  python set_variant.py --assets 1396615,1396617 --commit --max-changes 2

▍护栏
  1. 默认 dry-run，一个请求都不发
  2. `--commit` 必须同时给 `--max-changes`（实际条数 > 上限直接拒跑）
  3. **只动 `makeupStatus ∉ {0,2}` 的**（=「其他」区）；定稿、已是变体的一律不碰
  4. 逐条打印「前 → 后」，跑完回读核验
"""
import argparse
import json
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core）
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
from mc import Mobo, default_canvas_id, require_project_id  # noqa: E402

API_LIST = "/api/SeedanceScreenPlay/GetSeedanceSceneList"
API_SET = "/api/SeedanceScreenPlay/UpdateSeedanceSceneImageList"

FINAL, VARIANT = 0, 2         # 定稿 / 变体；其余（1 / 3 / undefined）算「其他」区


def fetch_scenes(m, project_id):
    r = m.post(API_LIST, {"projectId": project_id, "pageSize": 9999, "pageIndex": 1},
               label="read:scene_list")
    items = r.get("list") or []
    if not items and isinstance(r.get("data"), dict):
        items = r["data"].get("list") or []
    return items


def build_body(scene, img):
    """照抄前端 r4t() 里 QC() 的包装。"""
    return {
        "id": str(scene["id"]),
        "imageId": str(img["id"]),
        "imageUrl": img.get("imageUrl") or img.get("httpImageUrl") or "",
        "isDefault": False,
        "prompt": img.get("prompt") or img.get("description") or "",
        "makeupStatus": VARIANT,
        "name": img.get("assetName") or scene.get("name") or scene.get("sceneName") or "",
    }


def is_other_area(img):
    """是否落在「其他」区：makeupStatus 既不是定稿(0) 也不是变体(2)。"""
    mk = img.get("makeupStatus")
    try:
        return int(mk) not in (FINAL, VARIANT)
    except (TypeError, ValueError):
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None,
                    help="项目 id；不传读工作区 current_canvas.json")
    ap.add_argument("--scenes", default="", help="场景名，逗号分隔；auto = 全部场景")
    ap.add_argument("--assets", default="", help="直接给素材 id，逗号分隔（优先级最高）")
    ap.add_argument("--commit", action="store_true", help="真改（默认只 dry-run）")
    ap.add_argument("--max-changes", type=int, default=None, help="允许改动的最大条数")
    args = ap.parse_args()
    project_id = require_project_id(args.project)   # 指针缺了就停手（不回退旧项目）

    m = Mobo()
    scenes = fetch_scenes(m, project_id)
    print(f"  项目 {project_id}：读回 {len(scenes)} 个场景")

    want_names = [s.strip() for s in args.scenes.split(",") if s.strip()]
    want_ids = {s.strip() for s in args.assets.split(",") if s.strip()}
    if not want_names and not want_ids:
        print("!! 必须给 --scenes 或 --assets（防止误伤全项目）")
        return 2

    all_mode = any(s.lower() == "auto" for s in want_names)
    by_name = {}
    for s in scenes:
        for k in (s.get("name"), s.get("sceneName"), s.get("backGroundName")):
            if k:
                by_name.setdefault(k.strip(), s)

    plan = []          # (scene, img, body)
    missing = []
    if want_ids:
        for s in scenes:
            for img in s.get("imageList") or []:
                if str(img.get("id")) in want_ids and is_other_area(img):
                    plan.append((s, img, build_body(s, img)))
    else:
        targets = scenes if all_mode else []
        if not all_mode:
            for nm in want_names:
                s = by_name.get(nm)
                if s:
                    targets.append(s)
                else:
                    missing.append(nm)
        for s in targets:
            for img in s.get("imageList") or []:
                if is_other_area(img):
                    plan.append((s, img, build_body(s, img)))

    if missing:
        print(f"!! 这些场景名没读到：{missing}")

    print()
    print("=" * 92)
    print(f"{'场景':<22}{'素材id':<10}{'现状':<8}{'素材名':<26}{'动作'}")
    print("-" * 92)
    for s, img, _b in plan:
        nm = (img.get("assetName") or "")[:24]
        print(f"{(s.get('name') or s.get('sceneName') or '')[:20]:<22}"
              f"{img.get('id'):<10}{'其他(' + str(img.get('makeupStatus')) + ')':<8}"
              f"{nm:<26}→ 设为变体")
    print("-" * 92)
    print(f"  合计 {len(plan)} 张（全部来自「其他」区；定稿与已是变体的不列出、不处理）")

    if not args.commit:
        print("\n[DRY RUN] 没发任何写请求。确认后加 --commit --max-changes N")
        return 0

    if args.max_changes is None:
        print("\n!! 已给 --commit 但没给 --max-changes，拒跑")
        return 3
    if len(plan) > args.max_changes:
        print(f"\n!! 计划改动 {len(plan)} 条 > 上限 {args.max_changes}，拒跑")
        return 4
    if not plan:
        print("\n没有需要改的，退出。")
        return 0

    print("\n" + "=" * 92)
    print(f"真改 {len(plan)} 条（上限 {args.max_changes}）")
    print("=" * 92)
    ok = bad = 0
    log = []
    for s, img, body in plan:
        try:
            r = m.post(API_SET, body, label="write:set_variant")
            if r is True:                       # mc.Mobo.post 成功时返回 True
                good = True
            elif isinstance(r, dict):
                good = bool(r.get("status") is True or r.get("isSuccess") is True
                            or r.get("code") == 200)
            else:
                good = bool(r)
            if good:
                ok += 1
                print(f"  [OK ] {img.get('assetName')}  -> 变体")
            else:
                bad += 1
                print(f"  [ERR] {img.get('assetName')}  {json.dumps(r, ensure_ascii=False)[:160]}")
            log.append({"assetId": img.get("id"), "name": img.get("assetName"),
                        "resp": r, "ok": good})
        except Exception as e:
            bad += 1
            print(f"  [ERR] {img.get('assetName')}  {e!r}")
            log.append({"assetId": img.get("id"), "name": img.get("assetName"),
                        "error": repr(e), "ok": False})
        time.sleep(0.25)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    # 日志跟**当前画布**走（旧版写死 canvas2/，换了画布还往旧目录塞，回头翻日志会翻错地方）
    out = HERE / f"canvas_{default_canvas_id() or 'unknown'}" / f"set_variant_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"projectId": project_id, "ok": ok, "bad": bad,
                               "items": log}, ensure_ascii=False, indent=2),
                   encoding="utf-8")

    print()
    print("=" * 92)
    print("回读核验")
    print("=" * 92)
    after = fetch_scenes(m, project_id)
    idx = {}
    for s in after:
        for img in s.get("imageList") or []:
            idx[str(img.get("id"))] = img.get("makeupStatus")
    v = sum(1 for _s, img, _b in plan if idx.get(str(img.get("id"))) == VARIANT)
    print(f"  已变体 {v}/{len(plan)}")
    if ok != len(plan) or v != len(plan):
        print("  !! 有没改成功的，见上面的 ERR 与落盘日志")
    print(f"  日志 -> {out}")
    print(m.dump_audit())
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
