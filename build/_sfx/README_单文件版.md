# YayaTuPro 单文件版（SFX 自解压）

把 `dist_http/YayaTuPro`（1 个 exe + 1 个 .config + `_internal/` 共 1142 个文件、285.91 MB）
打包成**一个可独立分发的 exe**。

| 项 | 值 |
|---|---|
| 产物 | `dist_single/YayaTuPro.exe` |
| 大小 | **110.94 MB**（116,332,915 字节） |
| 压缩率 | 38.8%（zlib level 6） |
| SHA256 | `11FC3FB9B6AEBE923F2884AB17165D789601F20F4EED8F12A01D6EA72BF4AF37` |
| 原始目录 | 285.91 MB / 1142 文件 |
| 图标 | 沿用原 `YayaTuPro.exe` 的 7 级图标（含 256×256 32bpp） |
| 版本资源 | `鸭鸭图 (SuperSecureMedia)` |
| 运行时依赖 | .NET Framework 4.x（Win10 1903+ / Win11 自带） |

---

## 为什么不是 PyInstaller `--onefile`

项目里此前试过 PyInstaller onefile，结论是「进程活着、HTTP 正常、窗口不出现」，
`README_Portable.md` 记录为「兼容性死角，性价比极低」。

本次实测把根因定位清楚了，**不是 pywebview 的问题**：

- PyInstaller onefile 的 bootloader **必须**先把 285 MB 解到 `%TEMP%\_MEIxxxxxx` 才能跑；
- 一旦运行环境的 `%TEMP%` 不可写（被 ACL / 组策略 / 端点防护 / 沙箱拦截），
  bootloader 在建临时目录那一步就失败，`--windowed` 模式下**连报错都没有**，
  表现就是「进程在、没窗口」；
- 在 `E:\code1` 上实测：连一个 4 KB 的空白 .NET 程序都无法写 `%TEMP%`
  （`UnauthorizedAccessException`，但 ACL 里当前用户明明是 FullControl —— 拦截发生在更外层）。

所以 onefile 这条路在这个机器上有环境性死结。改用的方案是**外置 SFX 外壳**：
形态上是一个 exe，行为上跑的是**已经验收通过的 onedir 目录**，窗口行为 100% 等于原版。

---

## 文件布局

```
[0 .. 78848)             PE 引导器（C# WinForms，x64）
[78848 .. +116254049)    zip 载荷（_internal 全量 + exe + .config）
[.. +8)                  int64 LE = zip 长度
[.. +10)                 魔数 "YAYATUSFX1"
```

引导器从**自身尾部**读长度和魔数，用只读子流（`SubStream`）把 zip 直接喂给
`System.IO.Compression.ZipArchive`，**不落中间文件**。

---

## 运行时行为

1. **首次运行**：解压到可写目录 → 写版本戳 → 启动 `YayaTuPro.exe`
2. **后续运行**：版本戳一致 → **跳过解压**，直接启动（约 1.1 s 到窗口）
3. **换了新包**：版本戳变化 → 自动重新解压覆盖（实测 3.4 s）
4. **解包目录选择**（`ResolveRoot`，按顺序探测可写性）：
   - 已解包过的位置（避免重复解压）
   - `%LOCALAPPDATA%\YayaTuPro`
   - `%TEMP%\YayaTuPro`
   - **exe 所在目录**（前两个都不可写时的兜底 —— `E:\code1` 就是走这条）

版本戳 = `zip长度-前1MB的SHA1+尾64KB的SHA1`，不是文件时间戳，改 exe 内容必然触发重解包。

5. **多实例互斥**：`Global\YayaTuPro.Sfx.Launcher` 命名互斥体；
   第二个实例不会重复解压，会等第一个完成再启动。

---

## 实测数据

| 场景 | 结果 |
|---|---|
| 首次启动（全新机器，解包 285.91 MB） | 3.4 s 解包 + 启动 |
| 二次启动（免解包） | **1.12 s** 到窗口 |
| 换包后重解包 | 3.65 s 完成，被删的 `python312.dll` 按原 md5 补回 |
| 解包完整性 | 1142/1142 文件**逐字节一致**（md5 全比对） |
| 窗口 | 标题 `鸭鸭图 (SuperSecureMedia)`，1250×1025，渲染 3752 色（非白屏） |
| 解密（真实样本 17/30/33 MB） | 正确识别出载荷与密码保护状态 |
| 解密（自造 6 秒 H.264 样本） | 0.20 s，产物与原视频 **md5 完全一致** |
| 转码（mpeg4 → H.264） | 480×360 / 4 s → `h264 (High)`，正常 |
| 转码（已是 H.264） | 直接放行，不重复编码 |
| ffmpeg | 9.0.2-essentials，可独立转码 |
| **受限目录**（`E:\code1`，`%TEMP%` 被拦） | **自动降级到 exe 同级目录，3.4 s 解包，窗口正常** |

### 与 PyInstaller onefile 的对比

| 项 | PyInstaller onefile | 本方案（SFX + onedir） |
|---|---|---|
| 首次启动 | 每次都要解包 | 只在首次/换包时解包 |
| 二次启动 | **每次重解 285 MB** | 1.12 s（零解包） |
| `%TEMP%` 不可写 | **直接失败，且无提示** | 自动降级到 exe 同级 |
| 磁盘写入 | 285 MB / 每次启动 | 285 MB / 仅首次 |
| 崩溃残留 | `_MEI*` 垃圾堆积（本机曾积 1.8 GB） | staging 目录，下次启动自动清 |
| pywebview 窗口 | 有失败记录 | 等于已验收的 onedir |

---

## 重新构建

```bash
cd E:\tools\yayatu\build\_sfx
python pack_payload.py        # -> payload.zip（110.9 MB）
"C:/Windows/Microsoft.NET/Framework64/v4.0.30319/csc.exe" /nologo /target:winexe ^
  /platform:x64 /optimize+ /win32icon:app.ico /out:Sfx.exe ^
  /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll ^
  Sfx.cs AssemblyInfo.cs      # -> Sfx.exe（77 KB 引导器）
python build_single.py        # -> dist_single\YayaTuPro.exe
```

图标由 `extract_icon.py` 从原 exe 的 RT_GROUP_ICON 重建。

---

## 排障

引导器日志：解包目录同级的 `YayaTuPro_sfx.log`（正常情况在
`%LOCALAPPDATA%\YayaTuPro_sfx.log`，受限环境在 exe 旁边）。
程序自身日志仍是 `%TEMP%\YayaTuPro.log`。

退出码：

| 码 | 含义 |
|---|---|
| 0 | 正常 |
| 2 | 包损坏（尾部找不到 `YAYATUSFX1`） |
| 3 | 解压失败 |
| 4 | 三个候选目录都不可写 |

**这个版本没有做代码签名。** 首次运行 SmartScreen 可能拦一下，点「更多信息 → 仍要运行」即可。
要彻底消掉，需要 OV/EV 代码签名证书。

---

## 已知取舍

- 首次启动要等 3.4 s（解 285 MB），二次之后 1.1 s。
- 解包目录会长期占约 290 MB（`%LOCALAPPDATA%\YayaTuPro`）。
  删掉它不影响使用，下次启动会重新解压。
- `_internal` 里 ffmpeg 存了**两份完全相同的 105 MB**
  （`ffmpeg/ffmpeg.exe` 和 `imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v9.0.2.exe`，md5 相同）。
  按原样保留了，没有裁剪 —— 这是构建产物的问题，不在本次打包任务范围内。
  如果去掉一份，单文件可降到约 **67 MB**。
