# -*- coding: utf-8 -*-
"""
平台 REST 客户端。

只封装**已侦察确认**的接口。设计原则：
  * 每个请求都记进 self.audit（方法/路径/是否只读），方便事后审计「有没有碰扣费接口」
  * 只读方法以 read_ 开头；写方法以 wr_ 开头；扣费方法单独以 pay_ 开头并显式标注
  * 默认不提供任何扣费调用 —— 需要时由调用方自己决定

域名/前缀（侦察结论）：
  域名  由 .env 的 PLATFORM_BASE_URL 决定
  前缀  /api/Canvas   /api/Project   /api/OneClickRedraw
"""
import json
import os
import pathlib
import random
import string
import time

import sys

import requests

HERE = pathlib.Path(__file__).resolve().parent.parent  # scripts/（本文件在 scripts/core）
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from paths import CFG  # noqa: E402  统一配置：所有地址/凭证/目录都从 .env 读

BASE = CFG.require_platform()   # 平台地址（.env → PLATFORM_BASE_URL）
TOKEN_FILE = CFG.token_file     # 可选兜底：.env → TOKEN_FILE（默认 scripts/token.json）

# 节点类型枚举（来自前端 chunk）
NODE_TYPE = {"text": 1, "image": 2, "video": 3, "audio": 4, "group": 5, "director": 6, "video-editor": 7}
NODE_TYPE_NAME = {v: k for k, v in NODE_TYPE.items()}
NODE_KEY_PREFIX = {"text": "t", "image": "i", "video": "v", "video-editor": "ve", "audio": "a", "group": "g", "director": "d"}

# ------------------------------------------------------------ 默认项目 / 画布
# 工作区指针文件：开工时由 prep.py 写入，换剧会自动覆盖。
# ⚠️ 2026-09-27 修：原先有 8 个脚本把 PROJECT_ID 硬编码成 123456，
#    换到别的项目后 find_canvas / read_canvas_detail 会拿旧项目去查，
#    整个列表里找不到目标画布（报「找不到 canvasId」）。统一走这里。
WORKDIR = CFG.work_dir
POINTER_FILE = WORKDIR / "current_canvas.json"

# ⚠️ 2026-09-27 二次收口：**这里不再保留任何「读不到就用旧项目 / 旧画布」的兜底。**
#    旧版有两个兜底常量 —— FALLBACK_PROJECT_ID="123456"、FALLBACK_CANVAS="12345"。
#    后果：指针文件一丢（或写坏），付费的出图脚本不会报错，而是**静默跑回上一个剧组**，
#    钱花在别的项目上、界面上却是空的 —— 这比明写硬编码更难发现。
#    ⇒ 现在读不到就 require_*_id() 直接停手报错。**宁可停，不可猜。**

POINTER_HINT = (
    "读不到项目 / 画布配置：\n"
    f"    指针文件：{POINTER_FILE}（不存在，或里面缺 projectId / canvasId）\n"
    "    请在仓库根目录的 .env 里填好 PROJECT_ID / CANVAS_ID（怎么拿见 API-清单.md 第一节），\n"
    "    或运行 python scripts/check_env.py 自检。"
)


