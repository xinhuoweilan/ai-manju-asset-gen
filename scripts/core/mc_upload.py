# -*- coding: utf-8 -*-
"""平台图片上传模块（2026-10-06 打通）。

链路（实测）：
    POST /api/Common/CosCredential/GetUploadCredentialV2   → 腾讯云 COS 临时凭证
    PUT  {uploadDomain}{cos_key}  (COS V5 签名, 带 x-cos-security-token)  → 200
    可访问 URL = {accessDomain}{cos_key}

⚠️ 注意：**不要**用 /api/Common/Upload/Add —— 实测 HTTP 403 空响应（该口对当前凭据关闭）。
   前端代码里走的是 COS 直传分支（`R=()=>!e.cc` 为真时）。

用法：
    from mc_upload import upload_file
    info = upload_file("C:/path/to/x.png")
    # info = {"id":"", "name":"x.png", "path": cos_key, "url": cos_key, "httpPath": 可访问URL}
"""
import hashlib
import hmac
import json
import pathlib
import sys
import time
import urllib.parse

import requests

HERE = pathlib.Path(__file__).resolve().parent.parent  # ★ 整理器：HERE=工作区根目录（本文件在 core）
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from mc import BASE, TOKEN_FILE  # noqa: E402

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")

_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
         ".mp4": "video/mp4", ".mov": "video/quicktime"}


def _headers(token):
    return {
        "accept": "application/json, text/plain, */*",
        "authorization": "Bearer " + token,
        "areainterface": "cn",
        "origin": BASE,
        "referer": BASE + "/",
        "user-agent": _UA,
    }


def get_cos_credential(token=None, timeout=60):
    """取腾讯云 COS 临时上传凭证。**只读，不扣费。**"""
    if token is None:
        token = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["token"]
    r = requests.post(BASE + "/api/Common/CosCredential/GetUploadCredentialV2",
                      headers={**_headers(token), "content-type": "application/json"},
                      json={}, timeout=timeout)
    r.raise_for_status()
    return r.json()["data"]


def _cos_v5_auth(method, url, cred, expires=3600):
    """腾讯云 COS V5 签名（sha1），仅签 host 头。"""
    host = urllib.parse.urlparse(url).netloc
    uri_path = urllib.parse.urlparse(url).path
    now = int(time.time())
    key_time = f"{now - 60};{now + expires}"
    sign_time = key_time

    http_string = f"{method.lower()}\n{uri_path}\n\nhost={host}\n"
    sha1_http = hashlib.sha1(http_string.encode("utf-8")).hexdigest()
    string_to_sign = f"sha1\n{sign_time}\n{sha1_http}\n"
    sign_key = hmac.new(cred["tmpSecretKey"].encode("utf-8"),
                        key_time.encode("utf-8"), hashlib.sha1).hexdigest()
    signature = hmac.new(sign_key.encode("utf-8"),
                         string_to_sign.encode("utf-8"), hashlib.sha1).hexdigest()
    return (f"q-sign-algorithm=sha1&q-ak={cred['tmpSecretId']}&q-sign-time={sign_time}"
            f"&q-key-time={key_time}&q-header-list=host&q-url-param-list="
            f"&q-signature={signature}")


def upload_file(local_path, *, name=None, token=None, timeout=180, refresh_cred=True):
    """把一个本地文件传到平台 COS，返回前端同构的文件对象。

    返回 {"id", "name", "path", "url", "httpPath"}：
        path / url  = cos_key（服务端相对路径，形如 /academywebapi_files/upload/...）
        httpPath    = 可直接 GET 的完整 URL

    ⚠️ 只上传，不建节点、不扣费。
    """
    p = pathlib.Path(local_path)
    if not p.exists():
        raise FileNotFoundError(p)
    fname = name or p.name
    mime = _MIME.get(p.suffix.lower(), "application/octet-stream")
    raw = p.read_bytes()

    d = get_cos_credential(token=token)
    key_prefix = d["key"]
    if not key_prefix.endswith("/"):
        key_prefix += "/"
    cos_key = key_prefix + fname
    obj_url = d["uploadDomain"] + cos_key
    http_path = d["accessDomain"] + cos_key
    cred = d["credentials"]

    auth = _cos_v5_auth("put", obj_url, cred)
    host = urllib.parse.urlparse(obj_url).netloc
    rr = requests.put(obj_url, headers={
        "Authorization": auth,
        "Host": host,
        "Content-Type": mime,
        "x-cos-security-token": cred["sessionToken"],
    }, data=raw, timeout=timeout)
    if rr.status_code not in (200, 204):
        raise RuntimeError(f"COS PUT 失败 HTTP {rr.status_code}: {rr.text[:300]}")

    return {
        "id": "",
        "name": fname,
        "path": cos_key,
        "url": cos_key,
        "httpPath": http_path,
        "_etag": rr.headers.get("ETag", ""),
        "_size": len(raw),
    }


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        print("用法: python mc_upload.py <本地文件> [远端文件名]")
        sys.exit(2)
    nm = sys.argv[2] if len(sys.argv) > 2 else None
    info = upload_file(src, name=nm)
    print(json.dumps(info, ensure_ascii=False, indent=2))
