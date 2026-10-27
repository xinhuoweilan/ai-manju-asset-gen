# -*- coding: utf-8 -*-
"""删除某个场景/角色下的某张素材（用于清理上传测试产物）。

接口来源（2026-09-26 实测）：
  ✅ POST /api/ProjectSeedanceAsset/Delete
       {"items":[{"id":<assetId>, "seedanceAssetType":<5场景/4角色>, "seedanceBaseId":<场景/角色 id>}]}
       ⚠️ items 里必须是**对象**，塞字符串会 400（DTO 是 ProjectSeedanceAssetDeleteItemDto）
  ❌ /api/SeedanceScreenPlay/DeleteSeedanceSceneImage —— 传 {id} / {imageId} / {id,sceneId} 都回
       「参数错误」(code 201)，实测不通，别用

默认 dry-run，加 --commit 才真删。

用法（`--project` 不传就读 `current_canvas.json`）：
  python -X utf8 asset_delete.py --scene 示例场景名 --asset-id 1391904            # dry-run
  python -X utf8 asset_delete.py --scene 示例场景名 --asset-id 1391904 --commit
"""
import argparse
import io
import json
import pathlib
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = pathlib.Path(__file__).resolve().parent.parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core/子目录）
sys.path.insert(0, str(HERE))

import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
import mc  # noqa: E402

LOG = []


def L(s=""):
    LOG.append(str(s))
    print(s)


def scenes(m, pid):
    r = m.post("/api/SeedanceScreenPlay/GetSeedanceSceneList",
               {"projectId": pid, "pageIndex": 1, "pageSize": 9999}, raw=True)
    return (r.get("data") or {}).get("list") or []


def show(items, tag):
    L("   %s 素材 %d 条" % (tag, len(items)))
    for i in items:
        L("      - id=%-9s %-16s 状态=%s(%s)" % (
            i.get("id"), i.get("assetName"), i.get("assetStatus"), i.get("assetStatusText")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None,
                    help="项目 id；不传读工作区 current_canvas.json")
    ap.add_argument("--scene", default="", help="场景名")
    ap.add_argument("--scene-id", default="")
    ap.add_argument("--asset-id", required=True, help="要删的素材 id")
    ap.add_argument("--asset-type", type=int, default=5, help="5=场景图（默认） 4=角色图")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    args.project = mc.require_project_id(args.project)   # 指针缺了就停手（不回退旧项目）

    m = mc.Mobo()
    L("=" * 74)
    L("删除素材  %s / %s   资产 id=%s   %s" % (
        args.project, args.scene or args.scene_id, args.asset_id,
        "COMMIT" if args.commit else "DRY-RUN"))
    L("=" * 74)

    sid = args.scene_id
    target = None
    if not sid:
        cand = scenes(m, args.project)
        hit = [s for s in cand if str(s.get("sceneName")) == args.scene]
        if not hit:
            L("!! 场景名没精确命中「%s」，停下。" % args.scene)
            return 3
        target = hit[0]
        sid = target.get("id")
    else:
        target = next((s for s in scenes(m, args.project) if str(s.get("id")) == str(sid)), None)

    if target is None:
        L("!! 找不到场景")
        return 4

    before = target.get("imageList") or []
    show(before, "删除前")
    victim = [i for i in before if str(i.get("id")) == str(args.asset_id)]
    if not victim:
        L("!! 该场景下没有 id=%s 的素材，什么都不用删。" % args.asset_id)
        return 0
    v = victim[0]
    L("   待删: id=%s  %s  状态=%s(%s)  error=%r" % (
        v.get("id"), v.get("assetName"), v.get("assetStatus"), v.get("assetStatusText"),
        v.get("error")))
    if v.get("isDefault"):
        L("   ⚠️ 它是当前「定妆」，删掉后该场景可能没有定妆图 —— 先确认再删。")

    if not args.commit:
        L("")
        L("DRY-RUN：没发请求。加 --commit 才真删。")
        _save()
        return 0

    L("")
    L("删除中 …")
    payload = {"items": [{"id": str(args.asset_id), "seedanceAssetType": args.asset_type,
                          "seedanceBaseId": str(sid)}]}
    L("   POST /api/ProjectSeedanceAsset/Delete %s" % json.dumps(payload, ensure_ascii=False))
    r = m.post("/api/ProjectSeedanceAsset/Delete", payload, raw=True)
    L("   -> %s" % json.dumps(r, ensure_ascii=False)[:300])

    L("")
    L("回读（删除后）")
    after = next((s for s in scenes(m, args.project) if str(s.get("id")) == str(sid)), None)
    show(after.get("imageList") or [], "")
    gone = not any(str(i.get("id")) == str(args.asset_id) for i in (after.get("imageList") or []))
    L("   %s" % ("✔ 已删除" if gone else "✘ 还在，删除没生效"))

    _save()
    return 0


def _save():
    p = HERE / "asset_upload" / "delete_last.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(LOG), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
