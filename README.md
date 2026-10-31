# VideoSplitTool

[English](#english) · [中文](#中文)

---

## English

A small Windows GUI for **lossless video splitting**: pick a source, pick an output, type start and
end, get a clip cut with stream copy (`ffmpeg -c copy`). No re-encoding, no quality loss.

### Why another splitter

The usual one-liner is `ffmpeg -ss <start> -to <end> -i in -c copy out`. Depending on the container,
that command can silently produce a broken clip:

- **WMV/ASF, seek on the output side**: video jumps to the next keyframe, and everything between the
  requested time and that keyframe is missing — measured 7.7 s lost on a 48-minute file.
- **WMV/ASF, seek on the input side**: video ends up 2.257 s behind the audio on the same file.

Both come from an imprecise seek index in the container, not from ffmpeg being wrong.

This tool always works in two passes:

1. remux the whole source to Matroska with stream copy — the seek index and timestamps are rebuilt;
2. cut the requested range from that intermediate file, again with stream copy.

Inside one session the intermediate file is cached per source (keyed by path + size + mtime), so the
first cut of a movie pays the remux cost and every following cut takes about 0.2 s. Switching to
another source keeps the old cache until the new remux has finished, so aborting a switch does not
lose it. The cache is deleted when the app exits; leftovers from a crashed run are removed at startup.

### Features

- Stream copy only — video and audio bit-exact, container rewritten.
- Start/end time fields; the end is auto-filled as start + 46 s.
- Drag a video file anywhere onto the window to load it.
- Remembers the last input and output folders.
- Abort at any time: kills the ffmpeg process tree, removes partial output, keeps the cache if the
  remux already finished.
- Two-phase progress (remux → cut), single instance.
- The ffmpeg child runs at background CPU and disk-I/O priority, so a long remux does not starve
  foreground apps.
- Self-checks: refuses to write a container that cannot hold the source codecs instead of producing
  an unplayable file.

### Requirements

- Windows 10/11
- `ffmpeg.exe` and `ffprobe.exe` on `PATH`, or in `C:\ffmpeg\bin`
- For running from source or building: `tkinterdnd2` (drag and drop)

### Download

Take `VideoSplitTool-v1.4-win64.exe` from [Releases](../../releases), put it anywhere and run it.
The binary is a PyInstaller one-file build; the UI is in Chinese. `build.bat` produces the same
executable as `VideoSplitTool.exe`; the release asset just carries the version in its name.

### Build from source

```
py -3.13 -m pip install pyinstaller tkinterdnd2
py -3.13 -m PyInstaller --onefile --noconsole --collect-all tkinterdnd2 --name VideoSplitTool split_tool.py
```

or run `build.bat`.

### Known limitations

- A lossless cut can only start on a keyframe. The clip may begin up to one GOP before the time you
  typed (0.27 s in my test file, up to about 8 s on files with 8-second GOPs). MP4 output hides the
  extra head with an edit list; MKV and WMV output show it as a longer clip.
- The source codecs must fit the output container. `.wmv` accepts WMV / MPEG-4 part 2 / WMA / MP3,
  so copy-cutting an H.264 or HEVC source into `.wmv` is refused instead of silently writing garbage.
- No frame-accurate cut: that would require re-encoding, which this tool deliberately does not do.
- The UI is Chinese-only.

### License

MIT

---

## 中文

一个 Windows 小工具，做**无损切片**：选源文件、选输出、填起止时间，用流复制（`ffmpeg -c copy`）
切出片段。不重编码，画质不掉。

### 为什么还要一个切片工具

常见写法是 `ffmpeg -ss <起点> -to <终点> -i 输入 -c copy 输出`。按容器不同，这条命令会悄悄切坏：

- **WMV/ASF 把 `-ss` 放在 `-i` 后面**：视频跳到下一个关键帧，中间那段直接没了。48 分钟的片子上
  实测丢了 7.7 秒。
- **WMV/ASF 把 `-ss` 放在 `-i` 前面**：同一个文件，画面比声音晚 2.257 秒。

两者都来自容器里不准的 seek 索引，不是 ffmpeg 写错了。

这个工具固定走两段：

1. 先把整个源文件无损转封装成 Matroska，索引和时间戳重建；
2. 再从中间文件里切指定区间，同样是流复制。

同一次运行里，中间文件按源缓存（键是路径 + 文件大小 + 修改时间）：同一部片子第一刀付转封装的
时间，后面每刀 0.2 秒左右。换源时旧缓存保留到新源转封装成功之后再删，中途中断不会把它丢掉。
程序退出时删缓存；上次崩溃留下的残留下次启动时清掉。

### 功能

- 只做流复制，音视频码流不动，只重写容器。
- 起止时间两栏，结束时间默认填成起始 + 46 秒。
- 把视频文件拖到窗口任意位置即可载入。
- 记住上次的输入、输出目录。
- 随时中断：杀掉 ffmpeg 进程树、删掉半成品；转封装已经完成的会保留缓存。
- 两段进度（转封装 → 切片），单实例。
- ffmpeg 子进程跑在后台优先级（CPU 空闲 + 磁盘 I/O 最低），长时间转封装不抢前台。
- 写之前先检查容器装不装得下源编码，装不下就拒绝，而不是生成一个放不了的文件。

### 运行要求

- Windows 10/11
- `ffmpeg.exe` 和 `ffprobe.exe` 在 `PATH` 里，或放在 `C:\ffmpeg\bin`

### 下载

到 [Releases](../../releases) 取 `VideoSplitTool-v1.4-win64.exe`，放哪都行，双击即用。是
PyInstaller 单文件打包，界面中文。`build.bat` 构建出来的同一份程序叫 `VideoSplitTool.exe`，
Release 附件只是名字里带了版本号。

### 从源码构建

```
py -3.13 -m pip install pyinstaller tkinterdnd2
py -3.13 -m PyInstaller --onefile --noconsole --collect-all tkinterdnd2 --name VideoSplitTool split_tool.py
```

或者直接跑 `build.bat`。

### 已知限制

- 无损切只能从关键帧开始，画面起点会比填的时间早，最多一个 GOP（测试文件是 0.27 秒，8 秒 GOP
  的文件最多 8 秒）。MP4 输出用 edit list 把多出来的头藏掉，MKV 和 WMV 会实打实变长。
- 源的编码必须能装进输出容器。`.wmv` 只认 WMV / MPEG-4 part 2 / WMA / MP3，拿 H.264 或 HEVC
  源直接复制进 `.wmv` 会被拒绝，不会生成坏文件。
- 不支持精确到帧的切割——那必须重编码，这个工具刻意不做。
- 界面只有中文。

### 许可证

MIT
