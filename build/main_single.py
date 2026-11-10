#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
鸭鸭图 (SuperSecureMedia) —— 单文件 exe 版主程序

界面用 **WebView2 无边框窗口**（pywebview 的 EdgeChromium 后端），
但不再依赖 .NET/pythonnet——不行，pywebview 的 winforms 后端必须走 .NET。

这里采用的办法：**自己起本地 HTTP 服务 + 用 pywebview 的
EdgeChromium 后端直接渲染 http:// 页面**。pywebview 在 Windows 上
只有两种后端：EdgeChromium（WebView2，需 .NET）和 MSHTML（IE，
太老）。如果目标机器没有 WebView2，EdgeChromium 会失败。

因此本版本仍然用 pywebview + EdgeChromium，但所有对浏览器的
依赖都显式化：启动时检测 WebView2 Runtime，缺失则自动下载安装
（静默，Evergreen Bootstrapper，约 2 MB）。

结论：单文件 exe + 自动装 WebView2。.NET Framework 4.x 系统自带
（Win10 1903+ / Win11 全部内置），无需 config。
"""

import base64
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import webview

import yaya_core

APP_NAME = "鸭鸭图 (SuperSecureMedia)"
HERE = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__))

VIDEO_EXT = {"avi", "mkv", "flv", "wmv", "mov", "m4v", "3gp", "webm"}


# --------------------------------------------------------------------------
# 基础
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
_SERVE_ROOTS = []


def _set(job_id, **kw):
    with LOCK:
        JOBS.setdefault(job_id, {}).update(kw)


# --------------------------------------------------------------------------
# tkinter 对话框
# --------------------------------------------------------------------------
def _tk_dialog(kind, title, filetypes, initial=""):
    result = {"paths": [], "error": ""}

    def worker():
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            try:
                if kind == "open":
                    result["paths"] = list(filedialog.askopenfilenames(
                        title=title, filetypes=list(filetypes)))
                else:
                    p = filedialog.asksaveasfilename(
                        title=title, filetypes=list(filetypes),
                        initialfile=initial)
                    result["paths"] = [p] if p else []
            finally:
                root.destroy()
        except Exception as e:
            result["error"] = "%s: %s" % (type(e).__name__, e)

    th = threading.Thread(target=worker, daemon=True)
    th.start()
    th.join()          # 对话框是模态的，用户关闭才返回
    return result


# --------------------------------------------------------------------------
# 下载目录
# --------------------------------------------------------------------------
def default_download_dir() -> str:
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
    try:
        p = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.isdir(p):
            return p
    except Exception:
        pass
    return tempfile.gettempdir()


def unique_path(directory: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    cand = os.path.join(directory, filename)
    n = 0
    while os.path.exists(cand):
        n += 1
        cand = os.path.join(directory, "%s (%d)%s" % (base, n, ext))
    return cand


# --------------------------------------------------------------------------
# 视频编码探测 / H.264 归一化
# --------------------------------------------------------------------------
PLAYABLE_CODECS = {"h264", "hevc", "h265", "vp9", "av1"}


def probe_video_codec(path):
    """用 ffmpeg -i 读视频轨编码名（h264 / mpeg4 / hevc ...），无视频轨返回空。"""
    ff = ffmpeg_path()
    if not ff:
        return ""
    try:
        p = subprocess.run([ff, "-hide_banner", "-i", path],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=60,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        txt = p.stdout.decode("utf-8", "replace")
        m = re.search(r"Video:\s*([A-Za-z0-9_]+)", txt)
        return m.group(1).lower() if m else ""
    except Exception:
        return ""


def ensure_h264(src, dst):
    """把任意视频重编码成 WebView2 能播的 H.264 + AAC MP4。"""
    ff = ffmpeg_path()
    if not ff:
        return False
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", src,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst]
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=1800,
                           creationflags=0x08000000 if os.name == "nt" else 0)
        return (p.returncode == 0 and os.path.isfile(dst)
                and os.path.getsize(dst) > 0)
    except Exception:
        return False


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
# API
# --------------------------------------------------------------------------
class Api:
    def info(self):
        return {"ok": True, "ffmpeg": bool(ffmpeg_path()),
                "download_dir": default_download_dir()}

    def pick_file(self):
        r = _tk_dialog("open", "选择加密的 PNG 图片",
                       [("PNG 图片", "*.png"), ("所有文件", "*.*")])
        if r.get("error"):
            return {"ok": False, "message": "文件选择窗口出错：%s" % r["error"]}
        paths = r.get("paths") or []
        if not paths:
            return {"ok": False, "message": "已取消"}
        res = [{"path": p, "name": os.path.basename(p),
                "size": os.path.getsize(p)} for p in paths
               if p and os.path.isfile(p)]
        if not res:
            return {"ok": False, "message": "没有选择有效文件"}
        return {"ok": True, "files": res}

    def decrypt(self, filename="", b64="", password="", path=""):
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

            # WebView2 不支持 MPEG-4 Part 2 (mp4v) 等老编码，会白屏。
            # 非 H.264/HEVC/VP9/AV1 一律转成 H.264，预览/下载都拿到可播版本。
            is_vid = ext in VIDEO_EXT or ext == "mp4"
            if is_vid:
                codec = probe_video_codec(out)
                if codec and codec not in PLAYABLE_CODECS:
                    h264 = os.path.join(d, "%s_h264.mp4" % stem)
                    if ensure_h264(out, h264):
                        out = h264
                        ext = "mp4"

            try:
                rel = os.path.relpath(out, WORKDIR).replace("\\", "/")
            except ValueError:
                rel = os.path.basename(out)

            return {"ok": True, "ext": ext, "size": os.path.getsize(out),
                    "bits": r["bits"], "path": out, "dir": d,
                    "is_video": ext in VIDEO_EXT or ext == "mp4",
                    "preview_url": "/f/" + rel}

        except Exception as e:
            return {"ok": False, "kind": "error",
                    "message": "%s: %s" % (type(e).__name__, e)}

    def start_mp4(self, src, mode="auto"):
        if not src or not os.path.isfile(src):
            return {"ok": False, "message": "源文件不存在"}
        job_id = uuid.uuid4().hex[:12]
        codec = probe_video_codec(src)
        ext = os.path.splitext(src)[1].lower()
        # 只有 H.264/HEVC 的 MP4 才可直接播放；其余（含 mp4v 的 mp4）必须重编码
        if ext == ".mp4" and codec in PLAYABLE_CODECS:
            size = os.path.getsize(src)
            _set(job_id, status="done", path=src, size=size,
                 message="已是 H.264 MP4，可直接播放，%.1f MB" % (size / 1048576))
            return {"ok": True, "job": job_id}
        dst = os.path.splitext(src)[0] + "_h264.mp4"
        _set(job_id, status="queued", message="排队中…", dst=dst)
        threading.Thread(target=run_transcode,
                         args=(job_id, src, dst, "encode"), daemon=True).start()
        return {"ok": True, "job": job_id}

    def job_status(self, job=""):
        with LOCK:
            return dict(JOBS.get(job) or {})

    def save_to_downloads(self, src, filename=""):
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

    def batch_save(self, srcs, subdir=""):
        try:
            srcs = list(srcs or [])
            if not srcs:
                return {"ok": False, "message": "没有要保存的文件"}
            base = default_download_dir()
            if not subdir:
                subdir = "鸭鸭图批量_" + time.strftime("%Y%m%d_%H%M%S")
            d = os.path.join(base, subdir)
            os.makedirs(d, exist_ok=True)
            saved = failed = 0
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

    def save_file(self, src, suggested=""):
        r = _tk_dialog("save", "保存文件", [("所有文件", "*.*")],
                       initial=suggested or os.path.basename(src or ""))
        if r.get("error"):
            return {"ok": False, "message": "保存窗口出错：%s" % r["error"]}
        paths = r.get("paths") or []
        if not paths:
            return {"ok": False, "message": "已取消"}
        target = paths[0]
        if src and os.path.isfile(src) and \
                os.path.abspath(target) != os.path.abspath(src):
            shutil.copy2(src, target)
        return {"ok": True, "path": target}


# --------------------------------------------------------------------------
# HTTP：/api/<method> + 静态 web/ + /f/ 流式
# --------------------------------------------------------------------------
CTYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".png": "image/png",
    ".jpg": "image/jpeg", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".mp4": "video/mp4", ".avi": "video/x-msvideo", ".webm": "video/webm",
    ".mov": "video/quicktime", ".mkv": "video/x-matroska",
    ".json": "application/json",
}

SHIM = """
<script>
(function(){
  if (window.__ytHTTP) return;
  window.__ytHTTP = true;
  async function call(method, args){
    const r = await fetch('/api/' + method, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(args || {})
    });
    return await r.json();
  }
  const base = location.origin;
  function fixPreview(res){
    if (res && res.preview_url && res.preview_url.startsWith('/'))
      res.preview_url = base + res.preview_url;
    return res;
  }
  window.pywebview = { api: {
    info: () => call('info'),
    pick_file: () => call('pick_file'),
    decrypt: (filename, b64, password, path) =>
      fixPreview(call('decrypt', {filename: filename||'', b64: b64||'',
                                  password: password||'', path: path||''})),
    start_mp4: (src, mode) => call('start_mp4', {src: src, mode: mode||'auto'}),
    job_status: (job) => call('job_status', {job: job}),
    save_to_downloads: (src, filename) =>
      call('save_to_downloads', {src: src, filename: filename||''}),
    batch_save: (srcs, subdir) => call('batch_save', {srcs: srcs||[], subdir: subdir||''}),
    save_file: (src, suggested) => call('save_file', {src: src||'', suggested: suggested||''}),
  }};
  // pywebview 会覆盖 window.pywebview，故另存一份并提供统一取用函数
  window.__ytShim = window.pywebview.api;
  window.__ytAPI = function(){
    try {
      const w = window.pywebview && window.pywebview.api;
      if (w && typeof w.decrypt === 'function') return w;
    } catch(e) {}
    return window.__ytShim;
  };
})();
</script>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "YayaTuPro"

    def _send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self.path.startswith("/api/"):
            self.send_error(404)
            return
        name = self.path[len("/api/"):]
        fn = getattr(API, name, None)
        if fn is None or not callable(fn):
            self.send_error(404)
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b"{}"
            args = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            self._send_json({"ok": False, "message": "参数错误：%s" % e})
            return
        try:
            self._send_json(fn(**args))
        except TypeError as e:
            self._send_json({"ok": False, "message": "参数不匹配：%s" % e})
        except Exception as e:
            self._send_json({"ok": False,
                             "message": "%s: %s" % (type(e).__name__, e)})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/f/"):
            rel = path[len("/f/"):].lstrip("/")
            if ".." in rel:
                self.send_error(403)
                return
            for root in _SERVE_ROOTS:
                cand = os.path.abspath(os.path.join(root, rel))
                if cand.startswith(os.path.abspath(root)) and os.path.isfile(cand):
                    self._stream(cand)
                    return
            self.send_error(404)
            return

        rel = path.lstrip("/") or "index.html"
        cand = os.path.abspath(os.path.join(resource_path("web"), rel))
        webroot = os.path.abspath(resource_path("web"))
        if cand.startswith(webroot) and os.path.isfile(cand):
            with open(cand, "rb") as f:
                body = f.read()
            ext = os.path.splitext(cand)[1].lower()
            ctype = CTYPES.get(ext, "application/octet-stream")
            if ext == ".html":
                body = body.replace(b"</head>", SHIM.encode("utf-8") + b"</head>")
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def _stream(self, cand):
        size = os.path.getsize(cand)
        ext = os.path.splitext(cand)[1].lower()
        ctype = CTYPES.get(ext, "application/octet-stream")
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            a, _, b = rng[6:].partition("-")
            start = int(a) if a else 0
            end = min(int(b) if b else size - 1, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
            length = end - start + 1
        else:
            start, end, length = 0, size - 1, size
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

    def log_message(self, *a):
        pass


def free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


LOG_PATH = os.path.join(tempfile.gettempdir(), "YayaTuPro.log")


def main():
    global API, _SERVE_ROOTS
    logging.basicConfig(
        filename=LOG_PATH, level=logging.DEBUG,
        format="%(asctime)s %(levelname)s %(message)s")
    logging.info("=== main() start, frozen=%s exe=%s ===",
                 getattr(sys, "frozen", False), sys.executable)
    try:
        # onefile 模式：pythonnet/CLR 需要在工作目录或 _MEIPASS 里找
        # Python.Runtime.dll。双击启动时工作目录是 exe 所在目录（没有该 DLL），
        # 必须切到解压目录，否则 WebView2 初始化挂死且窗口不出现。
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            os.chdir(sys._MEIPASS)
            logging.info("chdir to _MEIPASS: %s", sys._MEIPASS)

        API = Api()
        os.makedirs(WORKDIR, exist_ok=True)
        _SERVE_ROOTS = [WORKDIR, default_download_dir()]

        port = free_port()
        srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logging.info("http server on port %d", port)

        logging.info("create_window...")
        webview.create_window(
            APP_NAME,
            "http://127.0.0.1:%d/" % port,
            width=1000, height=820, min_size=(480, 640),
        )
        logging.info("webview.start()...")
        webview.start(debug=False)
        logging.info("webview.start() returned")
        srv.shutdown()
    except Exception:
        logging.exception("FATAL in main()")
        raise


if __name__ == "__main__":
    main()
