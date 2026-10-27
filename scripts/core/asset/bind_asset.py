# -*- coding: utf-8 -*-
"""把图上传到平台「资产准备」并绑定到对应角色/场景 —— 支持单张 / 整批。

接口（2026-09-26 实测，前端 chunk 逆向）：
  A. 取 COS 临时凭证  POST /api/Common/CosCredential/GetUploadCredentialV2 {}
  B. 直传 COS         cos-js-sdk-v5 putObject   Key={key前缀}{HHMMSS}_{uuid32}{ext}
  C. 登记资产         POST /api/ProjectSeedanceAsset/SaveCosUpload
                      {assetName, cosUrl, projectId, seedanceBaseId,
                       duplicateMode, seedanceAssetType}   场景=5 / 角色=4 / 道具=3
  ⚠️ 内网 multipart 分支（ProjectSeedanceAsset/Upload）本站不可用：
     接口 200 且有新 id，但后端处理报 Unsupported media format、图是坏的。

名字匹配（★ 与 asset_match.py 共用同一份解析，别再写第二份）：
  文件名 `苏晚意-方案1.png` → 去后缀 `-方案N` / `（N）` → `苏晚意` → 与目标名比
  顺序：① 精确 ② 去后缀精确 ③ 唯一包含（双向）
  仍不唯一 / 都不中 ⇒ **停下并把候选报出来，不自行挑一个**（用户 2026-09-26 定的规矩）

默认 **dry-run**（只打印计划），`--commit` 才真传。

用法（`--project` 不传就读 `current_canvas.json`，换剧不用改命令）：
  # 单张（--target 可省；省了就从文件名推）
  python -X utf8 bind_asset.py --file "<素材目录>\\庄园走廊.png"
  # 单张指定目标
  python -X utf8 bind_asset.py --file "<素材目录>\\x.png" --target "示例场景名"
  # 整批：先出计划表，人工过一眼，再加 --commit
  python -X utf8 bind_asset.py --dir "<ASSET_ROOT>/<剧名>/人物场景资产图"
  python -X utf8 bind_asset.py --dir "<素材目录>" --commit --verify
  python -X utf8 bind_asset.py --dir "<素材目录>" --limit 1 --commit   # 先试 1 张验通道

⚠️ 依赖 `qcloud_cos`（COS 通道必需）—— 2026-09-27 起系统 Python 3.11.9 已补装，
   全链统一用它跑即可；别再混用托管 venv（那边没有 playwright / python-docx）。
"""
import argparse
import io
import json
import pathlib
import sys
import time
import uuid
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent.parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core/子目录）
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(HERE))

import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
import mc  # noqa: E402
import requests  # noqa: E402
from asset_match import page_all, strip_suffix  # noqa: E402  同一份名字解析

TYPE_ROLE = 4
TYPE_SCENE = 5
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

LOG = []


def L(s=""):
    LOG.append(str(s))
    print(s)


# ------------------------------------------------------------------ 读目标表
def load_targets(m, pid):
    scenes = page_all(m, "/api/SeedanceScreenPlay/GetSeedanceSceneList",
                      {"projectId": pid, "pageSize": 9999})
    roles = page_all(m, "/api/SeedanceScreenPlay/GetProjectActor",
                     {"projectId": pid, "pageSize": 9999})
    return scenes, roles


def match_target(query, cands, key):
    """按名字找目标。返回 (item|None, 命中方式, 候选名list)

    四步：精确 → 去后缀精确 → 唯一包含 → 报候选（停下）
    「唯一包含」是双向的：`庄园走廊` 能命中 `示例场景名`；反过来也行。
    只有**唯一**一个候选时才认，多个一律停下报出来。
    """
    q = (query or "").strip()
    if not q:
        return None, "", []
    names = [str(c.get(key) or "").strip() for c in cands]

    for c, n in zip(cands, names):
        if n == q:
            return c, "精确", []
    q2 = strip_suffix(q)
    if q2 and q2 != q:
        for c, n in zip(cands, names):
            if n == q2:
                return c, "去后缀精确(%s)" % q2, []
    base = q2 or q
    hits = [c for c, n in zip(cands, names)
            if n and (n in base or base in n)]
    if len(hits) == 1:
        return hits[0], "唯一包含", []
    # 停下：报候选（多个包含命中优先，其次前 2 字相同）
    near = [str(c.get(key)) for c in hits] or \
           [n for n in names if base[:2] and base[:2] in n]
    return None, "", near[:12]


