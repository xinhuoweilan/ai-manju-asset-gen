# -*- coding: utf-8 -*-
"""costume.py —— ★ 人物换装（把「角色 × 造型套 × 场次」翻译成可执行的出图 plan）

分工（不重复造轮子）：
  · 规划：`漫剧提示词生成器/tools/costume_plan.py`（口径 12 条，本脚本只调用）
  · 出图：翻译成 plan.json 后交给 `core/stage/canvas_gen.py` 执行（`--check/--nodes/--commit/--wait`）
  · 上传：出图后调 `core/asset/bind_asset.py` 传到角色库（命名＝**人名-服装名**）

用户口径（2026-10-09）：
  1. 变装形象图**必须在做分镜之前就位**，但**只出用户指定的人物**——所以先给清单等确认；
  2. 出图走**画布生成**，参考＝该角色**定妆图** + 服装描述；
  3. 命名＝**人名 + 服装名**；各套造型挂变体（`makeupStatus=2`），定妆基线保持 `makeupStatus=0`。

━━ 用法 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PY="python"
  cd "<asset-gen>"

  # ① 出清单（只读）：跑规划 → 打印「套 × 覆盖集 × 待填服装描述」→ 写 plan 模板
  "$PY" -X utf8 core/deriv/costume.py --plan --name Elena --drama 示例剧 \
        --script "<ASSET_ROOT>\示例剧\剧本.docx"

  # ② 填服装描述（也可直接编辑生成的 plan.json）
  "$PY" -X utf8 core/deriv/costume.py --fill plans/换装_Elena.plan.json \
        --set "住院病号服=浅蓝白条纹病号服" --set "日常装=米白针织衫配卡其阔腿裤"

  # ③ 出图：交给 canvas_gen
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/换装_Elena.plan.json --check
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/换装_Elena.plan.json --nodes
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/换装_Elena.plan.json --commit --yes
  "$PY" -X utf8 core/stage/canvas_gen.py --plan plans/换装_Elena.plan.json --wait

  # ④ 上传到角色库（命名 人名-服装名）
  "$PY" -X utf8 core/deriv/costume.py --post --name Elena --dir <生成图目录> [--commit]
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(HERE))
import paths  # noqa: E402,F401  ← 统一注入 scripts/core、scripts/src 等搜索路径
sys.path.insert(0, str(HERE / "core" / "asset"))

import mc  # noqa: E402
import mc_platform as mp  # noqa: E402

GEN_DIR = mc.CFG.scripts_root          # 提示词侧脚本目录（scripts/tools、scripts/src）
PLANS = mc.CFG.work_dir / "plans"      # 中间稿放 .env 的 WORK_DIR，不写进仓库
PY = sys.executable

PROMPT_TMPL = ("@ 图片参考绑定（严格按序号对应）：\n\n"
               "@图片1 作为 {actor} 的形象参考\n\n"
               "人物形象图：同一位角色，脸型、五官、发型与参考图完全一致，"
               "服装换成{clothes}；全身正面站姿、面朝镜头，纯灰底，"
               "画面中不出现其他人物与场景元素。\n\n"
               "衣着、发型、体态严格参照形象参考图，保持人物一致性。")


def p(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- 规划
def _run_planner_once(name, script, max_span, gap_break):
    cmd = [PY, "-X", "utf8", str(GEN_DIR / "tools" / "costume_plan.py"), "--name", name,
           "--max-span", str(max_span), "--gap-break", str(gap_break)]
    if script:
        cmd += ["--input", script]
    p("跑规划：--name %s" % name)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore",
                       cwd=str(GEN_DIR))
    print((r.stdout or "")[-700:])
    md = GEN_DIR / "output" / "换装节点" / ("%s.md" % name)
    return md if md.exists() else None


def run_planner(name, script, max_span=15, gap_break=3):
    """调 costume_plan.py 生成/更新 output/换装节点/<角色>.md

    ⚠️ 剧本里的角色名常是**短名**（Elena），角色库里是全名（Elena Vitiello）——
    所以先试全名，不中再退到短名（取第一段 / 括号前）。
    """
    cands = [name]
    short = re.split(r"[\s（(]", name)[0]
    if short and short != name:
        cands.append(short)
    for c in cands:
        md = _run_planner_once(c, script, max_span, gap_break)
        if md:
            return md, c
    p("!! 规划报告没生成（试过：%s）——多半是剧本里没有这个名字的到场场次" % "、".join(cands))
    return None, None


ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$")


def parse_table(md_path):
    """解析「服装套总表」→ [{no, type, look, episodes, scenes}]"""
    rows = []
    for ln in md_path.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(ln.strip())
        if not m:
            continue
        no, typ, look, eps, scenes = [x.strip() for x in m.groups()]
        if no.isdigit() and look and look not in ("造型（占位名）",):
            rows.append({"no": int(no), "type": typ.replace("**", ""),
                         "look": look.replace("**", ""), "episodes": eps, "scenes": scenes})
    return rows


def actor_final_image(m, pid, actor_name):
    """该角色的定妆图（makeupStatus==0 那张）"""
    for a in mp.get_actors(m, pid):
        if mp.actor_name(a) == actor_name or actor_name in mp.actor_name(a):
            fin = mp.pick_final(a)
            if fin:
                return {"assetId": fin.get("id"), "assetName": fin.get("assetName"),
                        "appearanceEpisode": fin.get("appearanceEpisode")}
    return None


def cmd_plan(m, pid, name, drama, script, canvas, episode_id, is_episode_mode, max_span):
    md, used = run_planner(name, script, max_span=max_span)
    if not md:
        return 2
    rows = parse_table(md)
    if not rows:
        p("!! 没能从报告里解析出服装套表（格式变了？看 %s）" % md)
        return 2
    base = actor_final_image(m, pid, name)
    if not base:
        p("!! 角色库里没找到「%s」的定妆图（makeupStatus=0），无法当参考" % name)
        return 2
    p()
    p("★ 「%s」共 %d 套造型（规划用名「%s」；参考＝定妆图 %s" % (
        name, len(rows), used, base["assetId"]))
    p("=" * 96)
    for r in rows:
        p("  %2d. %-26s 覆盖 %-18s %s" % (r["no"], r["look"], r["episodes"], r["type"]))
    p()

    PLANS.mkdir(parents=True, exist_ok=True)
    out = PLANS / ("换装_%s.plan.json" % name.replace(" ", ""))
    items = []
    for i, r in enumerate(rows, 1):
        items.append({
            "name": "换装_%s_%02d_%s" % (name, r["no"], r["look"]),
            "y": 12000 + i * 380,
            "_look": r["look"], "_episodes": r["episodes"],
            "prompt": PROMPT_TMPL.format(actor=name, clothes="<%s 的具体服装描述待填>" % r["look"]),
            "refs": [{"assetId": str(base["assetId"]), "sst": 4,
                      "label": base["assetName"] or name, "nodeKey": ""}],
        })
    plan = {"project": pid, "canvas": canvas, "episode_id": episode_id,
            "is_episode_mode": is_episode_mode, "base_x": 1500, "measured":
            {"width": 625, "height": 350}, "img_model": 26, "aspect_ratio": 1,
            "resolution": "2K", "quality": 3, "items": items}
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    p("plan 模板已写：%s（%d 项）" % (out, len(items)))
    p("→ 下一步：把每套的「具体服装描述」填进 prompt 的 <…待填>（可用 --fill，或直接编辑）")
    p("   然后交给 canvas_gen：--check → --nodes → --commit --yes → --wait")
    p()
    p("⚠️ 按用户口径：**先确认这次要出哪些人物**（本脚本一次只处理一个角色）。")
    return 0


def cmd_fill(plan_path, pairs):
    f = pathlib.Path(plan_path)
    plan = json.loads(f.read_text(encoding="utf-8"))
    mapping = {}
    for kv in pairs:
        k, _, v = kv.partition("=")
        mapping[k.strip()] = v.strip()
    hit = 0
    for it in plan["items"]:
        look = it.get("_look") or ""
        if look in mapping:
            it["prompt"] = re.sub(r"服装换成[^；]*；", "服装换成%s；" % mapping[look], it["prompt"])
            hit += 1
            p("  ✓ %s ← %s" % (look, mapping[look]))
    f.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    p("填了 %d/%d 套 → %s" % (hit, len(plan["items"]), f.name))
    return 0 if hit else 2


# ---------------------------------------------------------------- 上传
def cmd_post(name, img_dir, commit):
    d = pathlib.Path(img_dir)
    files = sorted(d.glob("换装_%s_*.png" % name))
    if not files:
        p("!! %s 里没找到 换装_%s_*.png" % (d, name))
        return 2
    p("将上传 %d 张到角色库（命名＝人名-服装名；默认 dry-run）" % len(files))
    for f in files:
        # 换装_<角色>_<套号>_<造型名>.png → 目标名 <角色>-<造型名>
        parts = f.stem.split("_")
        look = parts[-1] if len(parts) >= 4 else f.stem
        target = "%s-%s" % (name, look)
        cmd = [PY, "-X", "utf8", str(HERE / "core" / "asset" / "bind_asset.py"),
               "--file", str(f), "--target", target]
        if commit:
            cmd += ["--commit"]
        p("  $ %s" % " ".join(cmd[2:]))
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        print((r.stdout or "")[-360:])
    p()
    p("→ 上传后在平台上确认：各套挂「变体」、并标好该套的**出场集数**（appearanceEpisode）。")
    p("   （自动标注集数尚需实测平台接口，先人工确认——见 MEMORY 待办）")
    return 0


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description="人物换装（规划 → plan → 交给 canvas_gen）")
    ap.add_argument("--name", required=True, help="角色名（角色库里的正式名）")
    ap.add_argument("--drama", default=None)
    ap.add_argument("--script", default=None, help="剧本 docx（给 costume_plan 用）")
    ap.add_argument("--project", default=None)
    ap.add_argument("--canvas", default=None, help="画布 id（换装形象图建在哪张画布）")
    ap.add_argument("--episode-id", default="0")
    ap.add_argument("--episode-mode", action="store_true")
    ap.add_argument("--max-span", type=int, default=15)
    ap.add_argument("--plan", action="store_true", help="① 出清单 + 写 plan 模板")
    ap.add_argument("--post", action="store_true", help="④ 上传到角色库")
    ap.add_argument("--fill", default=None, help="② 填服装描述：plan.json 路径")
    ap.add_argument("--set", action="append", default=[], help='配合 --fill："占位名=具体服装"')
    ap.add_argument("--dir", default=None, help="配合 --post：生成图目录")
    ap.add_argument("--commit", action="store_true", help="配合 --post：真上传")
    a = ap.parse_args()

    if a.fill:
        return cmd_fill(a.fill, a.set)
    if a.post:
        if not a.dir:
            p("!! --post 需要 --dir <生成图目录>")
            return 2
        return cmd_post(a.name, a.dir, a.commit)
    if a.plan:
        m = mc.Mobo()
        pid = mc.require_project_id(a.project)
        if not a.canvas:
            p("!! 需要 --canvas（换装形象图建在哪张画布；纪律：不许靠指针猜）")
            return 2
        return cmd_plan(m, pid, a.name, a.drama, a.script, a.canvas,
                        a.episode_id, a.episode_mode, a.max_span)
    p("!! 给 --plan / --fill / --post 之一")
    return 2


if __name__ == "__main__":
    sys.exit(main())
