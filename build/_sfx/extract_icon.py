# -*- coding: utf-8 -*-
"""从 PyInstaller 生成的 exe 里提取 .ico（重建 RT_GROUP_ICON -> ICONDIR）。"""
import struct
import sys

import pefile

SRC = r"E:\tools\yayatu\build\dist_http\YayaTuPro\YayaTuPro.exe"
OUT = r"E:\tools\yayatu\build\_sfx\app.ico"

pe = pefile.PE(SRC, fast_load=True)
pe.parse_data_directories(
    directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_RESOURCE']])

icons = {}
group = None
for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
    if e.id is None or e.id not in (3, 14):
        continue
    for name_entry in e.directory.entries:
        for lang_entry in name_entry.directory.entries:
            d = lang_entry.data.struct
            raw = pe.get_data(d.OffsetToData, d.Size)
            if e.id == 3:
                icons[name_entry.id] = raw
            elif e.id == 14 and group is None:
                group = raw

if group is None:
    sys.exit("no RT_GROUP_ICON")

reserved, typ, count = struct.unpack_from("<HHH", group, 0)
print("group: reserved=%d type=%d count=%d" % (reserved, typ, count))

entries = []
for i in range(count):
    base = 6 + i * 14
    w, h, cc, r, planes, bits, size, nid = struct.unpack_from("<BBBBHHIH", group, base)
    entries.append((w, h, cc, r, planes, bits, size, nid))
    print("  entry %d: %dx%d %dbpp %d bytes id=%d" % (i, w, h, bits, size, nid))

# 组装 ICO
header = struct.pack("<HHH", 0, 1, count)
dir_size = count * 16
data_off = 6 + dir_size

blobs = []
for (w, h, cc, r, planes, bits, size, nid) in entries:
    raw = icons.get(nid)
    if raw is None:
        sys.exit("missing icon id %d" % nid)
    if len(raw) != size:
        print("  warn: id=%d declared %d actual %d" % (nid, size, len(raw)))
        size = len(raw)
    blobs.append((w, h, cc, r, planes, bits, size, raw))

dir_bytes = b""
for (w, h, cc, r, planes, bits, size, raw) in blobs:
    dir_bytes += struct.pack("<BBBBHHII", w, h, cc, r, planes, bits, size, data_off)
    data_off += size

with open(OUT, "wb") as f:
    f.write(header)
    f.write(dir_bytes)
    for (w, h, cc, r, planes, bits, size, raw) in blobs:
        f.write(raw)

import os
print("WROTE %s  %d bytes" % (OUT, os.path.getsize(OUT)))
