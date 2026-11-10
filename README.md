# YayaTuPro

把一组散落的运行文件打包成**单个可执行 exe** 的构建工具链。

产物是 `YayaTuPro.exe`（约 111 MB），双击即用，不需要装 Python。

## 下载

最新版（固定链接，永远指向最新正式版）：

**https://github.com/nuaaflag/yayatu/releases/latest/download/YayaTuPro.exe**

历史版本见 [Releases](https://github.com/nuaaflag/yayatu/releases)。

> 首次运行 SmartScreen 可能拦一下，点「更多信息 → 仍要运行」。
> 程序没有代码签名。

## 这是什么

原始程序是一个 PyInstaller `onedir` 目录（1 个 exe + 1 个 `.config` + `_internal/`
里 1142 个文件，共 285.91 MB），拷给别人得整个文件夹一起带走。

本项目把它收进**一个 exe**：

```
[0 .. 78848)             PE 引导器（C# WinForms，x64）
[78848 .. +116254049)    zip 载荷（_internal 全量 + exe + .config）
[.. +8)                  int64 LE = zip 长度
[.. +10)                 魔数 "YAYATUSFX1"
```

引导器从自身尾部读长度和魔数，用只读子流把 zip 直接喂给
`System.IO.Compression.ZipArchive`，**不落中间文件**。

## 为什么不用 PyInstaller `--onefile`

试过，不行。根因不是 pywebview 的兼容性问题，而是：

**onefile 的 bootloader 必须先把 285 MB 解到 `%TEMP%\_MEIxxxxxx` 才能跑。**
一旦 `%TEMP%` 不可写（ACL / 组策略 / 端点防护 / 沙箱），
bootloader 在建临时目录那步就死了，`--windowed` 模式下**连报错都没有** ——
表现就是「进程在、没窗口」。

实测：在受限目录下，连一个 4 KB 的空白 .NET 程序都无法写 `%TEMP%`
（`UnauthorizedAccessException`，但 ACL 里当前用户明明是 FullControl）。

所以改用**外置 SFX 外壳**：形态是一个 exe，行为跑的是已经验收通过的 onedir 目录。

## 运行时行为

1. **首次运行**：解压到可写目录 → 写版本戳 → 启动
2. **后续运行**：版本戳一致 → **跳过解压**，直接启动
3. **换了新包**：版本戳变化 → 自动重新解压覆盖
4. **解包目录**（按顺序探测可写性）：
   `%LOCALAPPDATA%\YayaTuPro` → `%TEMP%\YayaTuPro` → **exe 所在目录**

版本戳 = `zip长度-前1MB的SHA1+尾64KB的SHA1`，不是文件时间戳，改内容必然触发重解包。

## 实测

| 场景 | 结果 |
|---|---|
| 首次启动（解包 285.91 MB） | 3.4 s |
| 二次启动（免解包） | 1.12 s |
| 换包后重解包 | 3.65 s |
| 解包完整性 | 1142/1142 文件逐字节 md5 一致 |
| 受限目录（`%TEMP%` 被拦） | 自动降级到 exe 同级，窗口正常 |

## 构建

```bash
cd build/_sfx
python pack_payload.py        # -> payload.zip
csc /target:winexe /platform:x64 /optimize+ /win32icon:app.ico ^
    /out:Sfx.exe /reference:System.IO.Compression.dll ^
    /reference:System.IO.Compression.FileSystem.dll Sfx.cs AssemblyInfo.cs
python build_single.py        # -> ../dist_single/YayaTuPro.exe
```

需要：Python 3.12、.NET Framework 4.x 的 `csc.exe`、以及 `build/dist_http/YayaTuPro/`
这个待打包目录。

构建产物 `dist_single/`、`dist_http/`、`ffmpeg/` 都**不进仓库**，
通过 Release 分发。

## 目录

| 路径 | 说明 |
|---|---|
| `build/main_single.py` | 单文件版主程序（HTTP 服务 + WebView2 窗口） |
| `build/main.py` | onedir 版主程序 |
| `build/yaya_core.py` | 解密算法（LSB 位平面抽取 + 头部解析） |
| `build/web/` | 前端静态资源（React + Vite 打包产物） |
| `build/_sfx/Sfx.cs` | C# 自解压引导器源码 |
| `build/_sfx/pack_payload.py` | 打包 zip 载荷 |
| `build/_sfx/build_single.py` | 拼接 stub + 载荷 |
| `build/_sfx/extract_icon.py` | 从原 exe 重建图标 |
| `build/_sfx/test_e2e.py` | 端到端冒烟测试 |

## 排障

引导器日志：解包目录同级的 `YayaTuPro_sfx.log`
（正常在 `%LOCALAPPDATA%\YayaTuPro_sfx.log`，受限环境在 exe 旁边）。

退出码：`0` 正常 / `2` 包损坏 / `3` 解压失败 / `4` 三个候选目录都不可写

## 已知取舍

- 首次启动等 3.4 s，二次之后 1.1 s
- 解包目录长期占约 290 MB，删掉不影响使用，下次会重解