def load_pointer() -> dict:
    """读工作区里的 current_canvas.json；读不到就返回空 dict，绝不抛。"""
    try:
        return json.loads(POINTER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def default_project_id() -> str:
    """项目 id：优先 .env 的 PROJECT_ID，其次工作区指针文件。**都没有就是空串，绝不回退旧项目。**"""
    return str(CFG.project_id or load_pointer().get("projectId") or "").strip()


def default_canvas_id() -> str:
    """画布 id：优先 .env 的 CANVAS_ID，其次工作区指针文件。"""
    return str(CFG.canvas_id or load_pointer().get("canvasId") or "").strip()


def require_project_id(cli_value=None) -> str:
    """要一个项目 id。命令行没给、指针里也没有 ⇒ 停手报错（不猜、不回退）。"""
    pid = str(cli_value or "").strip() or default_project_id()
    if not pid:
        raise SystemExit("!! " + POINTER_HINT)
    return pid


def require_canvas_id(cli_value=None) -> str:
    """要一个画布 id。命令行没给、指针里也没有 ⇒ 停手报错。"""
    cid = str(cli_value or "").strip() or default_canvas_id()
    if not cid:
        raise SystemExit("!! " + POINTER_HINT)
    return cid


class MoboError(RuntimeError):
    def __init__(self, msg, path="", http=None, body=None, envelope=None):
        super().__init__(msg)
        self.path = path
        self.http = http
        self.body = body
        self.envelope = envelope


def new_node_key(kind: str, n: int = 13) -> str:
    """仿前端 nodeKey：<前缀>-<13位字母数字>。"""
    alphabet = string.ascii_letters + string.digits
    return f"{NODE_KEY_PREFIX.get(kind, 'n')}-" + "".join(random.choice(alphabet) for _ in range(n))


def image_node_data(prompt: str, *, img_model: int = 37, ratio: int = 2, resolution: str = "2K",
                    quality: int = 3, count: int = 1, mode_type: str = "text2image") -> str:
    """图片节点的 data 串（照抄前端默认 payload）。"""
    obj = {
        "url": [], "sourceSeedanceType": 0, "alt": "图片",
        "action": "image_generate", "generatorType": "default",
        "params": {
            "prompt": prompt, "imagePrompt": prompt,
            "model": "", "imgModel": img_model, "imageEditType": 0, "enhance": False, "count": count,
            "lightingEffects": [],
            "settings": {"resolution": resolution, "ratio": ratio, "quality": quality},
            "advancedSettings": {},
            "cameraControl": {"enabled": False, "camera": "panavision_dxl2",
                              "lens": "arri_signature_prime", "focal": "35mm", "aperture": "f/4"},
            "textList": [], "imageList": [], "videoList": [], "audioList": [], "mixedList": [],
            "modeType": mode_type,
        },
        "isStale": False,
    }
    return json.dumps(obj, ensure_ascii=False)


def video_node_data(prompt: str, *, model: str = "5", build_time: int = 5, ratio: int = 2,
                    resolution: str = "720p", count: int = 1, mode_type: str = "text2video",
                    screen_prompt: str | None = None) -> str:
    """视频节点的 data 串。"""
    obj = {
        "url": [], "sourceSeedanceType": 0, "poster": "",
        "action": "video_generate", "generatorType": "default",
        "params": {
            "prompt": prompt, "model": model, "line": 64, "buildTime": build_time,
            "aspectRatio": ratio, "modeType": mode_type, "count": count,
            "imageList": [], "videoList": [], "audioList": [],
            "settings": {"resolution": resolution, "enableSound": "on"},
            "advancedSettings": {"search_enabled": 1, "autoCompliance": 1},
            "textList": [], "mixedList": [], "cameraPresets": [], "effectPresets": [],
            "screenPrompt": screen_prompt if screen_prompt is not None else prompt,
        },
        "isStale": False,
    }
    return json.dumps(obj, ensure_ascii=False)


def make_node(kind: str, name: str, prompt: str, x: float, y: float, *, node_key: str | None = None,
              width: float = 220.0, height: float = 300.0, z_index: int = 2, **kw) -> dict:
    """构造一个可放进 create[] 的节点对象。"""
    t = NODE_TYPE[kind]
    data = image_node_data(prompt, **kw) if t == 2 else video_node_data(prompt, **kw)
    return {
        "nodeKey": node_key or new_node_key(kind),
        "type": t,
        "name": name,
        "isResultNode": 0,
        "status": 1,
        "isUploaded": 0,
        "position": {"x": float(x), "y": float(y)},
        "measured": {"width": float(width), "height": float(height)},
        "parentNodeKey": None,
        "zIndex": z_index,
        "data": data,
        "resultContent": None,
    }


class Mobo:
    def __init__(self, token: str | None = None, timeout: int = 60, verbose: bool = True):
        self.timeout = timeout
        self.verbose = verbose
        self.audit: list[dict] = []
        if token is None:
            token = CFG.require_token()
            self.token_exp = 0
        else:
            self.token_exp = 0
        self.token = token
        self.s = requests.Session()
        self.s.headers.update({
            "accept": "application/json, text/plain, */*",
            "authorization": "Bearer " + token,
            "content-type": "application/json",
            "areainterface": "cn",
            "origin": BASE,
            "referer": BASE + "/",
            "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"),
        })

    def clone(self) -> "Mobo":
        """并发用：复制一个共享审计记录、但独立 Session 的实例。

        requests.Session 不是线程安全的（连接池/状态），所以并发提交时
        每个线程用自己的 clone；audit 列表是共享的（list.append 在 GIL 下原子）。
        """
        c = Mobo(token=self.token, timeout=self.timeout, verbose=self.verbose)
        c.token_exp = self.token_exp
        c.audit = self.audit
        return c

    # ------------------------------------------------------------------ 底层
    def _call(self, method: str, path: str, body=None, params=None, *, label="", raw=False):
        url = BASE + path
        t0 = time.time()
        try:
            if method == "GET":
                r = self.s.get(url, params=params, timeout=self.timeout)
            else:
                r = self.s.post(url, json=(body if body is not None else {}), timeout=self.timeout)
        except Exception as e:
            self.audit.append({"m": method, "p": path, "label": label, "err": repr(e)})
            raise MoboError(f"{path} 请求失败: {e!r}", path=path, body=body) from e
        dt = time.time() - t0
        self.audit.append({"m": method, "p": path, "label": label, "http": r.status_code, "s": round(dt, 2)})
        try:
            j = r.json()
        except Exception:
            raise MoboError(f"{path} 非 JSON（HTTP {r.status_code}）: {r.text[:300]}",
                            path=path, http=r.status_code, body=body)
        if r.status_code != 200:
            raise MoboError(f"{path} HTTP {r.status_code}: {json.dumps(j, ensure_ascii=False)[:300]}",
                            path=path, http=r.status_code, body=body, envelope=j)
        if raw:
            return j
        if isinstance(j, dict):
            # ★ 2026-10-07 修：先验 isSuccess/code，再剥 data ——
            #   旧顺序「data 键存在就 return j["data"]」会把 {data:null, code:202,
            #   isSuccess:false} 吞成 None（画布被删时 GetDetail 静默返回 None 的根因）。
            if j.get("status") is False:
                raise MoboError(f"{path} status=false: {json.dumps(j, ensure_ascii=False)[:300]}",
                                path=path, http=200, body=body, envelope=j)
            if j.get("isSuccess") is False:
                raise MoboError(f"{path} isSuccess=false: {json.dumps(j, ensure_ascii=False)[:300]}",
                                path=path, http=200, body=body, envelope=j)
            code = j.get("code")
            if code not in (None, 0, 200, "200"):
                raise MoboError(f"{path} code={code}: {j.get('message')}",
                                path=path, http=200, body=body, envelope=j)
            if "data" in j:
                return j["data"]
            return j
        return j

    def post(self, path, body=None, **kw):
        return self._call("POST", path, body=body, **kw)

    def get(self, path, params=None, **kw):
        return self._call("GET", path, params=params, **kw)

    # ------------------------------------------------------- 只读：项目/账号
    def read_project(self, pid):
        return self.get("/api/Project/GetProject", {"id": str(pid)}, label="read:project")

    def read_select_items(self):
        return self.post("/api/Project/GetSelectItems", {}, label="read:select_items")

    def read_price(self, model_type, _id=None, template_id=None):
        """只读估价。POST /api/Project/CalculatePrice {modelType, id?/templateId?}"""
        body = {"modelType": model_type}
        if template_id is not None:
            body["id"] = 0
            body["templateId"] = template_id
        elif _id is not None:
            body["id"] = _id
        return self.post("/api/Project/CalculatePrice", body, label="read:price")

    def read_un_projects(self, page_size=50, key=""):
        return self.post("/api/Project/GetUniversalNodeProjectList",
                         {"pageIndex": 1, "pageSize": page_size, "key": key, "isTest": 0},
                         label="read:un_projects")

    # ------------------------------------------------------------ 只读：画布
    def read_canvas_list(self, project_id, episode_id="0", shot_id="0", is_episode_mode=True):
        return self.post("/api/Canvas/GetList",
                         {"projectId": str(project_id), "episodeId": str(episode_id),
                          "shotId": str(shot_id), "isEpisodeMode": bool(is_episode_mode)},
                         label="read:canvas_list")

    def read_canvas_detail(self, project_id, canvas_id, episode_id="0", shot_id="0", is_episode_mode=True):
        body = {"projectId": str(project_id), "episodeId": str(episode_id),
                "shotId": str(shot_id), "canvasId": str(canvas_id)}
        return self.post("/api/Canvas/GetDetail", body, label="read:canvas_detail")

    def read_shots(self, project_id, episode_id):
        return self.post("/api/Canvas/GetEpisodeShotsByEpisodeId",
                         {"projectId": str(project_id), "episodeId": str(episode_id)},
                         label="read:shots")

    # ------------------------------------------------------------ 写：画布
    def wr_create_canvas(self, project_id, shot_id, name, viewport_x=0.0, viewport_y=0.0, viewport_zoom=1.0,
                         episode_id=None):
        body = {"projectId": str(project_id), "shotId": str(shot_id), "name": name,
                "viewportX": viewport_x, "viewportY": viewport_y, "viewportZoom": viewport_zoom}
        if episode_id is not None:
            body["episodeId"] = str(episode_id)
        return self.post("/api/Canvas/Create", body, label="write:create_canvas")

    def wr_delete_canvas(self, project_id, canvas_id, episode_id="0", shot_id="0"):
        return self.post("/api/Canvas/Delete",
                         {"projectId": str(project_id), "episodeId": str(episode_id),
                          "shotId": str(shot_id), "canvasId": str(canvas_id)},
                         label="write:delete_canvas")

    def wr_rename_canvas(self, project_id, canvas_id, base_revision, name,
                         episode_id="0", shot_id="0"):
        """改名 = /Update 带 name 字段（前端 renameCanvas 的实现）。"""
        return self.post("/api/Canvas/Update",
                         {"projectId": str(project_id), "episodeId": str(episode_id),
                          "shotId": str(shot_id), "canvasId": str(canvas_id),
                          "baseRevision": base_revision, "name": name},
                         label="write:rename_canvas")

    def wr_update_nodes(self, project_id, canvas_id, base_revision, create=None, update=None, delete=None,
                        episode_id=None, shot_id="0"):
        body = {"projectId": str(project_id), "shotId": str(shot_id),
                "canvasId": str(canvas_id), "baseRevision": base_revision}
        if episode_id is not None:
            body["episodeId"] = str(episode_id)
        if create:
            body["create"] = create
        if update:
            body["update"] = update
        if delete:
            body["delete"] = delete
        return self.post("/api/Canvas/UpdateNode", body, label="write:update_nodes")

    def wr_update_connections(self, project_id, canvas_id, base_revision, create=None, update=None, delete=None,
                              shot_id="0"):
        body = {"projectId": str(project_id), "shotId": str(shot_id),
                "canvasId": str(canvas_id), "baseRevision": base_revision}
        if create:
            body["create"] = create
        if update:
            body["update"] = update
        if delete:
            body["delete"] = delete
        return self.post("/api/Canvas/UpdateConnection", body, label="write:update_connections")

    # ------------------------------------------------------ 写：通用工程设置
    # ================================================================ 扣费接口
    # ⚠️ 以下方法**会真实扣钱**。调用前必须已获用户明确同意，且先用 read_price 估价。
    def pay_create_generation_task(self, project_id, canvas_id, node_id, data,
                                   episode_id="0", shot_id="0", is_episode_mode=None):
        """提交生成任务（扣钱）。payload 照抄前端 image-submit：

            {projectId, episodeId:"0", shotId:"0", canvasId, nodeId:<后端节点id>, data}
            data(图片) = {createNum, imagePrompt, imgModel, imageEditType,
                          enhance, aspectRatio, imageQuality, resolution, files}
        返回 {taskBatchNo, tasks:[...]}
        """
        body = {"projectId": str(project_id), "episodeId": str(episode_id),
                "shotId": str(shot_id), "canvasId": str(canvas_id),
                "nodeId": node_id, "data": data}
        if is_episode_mode is not None:
            body["isEpisodeMode"] = bool(is_episode_mode)
        return self.post("/api/Canvas/CreateGenerationTask", body,
                         label="PAY:create_generation_task")

    # ------------------------------------------------------------ 只读：任务
    def read_generation_task(self, project_id, canvas_id, node_id, page_index=1, page_size=20,
                             episode_id="0", shot_id="0"):
        """任务列表/轮询（只读，不扣钱）。"""
        return self.post("/api/Canvas/GetGenerationTask",
                         {"projectId": str(project_id), "episodeId": str(episode_id),
                          "shotId": str(shot_id), "canvasId": str(canvas_id),
                          "nodeId": node_id,
                          "pageIndex": int(page_index),
                          "pageSize": min(100, max(1, int(page_size)))},
                         label="read:generation_task")

    def wr_set_episode_canvas_episode(self, project_id, episode_id, shot_id="0", is_episode_mode=True,
                                      canvas_id=None, base_revision=None, viewport_x=None, viewport_y=None,
                                      viewport_zoom=None):
        body = {"projectId": str(project_id), "shotId": str(shot_id), "episodeId": str(episode_id),
                "isEpisodeMode": bool(is_episode_mode)}
        if canvas_id is not None:
            body["canvasId"] = str(canvas_id)
        if base_revision is not None:
            body["baseRevision"] = base_revision
        if viewport_x is not None:
            body["viewportX"] = viewport_x
        if viewport_y is not None:
            body["viewportY"] = viewport_y
        if viewport_zoom is not None:
            body["viewportZoom"] = viewport_zoom
        return self.post("/api/Canvas/Update", body, label="write:update_canvas")

    def wr_initialize_episodes(self, project_id):
        return self.post("/api/Canvas/InitializeEpisodes", {"projectId": str(project_id)},
                         label="write:init_episodes")

    # ------------------------------------------------- 写：自由画布(UniversalNode)
    def wr_un_create_project(self, name):
        return self.post("/api/Project/CreateUniversalNodeProject", {"projectName": name},
                         label="write:un_create_project")

    def wr_un_copy_project(self, source_id, name):
        return self.post("/api/Project/CopyUniversalNodeProject",
                         {"sourceProjectId": str(source_id), "projectName": name.strip()},
                         label="write:un_copy_project")

    def wr_un_rename_project(self, pid, name):
        return self.post("/api/Project/UpdateUniversalNodeProjectName", {"id": str(pid), "projectName": name},
                         label="write:un_rename_project")

    # ------------------------------------------------------------------ 审计
    def dump_audit(self, path=None):
        lines = [f"{a.get('m')} {a.get('p')}  http={a.get('http')} {a.get('s')}s  {a.get('label')}"
                 + (f"  ERR={a['err']}" if a.get("err") else "") for a in self.audit]
        txt = "\n".join(lines)
        if path:
            pathlib.Path(path).write_text(txt, encoding="utf-8")
        return txt