# ------------------------------------------------------------------ COS 上传
def upload_to_cos(m, src):
    """取凭证 → 直传 COS → 返回 cosUrl（= accessDomain + Key，和前端一致）"""
    r = m.post("/api/Common/CosCredential/GetUploadCredentialV2", {}, raw=True)
    d = r.get("data") or {}
    cred = d.get("credentials") or {}
    if not cred.get("tmpSecretId"):
        raise RuntimeError("取 COS 凭证失败: %s" % json.dumps(r, ensure_ascii=False)[:300])

    key_prefix = d["key"] if d["key"].startswith("/") else "/" + d["key"]
    name = "%s_%s%s" % (datetime.now().strftime("%H%M%S"), uuid.uuid4().hex, src.suffix)
    key = key_prefix + name
    cos_url = d["accessDomain"].rstrip("/") + key

    from qcloud_cos import CosConfig, CosS3Client
    cfg = CosConfig(Region=d["region"], SecretId=cred["tmpSecretId"],
                    SecretKey=cred["tmpSecretKey"], Token=cred["sessionToken"],
                    Scheme="https")
    client = CosS3Client(cfg)
    ctype = "image/png" if src.suffix.lower() == ".png" else "image/jpeg"
    with open(src, "rb") as fh:
        resp = client.put_object(Bucket=d["bucket"], Key=key, Body=fh, ContentType=ctype)
    L("   COS HTTP %s" % (resp.get("status_code") or resp))
    return cos_url


def save_cos_upload(m, payload):
    return m.post("/api/ProjectSeedanceAsset/SaveCosUpload", payload, raw=True)


def upload_multipart(m, src, fields):
    with open(src, "rb") as fh:
        r = requests.post(mc.BASE + "/api/ProjectSeedanceAsset/Upload",
                          headers={"authorization": "Bearer " + m.token,
                                   "areainterface": "cn",
                                   "accept": "application/json, text/plain, */*"},
                          data=fields, files={"file": (src.name, fh, "image/png")},
                          timeout=300)
    return {"_http": r.status_code, "_body": r.text[:800]}


def do_upload(m, src, target_id, atype, asset_name, via, dup_mode):
    """真上传一张，返回新素材 id（可能 None）"""
    fields = {"assetName": asset_name, "projectId": str(PID),
              "seedanceBaseId": str(target_id) if target_id else "",
              "duplicateMode": dup_mode, "seedanceAssetType": atype}
    if via == "cos":
        cos_url = upload_to_cos(m, src)
        payload = {k: v for k, v in fields.items() if v != ""}
        payload["cosUrl"] = cos_url
        r = save_cos_upload(m, payload)
        L("   SaveCosUpload → %s" % json.dumps(r, ensure_ascii=False)[:200])
        return str(r.get("data") or "") or None, r
    r = upload_multipart(m, src, {k: str(v) for k, v in fields.items() if v != ""})
    L("   multipart → %s" % json.dumps(r, ensure_ascii=False)[:200])
    return None, r


# ------------------------------------------------------------------ 回读 / 校验
def reread(m, pid, kind, target_id):
    """回读某个角色/场景的 imageList"""
    if kind == "scene":
        lst = page_all(m, "/api/SeedanceScreenPlay/GetSeedanceSceneList",
                       {"projectId": pid, "pageSize": 9999})
        key = "sceneName"
    else:
        lst = page_all(m, "/api/SeedanceScreenPlay/GetProjectActor",
                       {"projectId": pid, "pageSize": 9999})
        key = "name"
    item = next((x for x in lst if str(x.get("id")) == str(target_id)), None)
    return item, key


def show_images(item, tag):
    imgs = (item or {}).get("imageList") or []
    L("   %s 素材 %d 条" % (tag, len(imgs)))
    for i in imgs:
        L("      - id=%-9s %-24s 状态=%s(%s)%s" % (
            i.get("id"), i.get("assetName"), i.get("assetStatus"),
            i.get("assetStatusText"), "  [定妆]" if i.get("isDefault") else ""))
    return imgs


