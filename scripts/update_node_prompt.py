#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""按批注返工：把新提示词写回**原画布节点**（覆盖，不新建方案号），可选直接重新提交出图。

    python scripts/update_node_prompt.py --node "公爵塔楼卧房-方案1" --from-docx 剧本.docx
    python scripts/update_node_prompt.py --node "A-方案2" --text "新提示词全文" --commit
    python scripts/update_node_prompt.py --node "A-方案1" --from-docx 剧本.docx --commit --max-spend 1.0

要点：
  · 只改 `prompt / imagePrompt / screenPrompt` 三个字段，**不动 `imageList` / `mixedList`**；
    但两者若本来为空（参考图丢了），会从 `mixedList`/`imageList` 互相补齐并提示。
  · 写完逐字回读校验，不一致就停手。
  · `--commit` = 付费重新出图，默认不加。
"""

import argparse
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "core"))
sys.path.insert(0, str(HERE / "tools"))

import mc  # noqa: E402


def load_new_prompts(args):
    """返回 {节点名: 新提示词}"""
    if args.text:
        return {n: args.text.strip() for n in args.node}

    if not args.from_docx:
        raise SystemExit("!! 需要 --from-docx <docx> 或 --text \"<新提示词>\"")

    from scene_frontface import read_scene_docx  # noqa: E402
    rec = read_scene_docx(pathlib.Path(args.from_docx))

    out = {}
    for name in args.node:
        if "-方案" not in name:
            raise SystemExit(f"!! 节点名要形如「场景名-方案N」：{name}")
        scene, _, ver = name.rpartition("-方案")
        item = rec.get(scene)
        if not item:
            raise SystemExit(f"!! docx 里找不到场景「{scene}」")
        txt = (item["variants"] or {}).get(ver)
        if not txt or len(txt) < 60:
            raise SystemExit(f"!! docx 里「{scene} 方案{ver}」没有可用提示词")
        out[name] = txt.strip()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", action="append", required=True, help="节点名，可重复")
    ap.add_argument("--from-docx", default=None, help="从这份 docx 取新提示词")
    ap.add_argument("--text", default=None, help="直接给提示词全文（单场景返工时用）")
    ap.add_argument("--project", default=None)
    ap.add_argument("--canvas", default=None)
    ap.add_argument("--commit", action="store_true", help="写完后立即重新提交出图（付费）")
    ap.add_argument("--max-spend", type=float, default=None, help="配合 --commit 的花费上限")
    a = ap.parse_args()

    prompts = load_new_prompts(a)
    pid = mc.require_project_id(a.project)
    cid = mc.require_canvas_id(a.canvas)

    m = mc.Mobo()
    d = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
    rev = int(d.get("revision") or 0)
    nodes = {n.get("name"): n for n in (d.get("nodes") or [])}

    updates, problems = [], []
    for name, prompt in prompts.items():
        node = nodes.get(name)
        if not node:
            problems.append(f"画布上没有节点「{name}」")
            continue
        data = json.loads(node["data"])
        p = data.setdefault("params", {})
        il, ml = p.get("imageList") or [], p.get("mixedList") or []
        if not il and ml:
            p["imageList"] = list(ml)
            print(f"  [补] {name} imageList 为空，已从 mixedList 补齐")
        elif il and not ml:
            p["mixedList"] = list(il)
            print(f"  [补] {name} mixedList 为空，已从 imageList 补齐")
        elif not il and not ml:
            print(f"  [!] {name} 参考图为空（这版本来就是纯文生图）")
        p["prompt"] = prompt
        p["imagePrompt"] = prompt
        p["screenPrompt"] = prompt
        item = dict(node)
        item["data"] = json.dumps(data, ensure_ascii=False)
        updates.append(item)
        print(f"  [改] {name}  （{len(prompt)} 字）")

    if problems:
        print("\n".join("!! " + x for x in problems))
    if not updates:
        return 2

    r = m.wr_update_nodes(pid, cid, rev, update=updates)
    print(f"\nUpdateNode → revision={(r or {}).get('revision') if isinstance(r, dict) else r}")

    back = m.read_canvas_detail(pid, cid, episode_id="0", shot_id="0", is_episode_mode=False)
    got = {n["nodeKey"]: n for n in (back.get("nodes") or [])}
    ok = 0
    for u in updates:
        cur = got.get(u["nodeKey"])
        if not cur:
            continue
        cp = json.loads(cur["data"]).get("params") or {}
        want = json.loads(u["data"])["params"]
        if cp.get("prompt") == want["prompt"] and cp.get("imageList") and cp.get("mixedList"):
            ok += 1
        else:
            print(f"  !! {u['name']} 回读不一致")
    print(f"回读校验：{ok}/{len(updates)} 一致且参考图完整")

    if not a.commit:
        print("\n（未提交出图。确认后加 --commit --max-spend N 重跑）")
        return 0 if ok == len(updates) else 3

    if ok != len(updates):
        print("!! 有节点校验未通过，先别提交")
        return 3

    print(f"\n>>> 重新提交 {len(updates)} 个节点出图")
    cmd = [sys.executable, "-X", "utf8", str(HERE / "core" / "submit_canvas2.py")]
    for u in updates:
        cmd += ["--node", u["name"]]
    cmd += ["--commit"]
    if a.max_spend:
        cmd += ["--max-spend", str(a.max_spend)]
    r2 = subprocess.run(cmd, capture_output=True)
    sys.stdout.write((r2.stdout or b"").decode("utf-8", "replace")[-1500:])
    return r2.returncode


if __name__ == "__main__":
    sys.exit(main())
