#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸭鸭图 (SuperSecureMedia) —— 重建版

相对原版的变化:
  1. onedir 打包，不再向 %TEMP% 解包（绕开 "Could not create temporary directory!"）
  2. 解密改在 Python 侧完成，直接读 PNG 原始字节，不经 canvas
     （绕开 WebView2 像素/色彩管理差异导致的"有效载荷损坏"误报）
  3. 内置 ffmpeg，解密出视频后可一键转 MP4
"""

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import webview

import yaya_core

APP_NAME = "鸭鸭图 (SuperSecureMedia)"
HERE = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__))

VIDEO_EXT = {"avi", "mkv", "flv", "wmv", "mov", "m4v", "3gp", "webm"}

# 大文件阈值：超过则建议/强制走路径模式（不经 JS base64 桥）
BIG_FILE = 24 * 1024 * 1024


# --------------------------------------------------------------------------
# 路径
# --------------------------------------------------------------------------
def resource_path(*parts) -> str:
    base = getattr(sys, "_MEIPASS", HERE)
    return os.path.join(base, *parts)


def ffmpeg_path() -> str:
    exe = "ffmpeg.exe"
    for cand in (resource_path("ffmpeg", exe), resource_path(exe),
                 os.path.join(HERE, "ffmpeg", exe), os.path.join(HERE, exe)):
        if os.path.isfile(cand):
            return cand
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


WORKDIR = os.path.join(tempfile.gettempdir(), "YayaTu_work")
JOBS = {}
LOCK = threading.Lock()

# 本地文件服务：把 WORKDIR 与「下载」目录以流式 HTTP 暴露给 WebView2，
# 用于 <video> 预览，避免把大文件 base64 塞进 JS（渲染进程会 OOM）。
_FILE_SERVER = {"server": None, "port": None, "thread": None}
_SERVE_ROOTS = []


def _start_file_server():
    """启动一次性的本地静态文件服务（只绑 127.0.0.1）。"""
    if _FILE_SERVER["server"]:
        return _FILE_SERVER["port"]

    roots = [WORKDIR, default_download_dir()]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            try:
                path = path.lstrip("/")
                # 拒绝路径穿越
                if ".." in path or ":" in path.replace("\\", "/").split("/", 1)[-1][:2]:
                    pass
                cand = None
                for root in roots:
                    p = os.path.abspath(os.path.join(root, path))
                    if not p.startswith(os.path.abspath(root)):
                        continue
                    if os.path.isfile(p):
                        cand = p
                        break
                if not cand:
                    self.send_error(404)
                    return
                size = os.path.getsize(cand)
                rng = self.headers.get("Range")
                ctype = "video/mp4" if cand.lower().endswith(".mp4") else \
                    "video/x-msvideo" if cand.lower().endswith(".avi") else \
                    "application/octet-stream"
                if rng and rng.startswith("bytes="):
                    a, _, b = rng[6:].partition("-")
                    start = int(a) if a else 0
                    end = int(b) if b else size - 1
                    end = min(end, size - 1)
                    self.send_response(206)
                    self.send_header("Content-Range",
                                     "bytes %d-%d/%d" % (start, end, size))
                    length = end - start + 1
                else:
                    start = 0
                    end = size - 1
                    length = size
                    self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(length))
                self.end_headers()
                with open(cand, "rb") as f:
                    f.seek(start)
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(1 << 20, remaining))
                        if not chunk:
                            break
                        try:
                            self.wfile.write(chunk)
                        except Exception:
                            return
                        remaining -= len(chunk)
            except Exception:
                try:
                    self.send_error(500)
                except Exception:
                    pass

        def log_message(self, *a):  # 静音
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    _FILE_SERVER.update(server=srv, port=port, thread=th)
    return port


def _set(job_id, **kw):
    with LOCK:
        JOBS.setdefault(job_id, {}).update(kw)


# --------------------------------------------------------------------------
# 转码
# --------------------------------------------------------------------------
def run_transcode(job_id, src, dst, mode):
    ff = ffmpeg_path()
    if not ff:
        _set(job_id, status="error", message="找不到 ffmpeg，无法转码。")
        return

    if mode == "copy":
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
               "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", dst]
    else:
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
               "-c:v", "libx264", "-preset", "medium", "-crf", "20",
               "-profile:v", "high", "-pix_fmt", "yuv420p",
               "-c:a", "aac", "-b:a", "192k",
               "-movflags", "+faststart", dst]

    _set(job_id, status="running", message="直拷贝中…" if mode == "copy" else "转码中…")

    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=0x08000000 if os.name == "nt" else 0)
        out, _ = proc.communicate()
        if proc.returncode != 0:
            if mode == "copy":
                run_transcode(job_id, src, dst, "encode")
                return
            msg = out.decode("utf-8", "replace")[-600:] if out else \
                "ffmpeg 退出码 %d" % proc.returncode
            _set(job_id, status="error", message=msg)
            return
        size = os.path.getsize(dst)
        _set(job_id, status="done", path=dst, size=size,
             message="完成，%.1f MB" % (size / 1048576))
    except Exception as e:
        _set(job_id, status="error", message="%s: %s" % (type(e).__name__, e))


# --------------------------------------------------------------------------
# 暴露给前端
# --------------------------------------------------------------------------
def default_download_dir() -> str:
    """
    解析真实的「下载」目录。

    Windows 上「下载」可能被重定向（本机就是 D:\\下载），
    所以优先读注册表 User Shell Folders，取不到再退回 UserProfile\\Downloads。
    """
    # 1) 注册表（最准，能拿到重定向后的真实路径）
    if os.name == "nt":
        try:
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                val, _ = winreg.QueryValueEx(
                    k, "{374DE290-123F-4565-9164-39C4925E467B}")
            val = os.path.expandvars(val)
            if val and os.path.isdir(val):
                return val
        except Exception:
            pass

    # 2) 标准 API（可能返回 UserProfile，需要复核）
    try:
        p = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(p):
            return p
    except Exception:
        pass

    return tempfile.gettempdir()


def unique_path(directory: str, filename: str) -> str:
    """重名时自动加 (1)(2)..."""
    base, ext = os.path.splitext(filename)
    cand = os.path.join(directory, filename)
    n = 0
    while os.path.exists(cand):
        n += 1
        cand = os.path.join(directory, "%s (%d)%s" % (base, n, ext))
    return cand


_TK_LAST_ERROR = {}


def _tk_open_files(title: str, filetypes):
    """
    tkinter 原生多选文件对话框。

    在独立线程跑自己的消息循环，与 WebView2 的 WinForms UI 线程互不干扰，
    避免了 pywebview create_file_dialog 从 js_api 线程调用时的互锁。
    返回路径列表（取消则空列表）。
    """
    result = {"paths": []}
    _TK_LAST_ERROR.clear()

    def worker():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                paths = filedialog.askopenfilenames(
                    title=title, filetypes=list(filetypes))
                result["paths"] = list(paths)
            finally:
                root.destroy()
        except Exception as e:
            # 不再静默吞掉：把错误带回前端显示，便于定位
            err = "%s: %s" % (type(e).__name__, e)
            result["error"] = err
            _TK_LAST_ERROR["error"] = err

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join()
    return result["paths"]


class Api:
    # ---- 能力探测 ----
    def info(self):
        ff = ffmpeg_path()
        port = _start_file_server()
        return {"ok": True, "ffmpeg": bool(ff), "ffmpeg_path": ff,
                "download_dir": default_download_dir(),
                "file_port": port, "big_file": BIG_FILE}

    # ---- 原生选择文件（tkinter 对话框：不依赖 WebView2 UI 线程，绝不互锁） ----
    def pick_file(self):
        try:
            paths = _tk_open_files(
                "选择加密的 PNG 图片",
                [("PNG 图片", "*.png"), ("所有文件", "*.*")])
            if not paths:
                err = _TK_LAST_ERROR.get("error")
                if err:
                    return {"ok": False, "message": "文件选择窗口出错：%s" % err}
                return {"ok": False, "message": "已取消"}
            res = [{"path": p, "name": os.path.basename(p),
                    "size": os.path.getsize(p)} for p in paths
                   if p and os.path.isfile(p)]
            if not res:
                return {"ok": False, "message": "没有选择有效文件"}
            return {"ok": True, "files": res}
        except Exception as e:
            return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}

    # ---- 核心：解密（两种模式） ----
    def decrypt(self, filename: str, b64: str = "", password: str = "",
                path: str = ""):
        """
        模式 A（小文件）: 前端传 b64，落地后解密。
        模式 B（大文件，推荐）: 前端只传 path（来自 pick_file），
            Python 直接从磁盘读，完全不经 JS 桥。

        两种模式都不再把载荷 base64 回传（那是上次 OOM 的根源），
        只返回落盘路径 + file:// 预览地址。
        """
        try:
            if path:
                if not os.path.isfile(path):
                    return {"ok": False, "kind": "input",
                            "message": "文件不存在：%s" % path}
                src = path
                d = os.path.join(WORKDIR, uuid.uuid4().hex[:10])
                os.makedirs(d, exist_ok=True)
            else:
                raw = base64.b64decode((b64 or "").split(",", 1)[-1])
                if not raw:
                    return {"ok": False, "kind": "input",
                            "message": "没有收到图片数据。"}
                d = os.path.join(WORKDIR, uuid.uuid4().hex[:10])
                os.makedirs(d, exist_ok=True)
                src = os.path.join(d, filename or "input.png")
                with open(src, "wb") as f:
                    f.write(raw)

            try:
                r = yaya_core.decrypt_png(src, password or "")
            except yaya_core.NeedPassword as e:
                return {"ok": False, "kind": "need_password", "message": str(e)}
            except yaya_core.WrongPassword as e:
                return {"ok": False, "kind": "wrong_password", "message": str(e)}
            except yaya_core.NoPayload as e:
                return {"ok": False, "kind": "no_payload", "message": str(e)}
            except yaya_core.DecodeError as e:
                return {"ok": False, "kind": "bad_header", "message": str(e)}
            except Exception as e:
                return {"ok": False, "kind": "error",
                        "message": "%s: %s" % (type(e).__name__, e)}

            ext = yaya_core.sniff_ext(r["data"], r["ext"]) or r["ext"] or "bin"
            stem = os.path.splitext(os.path.basename(
                filename or src or "decrypted"))[0] or "decrypted"
            out = os.path.join(d, "%s.%s" % (stem, ext))
            with open(out, "wb") as f:
                f.write(r["data"])

            port = _start_file_server()
            # 预览 URL 必须带 WORKDIR 下的相对子目录（<id>/<文件名>），
            # 否则流式服务 404
            try:
                rel = os.path.relpath(out, WORKDIR).replace("\\", "/")
            except ValueError:
                rel = os.path.basename(out)
            return {
                "ok": True,
                "ext": ext,
                "size": r["size"],
                "bits": r["bits"],
                "path": out,
                "dir": d,
                "is_video": ext in VIDEO_EXT or ext == "mp4",
                # 预览走本地流式服务，不经过 base64
                "preview_url": "http://127.0.0.1:%d/%s" % (port, rel),
            }
        except Exception as e:
            return {"ok": False, "kind": "error",
                    "message": "%s: %s" % (type(e).__name__, e)}

    # ---- 转 MP4 ----
    def start_mp4(self, src: str, mode: str = "auto"):
        if not src or not os.path.isfile(src):
            return {"ok": False, "message": "源文件不存在"}
        job_id = uuid.uuid4().hex[:12]
        dst = os.path.splitext(src)[0] + ".mp4"
        if os.path.abspath(dst) == os.path.abspath(src):
            # 已经是 MP4，无需转码，直接标记完成
            size = os.path.getsize(src)
            _set(job_id, status="done", path=src, size=size,
                 message="本身就是 MP4，无需转码，%.1f MB" % (size / 1048576))
            return {"ok": True, "job": job_id}
        m = "copy" if mode in ("auto", "copy") else "encode"
        _set(job_id, status="queued", message="排队中…", dst=dst)
        threading.Thread(target=run_transcode,
                         args=(job_id, src, dst, m), daemon=True).start()
        return {"ok": True, "job": job_id}

    def job_status(self, job_id: str):
        with LOCK:
            j = dict(JOBS.get(job_id) or {})
        # 大文件不再 base64 回传（OOM 根源）；前端用 preview_url 流式访问
        return j

    # ---- 保存到下载目录 ----
    def save_to_downloads(self, src: str, filename: str = ""):
        """
        把转码结果复制到用户的「下载」目录，并把最终路径回传前端显示。
        WebView2 里 a.click() 不弹下载框，所以必须由后端落地。
        """
        try:
            if not src or not os.path.isfile(src):
                return {"ok": False, "message": "转码结果不存在"}
            d = default_download_dir()
            os.makedirs(d, exist_ok=True)
            name = filename or os.path.basename(src) or "decrypted.mp4"
            dst = unique_path(d, name)
            shutil.copy2(src, dst)
            return {"ok": True, "path": dst, "dir": d,
                    "size": os.path.getsize(dst)}
        except Exception as e:
            return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}

    # ---- 批量保存 ----
    def batch_save(self, srcs, subdir: str = ""):
        """
        把一批文件统一存到「下载」目录下的子目录。
        子目录默认 鸭鸭图批量_<时间戳>，避免和已有文件混在一起。
        """
        try:
            srcs = list(srcs or [])
            if not srcs:
                return {"ok": False, "message": "没有要保存的文件"}

            base = default_download_dir()
            if not subdir:
                subdir = "鸭鸭图批量_" + time.strftime("%Y%m%d_%H%M%S")
            d = os.path.join(base, subdir)
            os.makedirs(d, exist_ok=True)

            saved = 0
            failed = 0
            paths = []
            for s in srcs:
                try:
                    if not s or not os.path.isfile(s):
                        failed += 1
                        continue
                    dst = unique_path(d, os.path.basename(s))
                    shutil.copy2(s, dst)
                    saved += 1
                    paths.append(dst)
                except Exception:
                    failed += 1
            return {"ok": True, "dir": d, "saved": saved,
                    "failed": failed, "paths": paths}
        except Exception as e:
            return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}

    # ---- 另存为 ----
    def save_file(self, src: str, suggested: str = ""):
        try:
            win = webview.windows[0]
            target = win.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename=suggested or os.path.basename(src),
                file_types=("所有文件 (*.*)",))
            if not target:
                return {"ok": False, "message": "已取消"}
            path = target if isinstance(target, str) else target[0]
            shutil.copy2(src, path)
            return {"ok": True, "path": path}
        except Exception as e:
            return {"ok": False, "message": "%s: %s" % (type(e).__name__, e)}


def main():
    api = Api()
    webview.create_window(
        APP_NAME,
        resource_path("web", "index.html"),
        width=1000, height=820, min_size=(480, 640),
        js_api=api,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
