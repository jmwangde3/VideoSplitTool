# -*- coding: utf-8 -*-
"""分割工具 - 无损切片（ffmpeg -c copy），带图形界面。

用法：
  双击运行 -> 选输入、输出、起止时间，点“开始”。
  命令行： 分割工具.exe --run <输入> <输出> <起始> <结束>
"""
import ctypes
import atexit
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

CREATE_NO_WINDOW = 0x08000000
WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4
APP_TITLE = "分割工具"
APP_VERSION = "1.3"
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
INI_PATH = os.path.join(APP_DIR, "split.ini")
LOG_PATH = os.path.join(APP_DIR, "split.log")
DEFAULT_END_OFFSET = 46.0
VIDEO_TYPES = [
    ("视频文件", "*.wmv *.asf *.mp4 *.mkv *.mov *.avi *.m4v *.ts *.flv *.m2ts"),
    ("所有文件", "*.*"),
]


def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    for cand in (
        os.path.join("C:\\ffmpeg\\bin", name + ".exe"),
        os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "ffmpeg", "bin", name + ".exe"),
    ):
        if os.path.exists(cand):
            return cand
    return name


FFMPEG = find_tool("ffmpeg")
FFPROBE = find_tool("ffprobe")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.CallWindowProcW.argtypes = [ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.CallWindowProcW.restype = ctypes.c_ssize_t
shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT
shell32.DragFinish.argtypes = [wintypes.HANDLE]
shell32.DragFinish.restype = None


def log(message):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + str(message).replace("\n", " | ")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def run_hidden(args, timeout=60):
    return subprocess.run(args, capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=timeout)


def parse_time(text):
    raw = str(text).strip().replace("：", ":").replace("：", ":")
    if not raw:
        raise ValueError("时间不能为空")
    if re.fullmatch(r"\d+(\.\d+)?", raw):
        return float(raw)
    parts = raw.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise ValueError("时间格式应为 00:43:47 或 43:47")
    values = []
    for part in parts:
        if not re.fullmatch(r"\d+(\.\d+)?", part):
            raise ValueError("时间格式应为 00:43:47 或 43:47")
        values.append(float(part))
    total = 0.0
    for value in values:
        total = total * 60.0 + value
    return total


def format_time(seconds):
    ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def human_size(size):
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f} GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size} B"


def probe(path):
    result = run_hidden([
        FFPROBE, "-v", "error", "-print_format", "json",
        "-show_entries", "format=format_name,duration:stream=index,codec_type,codec_name,width,height",
        path,
    ], timeout=120)
    if result.returncode != 0:
        raise RuntimeError("读不了文件信息：" + result.stderr.decode("utf-8", "replace").strip()[-300:])
    data = json.loads(result.stdout.decode("utf-8", "replace"))
    duration = 0.0
    fmt = data.get("format", {})
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    if duration <= 0:
        for stream in data.get("streams", []):
            try:
                duration = max(duration, float(stream.get("duration") or 0))
            except ValueError:
                pass
    return data, duration


COPY_RULES = {
    ".wmv": {"wmv1", "wmv2", "wmv3", "msmpeg4v3", "msmpeg4", "wmav1", "wmav2", "mp3"},
    ".asf": {"wmv1", "wmv2", "wmv3", "msmpeg4v3", "msmpeg4", "wmav1", "wmav2", "mp3"},
    ".mp4": {"h264", "hevc", "mpeg4", "mjpeg", "av1", "aac", "mp3", "ac3", "eac3", "alac", "opus"},
    ".m4v": {"h264", "hevc", "mpeg4", "mjpeg", "av1", "aac", "mp3", "ac3", "eac3", "alac", "opus"},
    ".mov": {"h264", "hevc", "mpeg4", "mjpeg", "av1", "aac", "mp3", "ac3", "eac3", "alac", "opus"},
    ".mkv": None,
}


def check_copy_support(ext, streams):
    rules = COPY_RULES.get(ext)
    if ext not in COPY_RULES or rules is None:
        return True, ""
    for stream in streams:
        codec = stream.get("codec_name")
        if codec and codec not in rules:
            return False, f"{ext} 容器装不下 {codec}"
    return True, ""


