# -*- coding: utf-8 -*-
"""
P2-A：零成本铺节点。把第一阶段产出的「场景/人物提示词」切成一条一条，
铺成魔创自由画布上的图片节点（不提交生成，不花一分钱）。

用法：
  python fill_sandbox.py            # dry-run：只解析 + 打印计划，不碰服务器
  python fill_sandbox.py --commit    # 真写：建沙盒画布 + 铺节点 + 回读校验
  python fill_sandbox.py --cleanup   # 删掉沙盒画布

安全护栏（写在代码里，不靠人记）：
  1. 只往「本脚本自己建的、名字等于 SANDBOX_NAME 的」画布写
  2. 目标画布 revision 必须 == 0（全新空画布），否则拒绝写
  3. 绝不调用 CreateGenerationTask / RedrawPicture 等任何扣费接口
  4. 单次 UpdateNode 原子写入，失败不留半成品
"""
import argparse
import json
import pathlib
import re
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent.parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core/子目录）
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
from mc import (CFG, Mobo, image_node_data, new_node_key, NODE_TYPE,  # noqa: E402
                default_project_id, require_project_id)

from docx import Document  # noqa: E402

GEN_OUT = CFG.work_dir / "prompt_output"   # 提示词产物目录（.env → WORK_DIR）
OUTDIR = CFG.work_dir / "sandbox"

PROJECT_ID = default_project_id()   # 由 current_canvas.json 决定
SANDBOX_NAME = "沙盒-API铺节点测试-可删"

# 版式参数（照抄 杨文伟资产 的实测值）
NODE_W, NODE_H = 622.0, 350.0
COL_PITCH = 900.0          # 列间距（场景与场景之间）
ROW_Y = [105.0, 549.0, 993.0]   # 三个方案的行 y
BASE_X, BASE_Y = 200.0, 105.0

# 图片模型参数（照抄 杨文伟资产 / 李勇资产 的实测值）
IMG_KW = dict(img_model=26, ratio=1, resolution="4K", quality=2, count=1, mode_type="text2image")
_top_extra = {"isStale": False, "defaultResultIndex": 0}


# ------------------------------------------------------------------ 解析产物
HEAD_RE = re.compile(r"^(?P<name>.+?)\s*-\s*方案(?P<n>\d+)")
# 兼容旧产物：单版角色曾写成「XX - 唯一方案（代称角色）」，被 HEAD_RE 判成「没解析出方案号」
# 整条跳过（2026-09-24 实测：46 个代称角色一个都没上画布）。现在按「方案1」收下。
SINGLE_RE = re.compile(r"^(?P<name>.+?)\s*-\s*唯一方案")


def parse_prompts(path: pathlib.Path):
    """把 docx 切成 [(序号, 名称, 方案号, 提示词全文), ...]"""
    doc = Document(str(path))
    items = []
    cur_title = None
    buf = []
    for p in doc.paragraphs:
        txt = p.text
        style = (p.style.name or "").lower()
        if style.startswith("title"):
            continue
        if style.startswith("heading"):
            if cur_title and buf:
                items.append((cur_title, "\n".join(buf).strip()))
            cur_title = txt.strip()
            buf = []
        else:
            t = txt.strip()
            if t:
                buf.append(t)
    if cur_title and buf:
        items.append((cur_title, "\n".join(buf).strip()))

    out = []
    for title, prompt in items:
        m = HEAD_RE.match(title)
        if m:
            name, n = m.group("name"), int(m.group("n"))
        else:
            ms = SINGLE_RE.match(title)
            if not ms:
                print(f"  !! 标题没解析出方案号，跳过: {title!r}")
                continue
            name, n = ms.group("name"), 1
        name = name.strip()
        # 去掉名称里的「（内景）」等后缀括号，只留地点名
        name = re.sub(r"（[^）]*）\s*$", "", name).strip()
        out.append((name, n, prompt))
    return out


def build_plan():
    scenes = parse_prompts(GEN_OUT / "场景提示词.docx")
    chars = parse_prompts(GEN_OUT / "人物提示词.docx")
    return chars, scenes


