# -*- coding: utf-8 -*-
"""canvas_fetch.py —— 「等出图 → 下载」公共件（只读，零扣费）

抽出来的原因：`fanda.py` 与 `canvas_gen.py` 都要轮询 + 下载，别再各写一份。
（后续可把它并入 `core/asset/finish.py`，届时两处都改调 finish —— 见 README 纪律第 12 条。）

用法：
    import sys; sys.path.insert(0, r"<工作区>/core/ops")
    from canvas_fetch import wait_and_download
    got = wait_and_download(m, pid, cid, name_prefix="示例E21_", out_dir=pathlib.Path(r"<素材目录>"),
                            episode_id="742273", is_episode_mode=True)
"""
import pathlib
import time


def node_by_id(m, project_id, canvas_id, node_id, episode_id="0", shot_id="0",
               is_episode_mode=False):
    """按 id 取单个节点的最新状态"""
    d = m.read_canvas_detail(project_id, canvas_id, episode_id=episode_id, shot_id=shot_id,
                             is_episode_mode=is_episode_mode)
    return next((n for n in (d.get("nodes") or []) if str(n.get("id")) == str(node_id)), None)


def wait_and_download(m, project_id, canvas_id, *, name_prefix=None, out_dir,
                      episode_id="0", shot_id="0", is_episode_mode=False,
                      interval=20, timeout=900, log=print, skip_existing=True):
    """轮询 → 把 status==3 且 resultContent 有值的节点图下载到 out_dir。

    返回 [{"name":..., "path":..., "bytes":...}]；超时的留在 'pending' 里报告。
    """
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    d = m.read_canvas_detail(project_id, canvas_id, episode_id=episode_id, shot_id=shot_id,
                             is_episode_mode=is_episode_mode)
    targets = [n for n in (d.get("nodes") or [])
               if str(n.get("name") or "").startswith(name_prefix or "")] if name_prefix else []
    if not targets:
        log("  （没有匹配 name_prefix=%r 的节点）" % name_prefix)
        return []
    log("等 %d 个节点出图（interval=%ss timeout=%ss）" % (len(targets), interval, timeout))

    done, pending, t0 = [], list(targets), time.time()
    while pending and time.time() - t0 < timeout:
        for n in list(pending):
            cur = node_by_id(m, project_id, canvas_id, n.get("id"),
                             episode_id=episode_id, shot_id=shot_id,
                             is_episode_mode=is_episode_mode)
            st = (cur or {}).get("status")
            url = str((cur or {}).get("resultContent") or "").strip()
            nm = str((cur or {}).get("name") or n.get("name"))
            if st in (3, 4) and not url and st == 4:
                log("  ✗ %s 失败（status=4）" % nm)
                pending.remove(n)
                continue
            if st == 3 and url:
                dest = out_dir / ("%s.png" % nm)
                if skip_existing and dest.exists() and dest.stat().st_size > 1024:
                    log("  ⏭ %s 已存在，跳过" % dest.name)
                    done.append({"name": nm, "path": dest, "bytes": dest.stat().st_size})
                    pending.remove(n)
                    continue
                try:
                    r = m.s.get(url, timeout=180)
                    r.raise_for_status()
                    dest.write_bytes(r.content)
                    log("  ↓ %s (%d KB)" % (dest.name, len(r.content) // 1024))
                    done.append({"name": nm, "path": dest, "bytes": len(r.content)})
                    pending.remove(n)
                except Exception as e:
                    log("  !! 下载失败 %s：%s" % (nm, e))
            else:
                log("  … %s status=%s" % (nm, st))
        if pending:
            time.sleep(interval)
    if pending:
        log("  ⚠️ 超时未完成：%s" % "、".join(str(n.get("name")) for n in pending))
    log("完成 %d 个 → %s" % (len(done), out_dir))
    return done
