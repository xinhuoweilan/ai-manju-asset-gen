# -*- coding: utf-8 -*-
"""fanda.py —— 反打图「一条命令」：算清单 → 建节点 → 出图 → 归档 → 上传

锚定点法与门规则见 SKILL.md 第五节 C-1；本脚本只做执行，不维护口径。

━━ 流程（顺序不可颠倒）━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ① 算清单     已通过场景（humanReviewStatus==0 且 assetStatus==3）中「尚未出反打」的
               —— 判据：归档目录里有没有 `<场景名>-反打-方案N.png`
  ② 给用户确认（本脚本把清单打出来，由模型/用户确认后加 --commit）
  ③ 建节点     拉线垫图：正向定稿图（makeupStatus==0 那张）作 @图片1
  ④ 提交出图   🔴 扣费；preflight 断言 + 打印估价，需 --yes
  ⑤ 下载归档   自动下载到 `人物场景资产图\反打图\<第N次提交>\`（自动续号）
  ⑥ 等用户审核 → 审核通过后再 `--upload` 上传到平台

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PY="python"
  cd "<asset-gen>"

  "$PY" -X utf8 core/deriv/fanda.py --plan  --project 123456 --drama 示例剧
  "$PY" -X utf8 core/deriv/fanda.py --nodes --project 123456 --canvas 12345 --drama 示例剧 [--only 旧仓库]
  "$PY" -X utf8 core/deriv/fanda.py --commit --project 123456 --canvas 12345 --drama 示例剧 --yes
  "$PY" -X utf8 core/deriv/fanda.py --wait  --project 123456 --canvas 12345 --drama 示例剧
  "$PY" -X utf8 core/deriv/fanda.py --upload --drama 示例剧 --batch 第1次提交

场景档案：`core/deriv/fanda_scenes.json`（每场景一句锚定点，写一次永久复用）
  { "旧仓库": {"space_type":"室内·大空间", "door":"反打时门正对镜头", "anchor":"铁皮门在画面正中，旧皮椅在画面深处偏右"} }
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
sys.path.insert(0, str(HERE / "core" / "asset"))
sys.path.insert(0, str(HERE / "core" / "ops"))

import mc  # noqa: E402
import mc_platform as mp  # noqa: E402
from preflight import PreflightError, check_generation_task, check_measured, image_size  # noqa: E402

ARCHIVE = pathlib.Path(__file__).resolve().parent / "fanda_scenes.json"

PROMPT_TMPL = ("帮我进行空间推理帮我生成这个图片的反打图，@图片1，{anchor}，"
               "严格参考图一的画面风格以及质感和光影")
# ★ 2026-10-15：原模板缺 `@图片1`，被 preflight 的 check_at_images 拦下
#   （参考图共 1 张必须出现 @图片1）——平台靠它绑定参考图，缺了参考图就绑不上。

IMG_MODEL, ASPECT_H, RESOLUTION, QUALITY = 26, 1, "2K", 3
MEAS = {"width": 625.0, "height": 350.0}          # 16:9 横图
SST_SCENE = 5                                      # 场景图
UNIT_PRICE = 0.12


def p(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 档案
def load_archive():
    if not ARCHIVE.exists():
        return {}
    try:
        return json.loads(ARCHIVE.read_text(encoding="utf-8"))
    except Exception as e:
        p("!! 档案读不出来（%s），按空处理" % e)
        return {}


def save_archive(d):
    ARCHIVE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    p("档案已写：%s（%d 项）" % (ARCHIVE.name, len(d)))


def machine_door_hint(scene):
    """从正向提示词的【构图与拍摄空间】推「门的方向类型」（左右必须识图，不给猜）"""
    fin = mp.pick_final(scene)
    prompt = (fin or {}).get("prompt") or ""
    import re
    m = re.search(r"【构图与拍摄空间】([^\n]*)", prompt)
    comp = (m.group(1) if m else "")
    m2 = re.search(r"【场景身份与状态】([^\n]*)", prompt)
    ident = (m2.group(1) if m2 else "")
    if "室外" in ident or "街" in ident and "室内" not in ident:
        return "室外 → 无门（锚点不提门）"
    if "从门口" in comp or "从门内侧" in comp:
        return "机位在门口朝内 ⇒ 反打时门**正对镜头**（画面正中）"
    if "斜侧" in comp or "从街角" in comp:
        return "正视图斜侧、门已入画 ⇒ 按守恒反打不再重复给门"
    return "需识图判定（正向机位与门的关系不明）"


# ---------------------------------------------------------------- 清单
def archived_names(drama):
    """归档目录里已出过的 <场景名> → 最大方案号"""
    root = mc.CFG.asset_root / drama / "人物场景资产图" / "反打图"
    got = {}
    if not root.exists():
        return got
    for f in root.rglob("*-反打-方案*.png"):
        stem = f.stem
        if "-反打-方案" not in stem:
            continue
        name, _, num = stem.rpartition("-反打-方案")
        try:
            n = int(num)
        except ValueError:
            n = 0
        if n > got.get(name, 0):
            got[name] = n
    return got


def next_batch_no(drama):
    root = mc.CFG.asset_root / drama / "人物场景资产图" / "反打图"
    n = 0
    if root.exists():
        for d in root.iterdir():
            if d.is_dir() and d.name.startswith("第") and d.name.endswith("次提交"):
                try:
                    n = max(n, int(d.name[1:-3]))
                except ValueError:
                    pass
    return n + 1


def _review_ok(fin, include_pending=False):
    """定稿图是否可用于反打：已通过(humanReviewStatus==0)；
    include_pending=True 时把「待审」(==1) 也放行（用户 2026-10-15 口径：待审的用定稿出）。"""
    if mp.is_passed(fin):
        return True
    if not include_pending:
        return False
    return fin.get("humanReviewStatus") in (0, 1) and str(fin.get("assetStatus")) == "3"


def compute_plan(m, pid, drama, only=None, scenes_whitelist=None, include_pending=False, redo=False):
    """返回 [(场景名, 定稿assetId, 锚定点, 说明)] —— 只读

    scenes_whitelist: 只处理名单内的场景（None = 全部）—— 用户「只做我负责的场景」用。
    include_pending : 待审场景也用定稿出（None/False = 只出已通过）。
    redo            : 忽略「已出过反打」的记录（重出用）。
    """
    scenes = mp.get_scenes(m, pid)
    arch = load_archive()
    done = archived_names(drama)
    wl = set(scenes_whitelist or [])
    rows, skipped_pass, todo_anchor = [], [], []
    for sc in scenes:
        nm = mp.scene_name(sc)
        if only and nm != only:
            continue
        if wl and nm not in wl:
            continue
        fin = mp.pick_final(sc)
        if not fin or not _review_ok(fin, include_pending):
            skipped_pass.append(nm)
            continue
        if nm in done and not redo:
            continue
        a = arch.get(nm) or {}
        anchor = (a.get("anchor") or "").strip()
        if not anchor:
            todo_anchor.append((nm, machine_door_hint(sc)))
            anchor = "<待补锚定点>"
        rows.append((nm, fin.get("id"), anchor, a.get("door", "")))
    return rows, skipped_pass, todo_anchor


def cmd_plan(m, pid, drama, only, wl=None, pend=False, redo=False):
    rows, skipped, todo = compute_plan(m, pid, drama, only, wl, pend, redo)
    p("★ 反打清单（已通过 且 尚未出）—— %d 个场景" % len(rows))
    p("=" * 92)
    for i, (nm, aid, anchor, door) in enumerate(rows, 1):
        p("%2d. %-22s 定稿 assetId=%-9s" % (i, nm, aid))
        p("      锚定点：%s" % anchor)
        if door:
            p("      门    ：%s" % door)
        p("      话术  ：%s" % PROMPT_TMPL.format(anchor=anchor))
    p()
    if todo:
        p("⚠️ 以下 %d 个场景**还没有锚定点**（先识图定门/窗/桌的位置，写进 %s）：" % (len(todo), ARCHIVE.name))
        for nm, hint in todo:
            p("   · %-22s 机器建议：%s" % (nm, hint))
        p()
    if skipped:
        p("（未通过、故未列入：%s）" % "、".join(skipped))
    p()
    p("估价：GPT Image 2 ¥%.2f/张 × %d = **¥%.2f**（提交前仍需你点头）"
      % (UNIT_PRICE, len(rows), UNIT_PRICE * len(rows)))
    return 0


# ---------------------------------------------------------------- 建节点
def bump_plan(nodes, base):
    """该 base 已有的最大方案号"""
    n = 0
    for x in nodes:
        nm = str(x.get("name") or "")
        if nm.startswith(base + "-方案"):
            try:
                n = max(n, int(nm.rsplit("-方案", 1)[1]))
            except ValueError:
                pass
    return n


def cmd_nodes(m, pid, cid, drama, only, wl=None, pend=False, redo=False):
    rows, _, _ = compute_plan(m, pid, drama, only, wl, pend, redo)
    if not rows:
        p("没有要建的场景。")
        return 0
    d = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
    rev, nodes = d.get("revision"), d.get("nodes") or []
    p("画布 %s revision=%s 节点 %d" % (cid, rev, len(nodes)))
    creates, y0 = [], 10000.0
    for nm, aid, anchor, door in rows:
        base = "%s-反打" % nm
        n = bump_plan(nodes, base) + 1
        node_name = "%s-方案%d" % (base, n)
        prompt = PROMPT_TMPL.format(anchor=anchor)
        try:
            check_generation_task([{"assetId": str(aid), "sourceSeedanceType": SST_SCENE}],
                                  [{"assetId": str(aid), "sourceSeedanceType": SST_SCENE}],
                                  prompt)
        except PreflightError as e:
            p("!! %s preflight 不通过：%s" % (nm, e))
            continue
        # ★ 2026-10-15：`imageList` 与 `mixedList` 必须**同时写**（对齐 canvas_gen 的成功写法）。
        #   只写 imageList 时，平台在任何一次 UpdateNode 规范化里会把 imageList 一并清空
        #   ⇒ 参考图静默丢失、出图变纯文生图（这就是「反打和正视图没区别/跑偏」的根因）。
        il = [{"nodeId": "", "mediaType": "image", "label": "正向定稿图", "url": "",
               "assetId": str(aid), "sourceSeedanceType": SST_SCENE,
               "width": 100, "height": 56}]
        data = {"url": [], "sourceSeedanceType": 0, "alt": node_name,
                "action": "image_generate", "generatorType": "default", "isStale": False,
                "params": {
                    "prompt": prompt, "imagePrompt": prompt, "screenPrompt": prompt,
                    "model": "", "imgModel": IMG_MODEL, "imageEditType": 0, "enhance": False,
                    "count": 1, "lightingEffects": [],
                    "settings": {"resolution": RESOLUTION, "ratio": ASPECT_H, "quality": QUALITY},
                    "advancedSettings": {}, "textList": [],
                    "imageList": list(il), "mixedList": list(il),
                    "videoList": [], "audioList": [], "files": [],
                    "modeType": "text2image",
                }}
        creates.append({"nodeKey": mc.new_node_key("image"), "name": node_name, "type": 2,
                        "isResultNode": 0, "status": 1, "isUploaded": 0,
                        "position": {"x": 100.0, "y": y0}, "measured": dict(MEAS),
                        "parentNodeKey": None, "zIndex": 2,
                        "data": json.dumps(data, ensure_ascii=False)})
        p("  + %-30s ← @图片1 = 定稿 %s" % (node_name, aid))
        y0 += 380.0
    if not creates:
        return 0
    r = m.wr_update_nodes(pid, cid, rev, create=creates, episode_id="0", shot_id="0")
    p(">>> UpdateNode create %d 个（返回 %s）" % (len(creates), json.dumps(r, ensure_ascii=False)[:140]))
    d2 = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
    p("复核 revision=%s 节点 %d" % (d2.get("revision"), len(d2.get("nodes") or [])))
    m.dump_audit()
    return 0


# ---------------------------------------------------------------- 提交（扣费）
def cmd_commit(m, pid, cid, drama, only, yes, wl=None, pend=False, redo=False):
    rows, _, _ = compute_plan(m, pid, drama, only, wl, pend, redo)
    if not rows:
        p("没有要提交的场景。")
        return 0
    if not yes:
        p("!! 这是**扣费**动作：%d 张 × ¥%.2f = **¥%.2f**" % (len(rows), UNIT_PRICE, UNIT_PRICE * len(rows)))
        p("   确认后加 `--yes` 重跑。")
        return 2
    d = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
    byname = {str(n.get("name")): n for n in (d.get("nodes") or [])}
    for nm, aid, anchor, door in rows:
        base = "%s-反打" % nm
        target = None
        for k in sorted(byname, reverse=True):
            if k.startswith(base + "-方案"):
                target = byname[k]
                break
        if not target:
            p("!! %s 画布上没有它的反打节点，先跑 --nodes" % nm)
            continue
        pr = (json.loads(target.get("data") or "{}").get("params") or {})
        prompt = pr.get("screenPrompt") or PROMPT_TMPL.format(anchor=anchor)
        files = [{"assetId": str(aid), "sourceSeedanceType": SST_SCENE}]
        try:
            est = check_generation_task(files, files, prompt)
        except PreflightError as e:
            p("!! %s preflight 不通过，跳过：%s" % (nm, e))
            continue
        data = {"createNum": 1, "imagePrompt": prompt, "imgModel": IMG_MODEL,
                "imageEditType": 0, "enhance": False, "aspectRatio": ASPECT_H,
                "imageQuality": QUALITY, "resolution": RESOLUTION,
                "files": files,
                "materials": [{"seedanceId": str(aid), "seq": 1}]}
        p("\n>>> CreateGenerationTask %s nodeId=%s" % (target.get("name"), target.get("id")))
        p("    files=%s" % json.dumps(files, ensure_ascii=False))
        p("    %s" % est)
        try:
            r = m.pay_create_generation_task(pid, cid, int(target.get("id")), data,
                                             episode_id="0", shot_id="0")
            p("    返回 %s" % json.dumps(r, ensure_ascii=False)[:200])
        except Exception as e:
            p("    !! 提交失败：%s" % str(e)[:300])
    p("\n### 审计（应只有本次的 PAY:create_generation_task）")
    m.dump_audit()
    return 0


# ---------------------------------------------------------------- 下载归档
def cmd_wait(m, pid, cid, drama, only, interval, timeout, wl=None):
    wlset = set(wl or [])
    d = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
    nodes = [n for n in (d.get("nodes") or [])
             if "-反打-方案" in str(n.get("name") or "")
             and (not only or str(n.get("name")).startswith(only))
             and (not wlset or str(n.get("name")).split("-反打")[0] in wlset)]
    if not nodes:
        p("画布上没找到反打节点。")
        return 0
    batch = "第%d次提交" % next_batch_no(drama)
    out = mc.CFG.asset_root / drama / "人物场景资产图" / "反打图" / batch
    out.mkdir(parents=True, exist_ok=True)
    p("归档目录：%s" % out)
    t0 = time.time()
    pending = list(nodes)
    while pending and time.time() - t0 < timeout:
        keep = []
        for n in pending:
            dd = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
            cur = next((x for x in (dd.get("nodes") or []) if str(x.get("id")) == str(n.get("id"))), None)
            st = (cur or {}).get("status")
            url = str((cur or {}).get("resultContent") or "").strip()
            if st == 3 and url:
                fname = "%s.png" % cur.get("name")
                try:
                    r = m.s.get(url, timeout=180)
                    r.raise_for_status()
                    (out / fname).write_bytes(r.content)
                    p("  ↓ %s (%d KB)" % (fname, len(r.content) // 1024))
                except Exception as e:
                    p("  !! 下载失败 %s：%s" % (fname, e))
                    keep.append(n)
            else:
                p("  … %s status=%s" % (cur.get("name"), st))
                keep.append(n)
        pending = keep
        if pending:
            time.sleep(interval)
    p("完成：%d 个已归档到 %s" % (len(nodes) - len(pending), out))
    p("→ **等你审核**；确认后再跑 `--upload --drama %s --batch %s`" % (drama, batch))
    return 0


def cmd_upload(drama, batch):
    """用户审核通过后：把该批次归档的反打图上传绑定到平台（调现成的 bind_asset.py）"""
    out = mc.CFG.asset_root / drama / "人物场景资产图" / "反打图" / batch
    if not out.exists():
        p("!! 找不到批次目录 %s" % out)
        return 2
    files = sorted(out.glob("*.png"))
    p("将上传 %d 张 → 平台（调 core/asset/bind_asset.py，先 dry-run 看计划）" % len(files))
    py = sys.executable
    for f in files:
        cmd = [py, "-X", "utf8", str(HERE / "core" / "asset" / "bind_asset.py"), "--file", str(f)]
        p("  $ %s" % " ".join(cmd[2:]))
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        print((r.stdout or "")[-400:])
    p("\n（bind_asset.py 默认 dry-run；确认计划无误后再逐张加 --commit）")
    return 0


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="反打图一条命令（默认只读）")
    ap.add_argument("--project", default=None)
    ap.add_argument("--canvas", default=None, help="画布 id（建节点/提交/下载必需；纪律：必须显式给）")
    ap.add_argument("--drama", required=True, help="剧名（归档落点用）")
    ap.add_argument("--only", default=None, help="只处理某个场景")
    ap.add_argument("--scenes", default=None,
                    help="只处理名单内的场景（逗号分隔）——用于「只做我负责的场景」")
    ap.add_argument("--include-pending", action="store_true",
                    help="待审(humanReviewStatus==1)的场景也用定稿出反打")
    ap.add_argument("--redo", action="store_true",
                    help="忽略「已出过反打」的记录，重出（会新建 -方案N 节点）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="只读：清单 + 话术 + 缺锚定点提示")
    g.add_argument("--nodes", action="store_true", help="建节点（免费）")
    g.add_argument("--commit", action="store_true", help="提交出图（🔴 扣费，需 --yes）")
    g.add_argument("--wait", action="store_true", help="轮询 + 下载归档")
    g.add_argument("--upload", action="store_true", help="审核后上传到平台")
    ap.add_argument("--batch", default=None, help="配合 --upload：第N次提交")
    ap.add_argument("--yes", action="store_true", help="确认扣费")
    ap.add_argument("--interval", type=int, default=20)
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()

    if a.upload:
        return cmd_upload(a.drama, a.batch or "第1次提交")

    m = mc.Mobo()
    pid = mc.require_project_id(a.project)
    wl = [s.strip() for s in (a.scenes or "").split(",") if s.strip()] or None
    pend = bool(a.include_pending)
    redo = bool(a.redo)
    if a.plan:
        return cmd_plan(m, pid, a.drama, a.only, wl, pend, redo)
    if not a.canvas:
        p("!! 需要 --canvas（纪律：不许靠工作区指针猜画布，会花错剧组的钱）")
        return 2
    if a.nodes:
        return cmd_nodes(m, pid, a.canvas, a.drama, a.only, wl, pend, redo)
    if a.commit:
        return cmd_commit(m, pid, a.canvas, a.drama, a.only, a.yes, wl, pend, redo)
    if a.wait:
        return cmd_wait(m, pid, a.canvas, a.drama, a.only, a.interval, a.timeout, wl)
    return 0


if __name__ == "__main__":
    sys.exit(main())
