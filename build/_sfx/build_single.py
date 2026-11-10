# -*- coding: utf-8 -*-
"""把 Sfx.exe + payload.zip 拼成单文件 YayaTuPro_单文件版.exe。

布局: [stub PE][payload zip][int64 LE = len(zip)]["YAYATUSFX1"]
"""
import os
import shutil
import struct
import sys

BASE = r"E:\tools\yayatu\build\_sfx"
STUB = os.path.join(BASE, "Sfx.exe")
ZIP = os.path.join(BASE, "payload.zip")
OUT = r"E:\tools\yayatu\build\dist_single\YayaTuPro.exe"
MAGIC = b"YAYATUSFX1"

for p in (STUB, ZIP):
    if not os.path.isfile(p):
        sys.exit("missing: " + p)

os.makedirs(os.path.dirname(OUT), exist_ok=True)

stub_size = os.path.getsize(STUB)
zip_size = os.path.getsize(ZIP)

# 对齐到 512 字节边界，让 PE 段与载荷分界清晰（也便于事后按偏移定位载荷）
pad = (-stub_size) % 512
print("stub=%d zip=%d pad=%d" % (stub_size, zip_size, pad))

with open(OUT, "wb") as out:
    with open(STUB, "rb") as f:
        shutil.copyfileobj(f, out)
    if pad:
        out.write(b"\x00" * pad)
    with open(ZIP, "rb") as f:
        shutil.copyfileobj(f, out)
    out.write(struct.pack("<q", zip_size))
    out.write(MAGIC)

total = os.path.getsize(OUT)
print("WROTE %s  %.2f MB (%d bytes)" % (OUT, total / 1048576.0, total))

# 立即回读校验
with open(OUT, "rb") as f:
    f.seek(-(8 + len(MAGIC)), os.SEEK_END)
    tail = f.read(8 + len(MAGIC))
    n = struct.unpack("<q", tail[:8])[0]
    assert tail[8:] == MAGIC, "magic mismatch"
    assert n == zip_size, "size mismatch %d != %d" % (n, zip_size)
    off = total - (8 + len(MAGIC)) - n
    f.seek(off)
    pk = f.read(4)
    assert pk == b"PK\x03\x04", "pk marker mismatch %r" % pk
print("VERIFY ok: payload at offset %d, %d bytes, starts with PK\\003\\004" % (off, n))