def wait_and_verify(m, pid, kind, target_id, new_id, src, timeout=150):
    """等新素材处理完（status 3/4），--verify 时下载比对 md5"""
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        item, _ = reread(m, pid, kind, target_id)
        cur = next((i for i in ((item or {}).get("imageList") or [])
                    if str(i.get("id")) == str(new_id)), None)
        if cur:
            st = cur.get("assetStatus")
            if st in (3, 4):
                last = cur
                break
            time.sleep(6)
    if not last:
        item, _ = reread(m, pid, kind, target_id)
        last = next((i for i in ((item or {}).get("imageList") or [])
                     if str(i.get("id")) == str(new_id)), None)
    if not last:
        L("   !! 回读不到新素材 id=%s" % new_id)
        return False

    L("   新素材 %s | %s | 状态=%s(%s) error=%r" % (
        last.get("id"), last.get("assetName"), last.get("assetStatus"),
        last.get("assetStatusText"), last.get("error") or ""))
    if last.get("assetStatus") != 3:
        L("   ✘ 后端处理未成功，图不可用")
        return False

    import hashlib
    a = hashlib.md5(src.read_bytes()).hexdigest()
    url = last.get("httpImageUrl") or ""
    if not url:
        L("   ⚠️ 没有 httpImageUrl，跳过 md5 校验")
        return True
    try:
        resp = requests.get(url, timeout=180)
        b = hashlib.md5(resp.content).hexdigest()
        out = HERE / "asset_upload" / "verify"
        out.mkdir(parents=True, exist_ok=True)
        (out / ("new_%s%s" % (new_id, src.suffix))).write_bytes(resp.content)
        if a == b:
            L("   ✔ md5 一致（%s…）—— 图原样落库" % a[:12])
            return True
        L("   ⚠️ md5 不同：源 %s… / 库 %s…（可能被重编码，肉眼确认预览图）" % (a[:12], b[:12]))
        return True
    except Exception as e:
        L("   ⚠️ 下载校验失败：%s" % e)
        return True


# ------------------------------------------------------------------ 批量计划
def build_plan(files, scenes, roles, kind_force, query=None):
    rows = []
    for f in files:
        q = (query or "").strip() or f.stem
        s_hit, s_how, s_near = match_target(q, scenes, "sceneName")
        r_hit, r_how, r_near = match_target(q, roles, "name")
        kind, hit, how, near, amb = None, None, "", [], False

        if kind_force == "asset":
            rows.append({"file": f, "kind": "asset", "target": "（不绑定）",
                         "target_id": "", "how": "指定 asset", "near": [], "imageList": []})
            continue
        if kind_force == "scene":
            hit, how, near, kind = s_hit, s_how, (s_near or r_near), "scene"
        elif kind_force == "role":
            hit, how, near, kind = r_hit, r_how, (r_near or s_near), "role"
        else:  # auto：先场景后角色；两边都中就报歧义
            if s_hit and r_hit:
                rows.append({"file": f, "kind": "?", "target": "歧义：场景/角色都命中",
                             "target_id": "", "how": "", "near":
                             [str(s_hit.get("sceneName")), str(r_hit.get("name"))],
                             "imageList": []})
                continue
            if s_hit:
                hit, how, near, kind = s_hit, s_how, s_near, "scene"
            elif r_hit:
                hit, how, near, kind = r_hit, r_how, r_near, "role"
            else:
                near = s_near or r_near
                kind = None

        rows.append({
            "file": f, "kind": kind,
            "target": (hit.get("sceneName") or hit.get("name")) if hit else "—",
            "target_id": str(hit.get("id")) if hit else "",
            "how": how, "near": near,
            "imageList": (hit.get("imageList") or []) if hit else [],
        })
    return rows