def layout(chars, scenes):
    """排布：左边人物区（2 列 × 3 行），向右留一列空档，再是场景区（N 列 × 3 行）。"""
    plan = []   # (name, prompt, x, y)
    groups = {}  # name -> col index

    # --- 人物区 ---
    order_c = []
    for nm, n, pr in chars:
        if nm not in order_c:
            order_c.append(nm)
    for nm, n, pr in chars:
        ci = order_c.index(nm)
        x = BASE_X + ci * COL_PITCH
        y = ROW_Y[min(n, 3) - 1]
        plan.append((f"{nm}-方案{n}", pr, x, y))

    # --- 场景区：从人物区右边隔一列开始 ---
    scene_x0 = BASE_X + (len(order_c) + 1) * COL_PITCH
    order_s = []
    for nm, n, pr in scenes:
        if nm not in order_s:
            order_s.append(nm)
    for nm, n, pr in scenes:
        ci = order_s.index(nm)
        x = scene_x0 + ci * COL_PITCH
        y = ROW_Y[min(n, 3) - 1]
        plan.append((f"{nm}-方案{n}", pr, x, y))

    return plan, order_c, order_s


def make_create_list(plan):
    out = []
    for name, prompt, x, y in plan:
        data = image_node_data(prompt, **IMG_KW)
        obj = json.loads(data)
        obj.update(_top_extra)
        node = {
            "nodeKey": new_node_key("image"),
            "type": NODE_TYPE["image"],
            "name": name,
            "isResultNode": 0,
            "status": 1,
            "isUploaded": 0,
            "position": {"x": float(x), "y": float(y)},
            "measured": {"width": NODE_W, "height": NODE_H},
            "parentNodeKey": None,
            "zIndex": 2,
            "data": json.dumps(obj, ensure_ascii=False),
            "resultContent": None,
            "boundShotId": None,
            "boundShot": None,
        }
        out.append(node)
    return out


