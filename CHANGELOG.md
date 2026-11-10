# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

## [1.0.0] - 2026-11-10

### Added

- 单文件自解压（SFX）打包方案：C# 引导器 + zip 载荷拼接到单个 exe 尾部
- 可写目录降级链：`%LOCALAPPDATA%` → `%TEMP%` → exe 同级
- 版本戳机制：内容变化自动重解包，未变化则跳过（二次启动 1.12 s）
- 多实例互斥：`Global\YayaTuPro.Sfx.Launcher`
- 引导器诊断日志 `YayaTuPro_sfx.log`
- 从原 exe 的 `RT_GROUP_ICON` 重建图标（7 级，含 256×256 32bpp）
- 解包完整性校验：1142/1142 文件逐字节 md5 一致

### Fixed

- 解决 PyInstaller `--onefile` 在 `%TEMP%` 不可写环境下静默失败（进程活着、无窗口）的问题

[Unreleased]: https://github.com/nuaaflag/yayatu/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/nuaaflag/yayatu/releases/tag/v1.0.0