def print_plan(rows):
    ok = [r for r in rows if r["kind"] in ("scene", "role", "asset")]
    bad = [r for r in rows if r["kind"] not in ("scene", "role", "asset")]
    L("=" * 100)
    L("%-34s %-16s %-8s %-12s %-6s %s" % ("文件名", "目标", "类型", "命中方式", "现有", "结论"))
    L("-" * 100)
    for r in rows:
        if r["kind"] in ("scene", "role", "asset"):
            L("%-34s %-16s %-8s %-12s %-6s ✔ 上传" % (
                r["file"].stem[:34], str(r["target"])[:16],
                {"scene": "场景", "role": "角色", "asset": "不绑定"}[r["kind"]],
                r["how"][:12], len(r["imageList"])))
        else:
            L("%-34s %-16s %-8s %-12s %-6s ✋ 停下" % (
                r["file"].stem[:34], "—", "?", "", "—"))
    L("-" * 100)
    L("合计 %d 张：可上传 %d ／ 需人工确认 %d" % (len(rows), len(ok), len(bad)))
    if bad:
        L("")
        L("✋ 需人工确认的（按规矩停下，不自行挑）：")
        for r in bad:
            L("   %s" % r["file"].name)
            L("      候选: %s" % (", ".join(map(str, r["near"])) or "无（名单里没有相近的）"))
    L("=" * 100)
    return len(ok), len(bad)


# ------------------------------------------------------------------ main
PID = ""