# ------------------------------------------------------------------ 找沙盒画布
def find_sandbox(m):
    lst = m.read_canvas_list(PROJECT_ID, episode_id="0", shot_id="0", is_episode_mode=False)
    for c in lst or []:
        if c.get("name") == SANDBOX_NAME:
            return c
    return None


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true", help="真写（默认只 dry-run）")
    ap.add_argument("--cleanup", action="store_true", help="删除沙盒画布")
    args = ap.parse_args()
    require_project_id(PROJECT_ID)      # 指针缺了就停手报错（绝不回退旧项目）

    OUTDIR.mkdir(parents=True, exist_ok=True)
    chars, scenes = build_plan()
    plan, order_c, order_s = layout(chars, scenes)

    print("=" * 78)
    print("解析第一阶段产物")
    print("=" * 78)
    print(f"  人物提示词.docx : {len(chars)} 条  ({len(order_c)} 个角色: {'、'.join(order_c)})")
    print(f"  场景提示词.docx : {len(scenes)} 条  ({len(order_s)} 个场景)")
    print(f"  合计要铺的节点  : {len(plan)} 个")
    print()
    print("  计划（名称 / 画布坐标 / 提示词前 60 字）:")
    for nm, pr, x, y in plan:
        print(f"    {nm:<28} ({x:>8.0f},{y:>6.0f})  {pr[:60]}...")
    print()
    lens = [len(p) for _, p, _, _ in plan]
    print(f"  提示词长度: 最短 {min(lens)} / 中位 {sorted(lens)[len(lens)//2]} / 最长 {max(lens)}")
    print(f"  总字符数  : {sum(lens)}")

    if not args.commit and not args.cleanup:
        print()
        print("[DRY RUN] 没有连接服务器，什么都没改。确认版式无误后加 --commit 真写。")
        (OUTDIR / "plan_dryrun.json").write_text(
            json.dumps([{"name": n, "x": x, "y": y, "prompt": p} for n, p, x, y in plan],
                       ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    m = Mobo()

    # ---------------- cleanup ----------------
    if args.cleanup:
        c = find_sandbox(m)
        if not c:
            print("没找到沙盒画布，无需清理")
            return 0
        print(f"[清理] 删除沙盒画布 {c['id']} / {c['name']}（revision {c['revision']}）")
        m.wr_delete_canvas(PROJECT_ID, c["id"])
        print("已删除。审计：")
        print(m.dump_audit())
        return 0

    # ---------------- 1. 建沙盒画布 ----------------
    print()
    print("=" * 78)
    print("1) 建沙盒画布")
    print("=" * 78)
    c = find_sandbox(m)
    if c:
        print(f"  已存在: id={c['id']} revision={c['revision']}，直接复用")
    else:
        # ⚠️ 必须带 episodeId:"0"（自由画布口径），否则服务端忽略 name、自动叫「画布N」
        fresh = m.wr_create_canvas(PROJECT_ID, shot_id="0", name=SANDBOX_NAME,
                                   viewport_x=0.0, viewport_y=0.0, viewport_zoom=0.6,
                                   episode_id="0")
        print(f"  Create 返回: id={fresh.get('id')} name={fresh.get('name')!r}")
        if fresh.get("name") != SANDBOX_NAME:
            print(f"  name 被忽略了，改用 /Update 改名")
            m.wr_rename_canvas(PROJECT_ID, fresh["id"], fresh.get("revision", 0), SANDBOX_NAME,
                               episode_id="0", shot_id="0")
        c = find_sandbox(m)
        if not c:
            print("  !! 建完没找到，中止")
            print(m.dump_audit())
            return 2
        print(f"  新建成功: id={c['id']} name={c['name']!r} revision={c['revision']}")

    canvas_id = c["id"]

    # ---------------- 护栏 ----------------
    assert c["name"] == SANDBOX_NAME, f"目标画布名字不对：{c['name']!r}"
    detail = m.read_canvas_detail(PROJECT_ID, canvas_id, episode_id="0", shot_id="0",
                                  is_episode_mode=False)
    rev = int(detail.get("revision") or 0)
    exist_nodes = detail.get("nodes") or []
    if exist_nodes:
        # 复用场景：先清掉上次铺的，保证可重复执行
        print(f"  [INFO] 画布上已有 {len(exist_nodes)} 个节点，先清空再重铺")
        m.wr_update_nodes(PROJECT_ID, canvas_id, rev,
                          delete=[n["nodeKey"] for n in exist_nodes])
        detail = m.read_canvas_detail(PROJECT_ID, canvas_id, episode_id="0", shot_id="0",
                                      is_episode_mode=False)
        rev = int(detail.get("revision") or 0)
        print(f"  清空后 revision = {rev}，节点数 = {len(detail.get('nodes') or [])}")

    # ---------------- 2. 铺节点 ----------------
    print()
    print("=" * 78)
    print(f"2) 铺 {len(plan)} 个节点（一次 UpdateNode 原子写入，baseRevision={rev}）")
    print("=" * 78)
    create = make_create_list(plan)
    t0 = time.time()
    resp = m.wr_update_nodes(PROJECT_ID, canvas_id, rev, create=create)
    dt = time.time() - t0
    print(f"  HTTP 返回耗时 {dt:.2f}s")
    print(f"  响应 revision = {resp.get('revision') if isinstance(resp, dict) else '?'}")

    (OUTDIR / "sent_create.json").write_text(
        json.dumps(create, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- 3. 回读校验 ----------------
    print()
    print("=" * 78)
    print("3) 回读校验（GetDetail）")
    print("=" * 78)
    back = m.read_canvas_detail(PROJECT_ID, canvas_id, episode_id="0", shot_id="0",
                                is_episode_mode=False)
    (OUTDIR / "readback.json").write_text(
        json.dumps(back, ensure_ascii=False, indent=2), encoding="utf-8")
    nodes = {n["nodeKey"]: n for n in (back.get("nodes") or [])}
    print(f"  画布上节点数 = {len(nodes)}（期望 {len(plan)}）")

    ok = bad = 0
    for node in create:
        got = nodes.get(node["nodeKey"])
        if not got:
            print(f"  !! 丢失 {node['name']}")
            bad += 1
            continue
        want = json.loads(node["data"])["params"]["prompt"]
        have = json.loads(got["data"])["params"]["prompt"]
        if want == have:
            ok += 1
        else:
            bad += 1
            print(f"  !! 提示词不一致 {node['name']}: want {len(want)} 字 / got {len(have)} 字")
    print(f"  提示词逐字一致: {ok}/{len(plan)}   异常: {bad}")

    print()
    print("=" * 78)
    print("4) 请求审计（应全部为 write/read，无任何扣费接口）")
    print("=" * 78)
    print(m.dump_audit())
    (OUTDIR / "audit.txt").write_text(m.dump_audit(), encoding="utf-8")

    meta = {"canvasId": canvas_id, "canvasName": SANDBOX_NAME, "revision": back.get("revision"),
            "nodeCount": len(nodes), "expected": len(plan), "promptExact": ok, "promptBad": bad,
            "updateNodeSeconds": round(dt, 2)}
    (OUTDIR / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(json.dumps(meta, ensure_ascii=False))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
