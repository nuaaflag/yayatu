# Changelog

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.0.1] - 2026-11-17

### Fixed

- **MP4 largesize box 导致大视频被截断**：`_trim_container` 把 `size == 1`
  误判为损坏数据，而 MP4 规范中 `size == 1` 是 largesize 标记（真实大小在后续
  8 字节的 64 位整数中）。ffmpeg 在 faststart 场景大量使用此写法，导致 mdat
  被丢弃，解出的 mp4 只有 ftyp + moov，无法播放。
  实测样本 8,110,893 字节的视频原本只解出 30,838 字节（丢失 99.6%）。
- `size == 0` 的 box 现在按规范延伸到末尾，并裁掉画布尾部 0 填充
- box 链解析失败时安全回退到兜底逻辑，不再返回半截数据
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

[1.0.1]: https://github.com/nuaaflag/yayatu/releases/tag/v1.0.1