class SplitError(Exception):
    pass


def kill_tree(pid):
    try:
        run_hidden(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=20)
    except Exception as exc:  # noqa: BLE001
        log(f"kill_tree failed: {exc}")


def lower_process_priority(pid):
    """把 ffmpeg 降到后台模式：CPU 空闲优先级 + 磁盘 I/O 最低优先级，不抢前台。"""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x0200, False, pid)  # PROCESS_SET_INFORMATION
        if handle:
            kernel32.SetPriorityClass(handle, 0x00100000)  # PROCESS_MODE_BACKGROUND_BEGIN
            kernel32.CloseHandle(handle)
    except Exception as exc:  # noqa: BLE001
        log(f"lower priority failed: {exc}")


def run_ffmpeg(args, total_seconds, base_pct, weight_pct, on_progress, abort_event):
    log("run: " + " ".join(args))
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
    lower_process_priority(proc.pid)
    stderr_lines = []

    def drain():
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", "replace").strip()
                if line:
                    stderr_lines.append(line)
        except Exception:  # noqa: BLE001
            pass

    threading.Thread(target=drain, daemon=True).start()
    aborted = False
    try:
        for raw in proc.stdout:
            if abort_event is not None and abort_event.is_set():
                aborted = True
                break
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
                value = line.split("=", 1)[1]
                if value.isdigit():
                    seconds = int(value) / 1_000_000.0
                    if total_seconds > 0 and on_progress:
                        pct = min(1.0, max(0.0, seconds / total_seconds))
                        on_progress(base_pct + weight_pct * pct)
    except Exception as exc:  # noqa: BLE001
        aborted = True
        log(f"progress read failed: {exc}")
    finally:
        if aborted:
            kill_tree(proc.pid)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            kill_tree(proc.pid)
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                pass
    if aborted:
        raise SplitError("已中断")
    if proc.returncode != 0:
        tail = "\n".join(stderr_lines[-12:]) or f"ffmpeg 退出码 {proc.returncode}"
        raise SplitError(tail)


