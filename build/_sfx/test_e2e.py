# -*- coding: utf-8 -*-
"""端到端：解密样本 -> 检查落盘产物 -> 触发 ffmpeg 转码 -> 验证可播。"""
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

LOG = os.path.join(os.environ["TEMP"], "YayaTuPro.log")
WORK = os.path.join(os.environ["TEMP"], "sfx_e2e")
SAMPLE = os.path.join(WORK, "encrypted_no_pw.png")
SRC_VIDEO = os.path.join(WORK, "payload.mp4")
FFMPEG = r"C:\Users\18430\AppData\Local\YayaTuPro\_internal\ffmpeg\ffmpeg.exe"

with open(LOG, encoding="utf-8", errors="ignore") as f:
    port = re.findall(r"http server on port (\d+)", f.read())[-1]
base = "http://127.0.0.1:%s" % port
print("端口:", port)


def api(method, payload=None, timeout=900):
    data = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(base + "/api/" + method, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


print("\n=== 1. 解密 ===")
t0 = time.time()
r = api("decrypt", {"path": SAMPLE, "filename": "encrypted_no_pw.png", "password": ""})
print("响应:", json.dumps({k: v for k, v in r.items() if k != "data"},
                         ensure_ascii=False))
print("耗时: %.2fs" % (time.time() - t0))
if not r.get("ok"):
    sys.exit("解密失败")

out = r.get("out") or r.get("path") or r.get("file")
print("落盘:", out, "存在:", bool(out and os.path.isfile(out)))
if out and os.path.isfile(out):
    print("大小: %d 字节" % os.path.getsize(out))
    h1 = hashlib.md5(open(out, "rb").read()).hexdigest()
    h2 = hashlib.md5(open(SRC_VIDEO, "rb").read()).hexdigest()
    print("与原视频 md5 一致:", h1 == h2)

print("\n=== 2. 转码任务 ===")
t0 = time.time()
j = api("start_mp4", {"src": out, "mode": "auto"})
print("start_mp4:", json.dumps(j, ensure_ascii=False))
job = j.get("job") or j.get("id") or ""

final = None
for _ in range(400):
    time.sleep(0.5)
    st = api("job_status", {"job": job})
    final = st
    s = st.get("status") or st.get("state") or ""
    if s in ("done", "error", "failed", "cancelled"):
        break
print("最终状态: %s  耗时 %.1fs" % (json.dumps(final, ensure_ascii=False), time.time() - t0))

src = final.get("out") or final.get("dst") or final.get("path") or ""
print("转码产物:", src, "存在:", bool(src and os.path.isfile(src)))

if src and os.path.isfile(src):
    print("大小: %d 字节" % os.path.getsize(src))
    p = subprocess.run([FFMPEG, "-hide_banner", "-i", src],
                       capture_output=True, text=True, errors="ignore")
    for line in p.stderr.splitlines():
        if "Stream #" in line or "Duration" in line:
            print("  ", line.strip())
