# -*- coding: utf-8 -*-
"""复现 dist_http/YayaTuPro 的构建（onedir）。

依据原产物 _internal 的实际内容反推参数：
  - 含 ffmpeg/（手写 add-data）、web/（手写 add-data）
  - 不含 imageio_ffmpeg 的 binaries 之外的裁剪（原产物里 ffmpeg 存了两份）
  - 保留了 cryptography / psutil / yaml / ujson / markupsafe / charset_normalizer
  - tcl/tk 全套（main_single 里函数内 import tkinter）
"""
import os
import sys

import PyInstaller.__main__

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "dist_rebuild")
WORK = os.path.join(HERE, "build_rebuild")

args = [
    os.path.join(HERE, "main_single.py"),
    "--noconfirm",
    "--clean",
    "--onedir",
    "--windowed",
    "--name", "YayaTuPro",
    "--distpath", OUT,
    "--workpath", WORK,
    "--specpath", WORK,
    "--noupx",
    # 前端与 ffmpeg 手动带进去
    "--add-data", os.path.join(HERE, "web") + os.pathsep + "web",
    "--add-data", os.path.join(HERE, "ffmpeg") + os.pathsep + "ffmpeg",
    # 动态 import，静态分析抓不到
    "--hidden-import", "webview.platforms.winforms",
    "--hidden-import", "webview.platforms.edgechromium",
    "--hidden-import", "clr",
    "--hidden-import", "pythonnet",
    "--hidden-import", "clr_loader",
    "--hidden-import", "tkinter",
    "--hidden-import", "tkinter.filedialog",
    "--hidden-import", "yaya_core",
]

print("=" * 60)
print("构建参数:")
for a in args:
    print("  " + a)
print("=" * 60)

PyInstaller.__main__.run(args)

exe = os.path.join(OUT, "YayaTuPro", "YayaTuPro.exe")
print("\n" + "=" * 60)
if os.path.isfile(exe):
    print("构建成功: %s (%d 字节)" % (exe, os.path.getsize(exe)))
else:
    print("构建失败: 找不到 %s" % exe)
    sys.exit(1)
