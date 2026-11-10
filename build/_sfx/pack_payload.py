# -*- coding: utf-8 -*-
"""把 dist_http/YayaTuPro 打成 zip 载荷（供 SFX stub 从自身尾部读取）。"""
import os
import sys
import time
import zipfile

SRC = r"E:\tools\yayatu\build\dist_http\YayaTuPro"
OUT = r"E:\tools\yayatu\build\_sfx\payload.zip"

t0 = time.time()
n = 0
raw = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
    for root, dirs, files in os.walk(SRC):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            arc = os.path.relpath(p, SRC).replace(os.sep, "/")
            z.write(p, arc)
            n += 1
            raw += os.path.getsize(p)
            if n % 200 == 0:
                print("  ... %d files, %.1f MB raw" % (n, raw / 1048576.0), flush=True)

size = os.path.getsize(OUT)
print("DONE files=%d raw=%.1fMB zip=%.1fMB ratio=%.1f%% elapsed=%.1fs"
      % (n, raw / 1048576.0, size / 1048576.0, size * 100.0 / raw, time.time() - t0))