def do_cut(src, dst, start, end, on_phase=None, on_progress=None, abort_event=None, use_cache=True):
    """无损切片。返回用时秒数。on_phase(text), on_progress(0-1)。"""
    started = time.time()
    if not os.path.isfile(src):
        raise SplitError("输入文件不存在：" + src)
    if os.path.abspath(src).lower() == os.path.abspath(dst).lower():
        raise SplitError("输出文件不能和输入相同")

    data, duration = probe(src)
    streams = [s for s in data.get("streams", []) if s.get("codec_type") in ("video", "audio")]
    if not any(s.get("codec_type") == "video" for s in streams):
        raise SplitError("这个文件里没有视频流")
    if duration <= 0:
        raise SplitError("读不到片长，无法校验时间")
    if start < 0:
        raise SplitError("起始时间不能是负数")
    if end <= start:
        raise SplitError("结束时间必须大于起始时间")
    if end > duration + 0.5:
        raise SplitError(f"结束时间超过片长（{format_time(duration)[:8]}）")

    out_ext = os.path.splitext(dst)[1].lower()
    if not out_ext:
        raise SplitError("输出文件要有扩展名，比如 .wmv / .mp4 / .mkv")
    ok, why = check_copy_support(out_ext, streams)
    if not ok:
        raise SplitError(why + "，请把输出扩展名改成 .mkv 或与源相同的格式")

    out_dir = os.path.dirname(os.path.abspath(dst))
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir, exist_ok=True)

    # 所有格式统一两段式：先无损转封装到 mkv（索引/时间戳归一），再从 mkv 切。
    # ASF/TS 这类封装的 seek 索引不可靠，直接切会出现丢帧或音画不同步。
    key = source_key(src) if use_cache else ""
    cache_hit = bool(use_cache and key and _cache["key"] == key and os.path.isfile(_cache["path"]))
    remux_ok = cache_hit
    tmp_path = _cache["path"] if cache_hit else os.path.join(
        tempfile.gettempdir(),
        f"splitcache_{key}.mkv" if use_cache else f"split_src_{os.getpid()}_{int(time.time())}.mkv",
    )
    try:
        if cache_hit:
            if on_phase:
                on_phase("切片（用缓存）")
            base_pct = 0.0
        else:
            if use_cache:
                drop_cache()
            free = shutil.disk_usage(tempfile.gettempdir()).free
            need = os.path.getsize(src) + 64 * 1024 * 1024
            if free < need:
                raise SplitError(f"临时目录空间不够（需要 {human_size(need)}，剩 {human_size(free)}）")
            if on_phase:
                on_phase("转封装")
            run_ffmpeg(
                [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostats", "-i", src,
                 "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-f", "matroska",
                 "-progress", "pipe:1", tmp_path],
                duration, 0.0, 0.5, on_progress, abort_event,
            )
            remux_ok = True
            if use_cache:
                _cache["key"] = key
                _cache["path"] = tmp_path
            if on_phase:
                on_phase("切片")
            base_pct = 0.5

        run_ffmpeg(
            [FFMPEG, "-y", "-hide_banner", "-loglevel", "error", "-nostats",
             "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", tmp_path,
             "-map", "0:v:0?", "-map", "0:a?", "-c", "copy", "-progress", "pipe:1", dst],
            end - start, base_pct, 1.0 - base_pct, on_progress, abort_event,
        )
        if not os.path.isfile(dst) or os.path.getsize(dst) == 0:
            raise SplitError("没有产出文件")
    except SplitError:
        if os.path.isfile(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        raise
    finally:
        # 缓存可复用则保留；CLI 或转封装失败则删掉半成品
        if os.path.isfile(tmp_path) and (not use_cache or not remux_ok):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            if _cache.get("path") == tmp_path:
                _cache["key"] = ""
                _cache["path"] = ""
    return time.time() - started, cache_hit


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_TITLE} {APP_VERSION}")
        self.resizable(False, False)
        self.var_in = tk.StringVar()
        self.var_out = tk.StringVar()
        self.var_start = tk.StringVar(value="00:00:00")
        self.var_end = tk.StringVar(value="00:00:46")
        self.var_status = tk.StringVar(value="选好输入文件，输出名会自动填。")
        self.end_auto = True
        self.out_auto = True
        self.running = False
        self.worker = None
        self.abort_event = threading.Event()
        self.queue = Queue()
        self.last_in_dir = ""
        self.last_out_dir = ""
        self.load_settings()
        self.build_ui()
        if os.environ.get("SPLIT_TOOL_HIDDEN"):
            self.geometry("+-4000+-4000")
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.enable_drop()
        self.after(100, self.poll_queue)

    # ---------- 界面 ----------
    def build_ui(self):
        pad = {"padx": 6, "pady": 5}
        frame = ttk.Frame(self, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        ttk.Label(frame, text="输入文件").grid(row=0, column=0, sticky="w", **pad)
        self.ent_in = ttk.Entry(frame, textvariable=self.var_in, width=62)
        self.ent_in.grid(row=0, column=1, sticky="we", **pad)
        ttk.Button(frame, text="选择…", command=self.pick_input, width=8).grid(row=0, column=2, **pad)

        ttk.Label(frame, text="输出文件").grid(row=1, column=0, sticky="w", **pad)
        self.ent_out = ttk.Entry(frame, textvariable=self.var_out, width=62)
        self.ent_out.grid(row=1, column=1, sticky="we", **pad)
        ttk.Button(frame, text="另存为…", command=self.pick_output, width=8).grid(row=1, column=2, **pad)

        time_frame = ttk.Frame(frame)
        time_frame.grid(row=2, column=1, sticky="w", **pad)
        ttk.Label(frame, text="起始时间").grid(row=2, column=0, sticky="w", **pad)
        self.ent_start = ttk.Entry(time_frame, textvariable=self.var_start, width=14)
        self.ent_start.pack(side="left")
        ttk.Label(time_frame, text="　结束时间").pack(side="left")
        self.ent_end = ttk.Entry(time_frame, textvariable=self.var_end, width=14)
        self.ent_end.pack(side="left")
        ttk.Label(time_frame, text="  格式 00:43:47").pack(side="left")

        self.ent_start.bind("<FocusOut>", self.on_start_changed)
        self.ent_start.bind("<Return>", self.on_start_changed)
        self.ent_end.bind("<KeyRelease>", self.on_end_typed)
        self.ent_in.bind("<KeyRelease>", self.on_in_typed)

        self.progress = ttk.Progressbar(frame, mode="determinate", maximum=1000)
        self.progress.grid(row=3, column=0, columnspan=3, sticky="we", padx=6, pady=(10, 4))

        self.lbl_status = ttk.Label(frame, textvariable=self.var_status, wraplength=560)
        self.lbl_status.grid(row=4, column=0, columnspan=3, sticky="w", padx=6, pady=(0, 8))

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=3, sticky="e", padx=6, pady=(0, 2))
        self.btn_start = ttk.Button(buttons, text="开始", command=self.on_start, width=12)
        self.btn_start.pack(side="left", padx=(0, 8))
        self.btn_abort = ttk.Button(buttons, text="中断", command=self.on_abort, width=12, state="disabled")
        self.btn_abort.pack(side="left")

    # ---------- 设置 ----------
    def load_settings(self):
        try:
            with open(INI_PATH, "r", encoding="utf-8") as fh:
                for line in fh:
                    key, _, value = line.strip().partition("=")
                    if key == "in_dir":
                        self.last_in_dir = value
                    elif key == "out_dir":
                        self.last_out_dir = value
        except OSError:
            pass

    def save_settings(self):
        try:
            with open(INI_PATH, "w", encoding="utf-8") as fh:
                fh.write("in_dir=" + self.last_in_dir + "\n")
                fh.write("out_dir=" + self.last_out_dir + "\n")
        except OSError as exc:
            log(f"save settings failed: {exc}")

    # ---------- 交互 ----------
    def enable_drop(self):
        """让窗口接收资源管理器拖进来的文件。"""
        try:
            self.update_idletasks()
            hwnd = user32.GetParent(self.winfo_id()) or self.winfo_id()
            shell32.DragAcceptFiles(hwnd, True)
            self._drop_hwnd = hwnd
            self._old_proc = user32.GetWindowLongPtrW(hwnd, GWLP_WNDPROC)

            def proc(h, msg, wparam, lparam):
                if msg == WM_DROPFILES:
                    path = None
                    count = 0
                    log("WM_DROPFILES received")
                    try:
                        count = shell32.DragQueryFileW(wparam, 0xFFFFFFFF, None, 0)
                        if count:
                            buf = ctypes.create_unicode_buffer(32767)
                            shell32.DragQueryFileW(wparam, 0, buf, 32767)
                            path = buf.value
                    except Exception as exc:  # noqa: BLE001
                        log(f"drop read failed: {exc}")
                    finally:
                        try:
                            shell32.DragFinish(wparam)
                        except Exception:  # noqa: BLE001
                            pass
                    if path:
                        self.after(0, lambda p=path, n=count: self.accept_dropped(p, n))
                    return 0
                return user32.CallWindowProcW(self._old_proc, h, msg, wparam, lparam)

            self._drop_cb = WNDPROC(proc)
            user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC,
                                     ctypes.cast(self._drop_cb, ctypes.c_void_p).value)
            log(f"drop hook installed on {hwnd}")
        except Exception as exc:  # noqa: BLE001
            log(f"enable drop failed: {exc}")

    def accept_dropped(self, path, count=1):
        path = os.path.normpath(path.strip().strip('"'))
        if not os.path.isfile(path):
            self.set_status("拖进来的不是文件：" + path)
            return
        self.var_in.set(path)
        self.out_auto = True
        self.last_in_dir = os.path.dirname(path)
        self.apply_input(path)
        if count > 1:
            self.set_status(f"拖进来 {count} 个文件，只用了第一个。")
        self.save_settings()

    @staticmethod
    def _usable_dir(path):
        return path if path and os.path.isdir(path) else ""

    def pick_input(self):
        init = (self._usable_dir(self.last_in_dir)
                or self._usable_dir(os.path.dirname(self.var_in.get().strip())))
        path = filedialog.askopenfilename(title="选择输入文件", initialdir=init or None,
                                          filetypes=VIDEO_TYPES)
        if not path:
            return
        self.var_in.set(os.path.normpath(path))
        self.last_in_dir = os.path.dirname(path)
        self.apply_input(path)
        self.save_settings()

    def apply_input(self, path):
        base = os.path.splitext(os.path.basename(path))[0]
        ext = os.path.splitext(path)[1]
        out_dir = self.last_out_dir if self.last_out_dir and os.path.isdir(self.last_out_dir) else os.path.dirname(path)
        if self.out_auto or not self.var_out.get().strip():
            self.var_out.set(os.path.join(out_dir, base + "_cut" + ext))
        self.var_start.set("00:00:00")
        self.end_auto = True
        self.set_status(f"已选：{os.path.basename(path)}")
        try:
            _, duration = probe(path)
            if duration > 0:
                self.var_end.set(format_time(min(DEFAULT_END_OFFSET, duration))[:8])
                self.set_status(f"片长 {format_time(duration)[:8]}，默认切 46 秒，可改时间。")
        except Exception as exc:  # noqa: BLE001
            self.set_status("读不到片长：" + str(exc)[:120])

    def pick_output(self):
        current = self.var_out.get().strip()
        init = (self._usable_dir(self.last_out_dir)
                or self._usable_dir(os.path.dirname(current))
                or self._usable_dir(self.last_in_dir))
        init_name = os.path.basename(current) if current else ""
        path = filedialog.asksaveasfilename(title="输出文件", initialdir=init or None, initialfile=init_name,
                                            defaultextension=os.path.splitext(init_name)[1] or ".wmv",
                                            filetypes=[("视频文件", "*.wmv *.mp4 *.mkv *.mov"), ("所有文件", "*.*")])
        if not path:
            return
        self.var_out.set(os.path.normpath(path))
        self.out_auto = False
        self.last_out_dir = os.path.dirname(path)
        self.save_settings()

    def on_in_typed(self, _event=None):
        self.out_auto = True

    def on_end_typed(self, _event=None):
        self.end_auto = False

    def on_start_changed(self, _event=None):
        if not self.end_auto:
            return
        try:
            start = parse_time(self.var_start.get())
        except ValueError:
            return
        self.var_end.set(format_time(start + DEFAULT_END_OFFSET)[:8])

    def set_status(self, text):
        self.var_status.set(text)

    # ---------- 执行 ----------
    def on_start(self):
        if self.running:
            return
        src = self.var_in.get().strip().strip('"')
        dst = self.var_out.get().strip().strip('"')
        if not src:
            self.set_status("先选输入文件。")
            return
        if not dst:
            self.set_status("输出文件不能为空。")
            return
        try:
            start = parse_time(self.var_start.get())
            end = parse_time(self.var_end.get())
        except ValueError as exc:
            self.set_status(str(exc))
            return
        if not os.path.splitext(dst)[1]:
            self.set_status("输出文件要有扩展名，比如 .wmv。")
            return
        os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
        self.last_in_dir = os.path.dirname(os.path.abspath(src))
        self.last_out_dir = os.path.dirname(os.path.abspath(dst))
        self.save_settings()

        self.running = True
        self.abort_event.clear()
        self.progress["value"] = 0
        self.btn_start["state"] = "disabled"
        self.btn_abort["state"] = "normal"
        for entry in (self.ent_in, self.ent_out, self.ent_start, self.ent_end):
            entry["state"] = "disabled"
        self.set_status("准备中…")
        log(f"START src={src} dst={dst} start={start:.3f} end={end:.3f}")
        self.worker = threading.Thread(target=self.worker_run, args=(src, dst, start, end), daemon=True)
        self.worker.start()

    def worker_run(self, src, dst, start, end):
        def on_phase(text):
            self.queue.put(("phase", text))

        def on_progress(pct):
            self.queue.put(("progress", pct))

        try:
            elapsed, cache_hit = do_cut(src, dst, start, end, on_phase, on_progress, self.abort_event)
            size = os.path.getsize(dst) if os.path.isfile(dst) else 0
            tail = " · 用缓存" if cache_hit else " · 源已缓存"
            self.queue.put(("done", True, f"完成：{os.path.basename(dst)} · {human_size(size)} · 用时 {elapsed:.1f} 秒{tail}"))
            log(f"DONE dst={dst} size={size} elapsed={elapsed:.1f} cache={cache_hit}")
        except SplitError as exc:
            text = "已中断，没有产出文件" if str(exc) == "已中断" else "失败：" + str(exc)
            self.queue.put(("done", False, text))
            log(f"FAIL {exc}")
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("done", False, f"失败：{type(exc).__name__}: {exc}"))
            log(f"FAIL {type(exc).__name__}: {exc}")

    def poll_queue(self):
        try:
            while True:
                msg = self.queue.get_nowait()
                if msg[0] == "phase":
                    self.progress["value"] = 0
                    self.set_status(msg[1] + " 0%")
                elif msg[0] == "progress":
                    pct = max(0.0, min(1.0, msg[1]))
                    self.progress["value"] = int(pct * 1000)
                    self.set_status(f"处理中 {pct * 100:.0f}%")
                elif msg[0] == "done":
                    self.finish(msg[1], msg[2])
        except Empty:
            pass
        self.after(100, self.poll_queue)

    def finish(self, ok, text):
        self.running = False
        self.btn_start["state"] = "normal"
        self.btn_abort["state"] = "disabled"
        for entry in (self.ent_in, self.ent_out, self.ent_start, self.ent_end):
            entry["state"] = "normal"
        if ok:
            self.progress["value"] = 1000
        else:
            self.progress["value"] = 0
        self.set_status(text)

    def on_abort(self):
        if not self.running:
            return
        self.abort_event.set()
        self.btn_abort["state"] = "disabled"
        self.set_status("正在中断…")

    def on_close(self):
        if self.running:
            if not messagebox.askyesno(APP_TITLE, "正在处理，确定中断并退出？"):
                return
            self.abort_event.set()
            time.sleep(0.8)
        self.save_settings()
        drop_cache()
        self.destroy()