def main():
    global PID
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=None,
                    help="项目 id；不传读工作区 current_canvas.json")
    ap.add_argument("--file", help="单张图片路径")
    ap.add_argument("--dir", help="整个目录（批量）")
    ap.add_argument("--target", default="", help="目标名；省了就从文件名推")
    ap.add_argument("--kind", default="auto", choices=["auto", "scene", "role", "asset"])
    ap.add_argument("--id", default="", help="目标 id（给了就跳过按名字查）")
    ap.add_argument("--name", default="", help="资产名（默认取文件名；★后端会按目标名自动编号，一般不生效）")
    ap.add_argument("--duplicate-mode", type=int, default=0)
    ap.add_argument("--via", default="cos", choices=["cos", "multipart"])
    ap.add_argument("--limit", type=int, default=0, help="批量时只处理前 N 张（试跑用）")
    ap.add_argument("--sleep", type=float, default=0.0, help="每张之间间隔秒数")
    ap.add_argument("--verify", action="store_true", help="等后端处理完并下载比对 md5")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    args.project = mc.require_project_id(args.project)   # 指针缺了就停手（不回退旧项目）
    PID = str(args.project)

    if not args.file and not args.dir:
        L("!! 需要 --file 或 --dir")
        return 2

    m = mc.Mobo()
    L("=" * 100)
    L("上传到平台「资产准备」   项目 %s   通道 %s   %s" % (
        PID, args.via, "COMMIT（真上传）" if args.commit else "DRY-RUN（不传）"))
    L("=" * 100)

    scenes, roles = load_targets(m, PID)
    L("目标表：场景 %d / 角色 %d" % (len(scenes), len(roles)))
    L("")

    # ---------- 单张 ----------
    if args.file:
        src = pathlib.Path(args.file)
        if not src.exists():
            L("!! 文件不存在: %s" % src)
            return 2
        rows = build_plan([src], scenes, roles, args.kind, query=args.target)
        r = rows[0]
        if args.id:
            r.update(kind=("scene" if args.kind != "role" else "role"),
                     target=args.target or "(按 id)", target_id=str(args.id), how="指定 id")
        L("文件 %s  (%.2f MB)" % (src.name, src.stat().st_size / 1024 / 1024))
        if r["kind"] not in ("scene", "role", "asset"):
            L("① 匹配目标")
            L("   查询名: %s" % (args.target or src.stem))
            L("   !! 没有唯一命中，按规矩停下")
            L("   候选: %s" % (", ".join(map(str, r["near"])) or "无（名单里没有相近的）"))
            L("   ⇒ 请人工确认后，用 --target <准确名字> 重跑。")
            _save()
            return 3
        L("① 目标: %s (%s) | 命中方式 %s" % (r["target"], r["kind"], r["how"]))

        if r["kind"] in ("scene", "role", "asset"):
            item, _ = reread(m, PID, r["kind"], r["target_id"]) if r["kind"] != "asset" else (None, "")
            if item:
                L("")
                L("② 上传前")
                before = show_images(item, "")
                before_ids = {str(i.get("id")) for i in before}
            else:
                before_ids = set()

        atype = {"scene": TYPE_SCENE, "role": TYPE_ROLE}.get(r["kind"], 0)
        fields = {"assetName": args.name or src.name, "projectId": PID,
                  "seedanceBaseId": r["target_id"], "duplicateMode": args.duplicate_mode,
                  "seedanceAssetType": atype}
        L("")
        L("③ 将要发出的请求")
        for k, v in fields.items():
            L("     %-18s = %s" % (k, v if v != "" else "(省略)"))

        if not args.commit:
            L("")
            L("DRY-RUN：没发任何请求。加 --commit 才真传。")
            _save()
            return 0

        L("")
        L("④ 真上传 …")
        new_id, _ = do_upload(m, src, r["target_id"],
                              fields["seedanceAssetType"], fields["assetName"],
                              args.via, args.duplicate_mode)
        L("")
        L("⑤ 回读")
        if r["kind"] != "asset" and new_id:
            wait_and_verify(m, PID, r["kind"], r["target_id"], new_id, src) if args.verify \
                else _reread_brief(m, r, new_id)
        _save()
        return 0

    # ---------- 批量 ----------
    d = pathlib.Path(args.dir)
    if not d.is_dir():
        L("!! 目录不存在: %s" % d)
        return 2
    files = sorted(p for p in d.iterdir() if p.suffix.lower() in IMG_EXT)
    if args.limit:
        files = files[:args.limit]
    if not files:
        L("!! 目录里没有图片文件")
        return 2

    L("目录 %s —— %d 张图片" % (d, len(files)))
    rows = build_plan(files, scenes, roles, args.kind)
    n_ok, n_bad = print_plan(rows)

    if not args.commit:
        L("")
        L("DRY-RUN：没发任何请求。核对计划表后加 --commit 真传。")
        _save()
        return 0

    L("")
    L("④ 开始上传（%d 张）" % n_ok)
    done, failed = [], []
    for r in rows:
        if r["kind"] not in ("scene", "role", "asset"):
            continue
        src = r["file"]
        atype = {"scene": TYPE_SCENE, "role": TYPE_ROLE}.get(r["kind"], 0)
        L("")
        L("── %s → %s(%s) [%d 张现有]" % (src.name, r["target"], r["kind"], len(r["imageList"])))
        try:
            new_id, _ = do_upload(m, src, r["target_id"], atype, src.name,
                                  args.via, args.duplicate_mode)
            L("   ✔ 新素材 id=%s" % new_id)
            done.append((r, new_id))
        except Exception as e:
            L("   ✘ 失败：%s" % e)
            failed.append((src.name, str(e)))
        if args.sleep:
            time.sleep(args.sleep)

    L("")
    L("⑤ 回读汇总")
    for r, new_id in done:
        if r["kind"] == "asset" or not new_id:
            continue
        item, key = reread(m, PID, r["kind"], r["target_id"])
        cur = next((i for i in ((item or {}).get("imageList") or [])
                    if str(i.get("id")) == str(new_id)), None)
        if not cur:
            L("   ? %-30s id=%s 回读不到" % (r["file"].stem[:30], new_id))
            continue
        L("   %s %-30s %-24s 状态=%s(%s)" % (
            "✔" if cur.get("assetStatus") == 3 else "…", r["file"].stem[:30],
            cur.get("assetName"), cur.get("assetStatus"), cur.get("assetStatusText")))
        if args.verify:
            wait_and_verify(m, PID, r["kind"], r["target_id"], new_id, r["file"])

    L("")
    L("=" * 100)
    L("完成：成功 %d ／ 失败 %d ／ 需人工 %d" % (len(done), len(failed), n_bad))
    for nm, err in failed:
        L("   ✘ %s —— %s" % (nm, err))
    L("=" * 100)
    _save()
    return 0


def _reread_brief(m, r, new_id):
    item, _ = reread(m, PID, r["kind"], r["target_id"])
    show_images(item, "上传后")
    L("   新素材 id=%s（加 --verify 可等处理完并比对 md5）" % new_id)


def _save():
    p = HERE / "asset_upload" / "bind_last.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(LOG), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