def cli_main(argv):
    if len(argv) != 5:
        log("CLI usage: --run <src> <dst> <start> <end>")
        return 2
    try:
        src, dst = argv[1], argv[2]
        start = parse_time(argv[3])
        end = parse_time(argv[4])
        elapsed, _ = do_cut(src, dst, start, end, lambda t: log("phase: " + t), None, None, use_cache=False)
        log(f"CLI OK {dst} elapsed={elapsed:.1f}")
        return 0
    except Exception as exc:  # noqa: BLE001
        log(f"CLI FAIL {type(exc).__name__}: {exc}")
        return 1


def clean_stale_temp():
    """启动清残留：上次崩溃留下的转封装文件；1 小时前的临时文件。"""
    try:
        temp_dir = tempfile.gettempdir()
        now = time.time()
        for name in os.listdir(temp_dir):
            if not name.endswith(".mkv"):
                continue
            path = os.path.join(temp_dir, name)
            try:
                if name.startswith("splitcache_"):
                    os.remove(path)
                elif name.startswith("split_src_") and now - os.path.getmtime(path) > 3600:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass


# 同源缓存：同一个源切多段时，只转封装一次
_cache = {"key": "", "path": ""}


def source_key(path):
    stat = os.stat(path)
    raw = f"{os.path.abspath(path).lower()}|{stat.st_size}|{int(stat.st_mtime)}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


def drop_cache():
    path = _cache.get("path")
    if path and os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass
    _cache["key"] = ""
    _cache["path"] = ""


def single_instance():
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Local\\QoderSplitTool_v1")
    if kernel32.GetLastError() == 183:
        return None
    return handle


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "--run":
        log(f"cli v{APP_VERSION}")
        sys.exit(cli_main(sys.argv[1:]))
    log(f"start v{APP_VERSION}")
    mutex = single_instance()
    if mutex is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo(APP_TITLE, "已经开着一个了。")
        root.destroy()
        return
    clean_stale_temp()
    atexit.register(drop_cache)
    app = App()
    app.mainloop()
    drop_cache()
    del mutex


if __name__ == "__main__":
    main()
