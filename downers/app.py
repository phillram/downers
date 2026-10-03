"""The Downers window: a compact, dark tkinter front end for yt-dlp."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

import yt_dlp

from downers import __version__, engine, updater
from downers.engine import Job, Settings
from downers.paths import (APP_DIR, ARCHIVE_FILE, DATA_DIR, FROZEN, ICON, QUEUE_FILE,
                           RESUME_DIR, SETTINGS_FILE)

BG, PANEL, FIELD, HOVER = "#1e1f22", "#2b2d31", "#383a40", "#4e5058"
FG, MUTED, ACCENT = "#e6e6e6", "#9a9ca3", "#5865f2"
GOOD, WARN, BAD = "#3ba55d", "#faa61a", "#ed4245"

LOG_LIMIT = 3000  # lines kept, in memory and in the log window
LOG_LINE_LIMIT = 2000  # characters per line; Tk slows to a crawl on huge lines

# Display label -> settings value, for each drop-down
VIDEO_Q = {"Best": "best", "2160p 4K": "2160", "1440p": "1440", "1080p": "1080",
           "720p": "720", "480p": "480", "360p": "360"}
CONTAINER = {"MP4": "mp4", "MKV": "mkv"}
AUDIO_FMT = {"Original": "original", "MP3": "mp3", "M4A": "m4a", "Opus": "opus",
             "FLAC": "flac", "WAV": "wav"}
AUDIO_Q = {"Best": "best", "320k": "320", "256k": "256", "192k": "192",
           "128k": "128", "96k": "96"}
BROWSER = {"None": "", "Firefox": "firefox", "Chrome": "chrome", "Edge": "edge",
           "Brave": "brave", "Opera": "opera", "Vivaldi": "vivaldi"}

# Options that describe the connection rather than the download. These apply to every
# download as it starts, so a login set after "sign in" errors works on Resume.
LIVE = ("cookies", "cookies_file", "proxy", "rate_limit")

RETRIES = 2                 # automatic retries after the first try
RETRY_DELAY = 60            # seconds to wait before each
UPDATE_CHECK_EVERY = 7 * 24 * 3600


class Tooltip:
    """A short explanation that appears when the pointer rests on a widget."""

    def __init__(self, widget: tk.Misc, text: str):
        self.widget, self.text, self.tip, self.pending = widget, text, None, None
        widget.bind("<Enter>", self._schedule, add=True)
        widget.bind("<Leave>", self._hide, add=True)
        widget.bind("<ButtonPress>", self._hide, add=True)

    def _schedule(self, _):
        self.pending = self.widget.after(500, self._show)

    def _show(self):
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget, bg=HOVER)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, bg="#111214", fg=FG, justify="left",
                 wraplength=320, padx=8, pady=5, font=("Segoe UI", 9)
                 ).pack(padx=1, pady=1)

    def _hide(self, _=None):
        if self.pending:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.tip:
            self.tip.destroy()
            self.tip = None


def tip(widget, text):
    Tooltip(widget, text)
    return widget


def label_for(mapping: dict, value: str) -> str:
    return next((k for k, v in mapping.items() if v == value), next(iter(mapping)))


def dark_title_bar(window: tk.Misc) -> None:
    """Ask Windows 10/11 for a dark title bar. Harmless anywhere else."""
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        on = ctypes.c_int(1)
        for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE, and its pre-20H1 number
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), 4) == 0:
                break
        # Repaint the frame in case the window is already showing
        flags = 0x27  # SWP_NOSIZE | NOMOVE | NOZORDER | FRAMECHANGED | NOACTIVATE
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, flags)
    except Exception:
        pass


def write_json(path: Path, data) -> None:
    """Write via a temporary file and swap it in, so a crash or power cut mid-write
    can't leave half a queue behind (which would read back as an empty one)."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def on_screen(geometry: str) -> bool:
    """Is a saved "WxH+X+Y" still on a connected monitor? Unplugging one
    shouldn't leave the window somewhere it can't be reached."""
    m = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geometry or "")
    if not m:
        return False
    x, y = int(m[3]), int(m[4])
    try:
        metric = ctypes.windll.user32.GetSystemMetrics
        left, top, width, height = (metric(i) for i in (76, 77, 78, 79))  # the virtual screen
    except Exception:
        return True
    # The title bar's left end has to be grabbable
    return left <= x + 100 < left + width and top <= y < top + height - 40


def center_over(win: tk.Misc, over: tk.Misc | None = None, size: tuple[int, int] | None = None):
    """Place win in the middle of `over`, or of the screen, kept fully on screen."""
    win.update_idletasks()
    w, h = size or (win.winfo_reqwidth(), win.winfo_reqheight())
    if over is not None and over.winfo_viewable():
        cx = over.winfo_rootx() + over.winfo_width() // 2
        cy = over.winfo_rooty() + over.winfo_height() // 2
    else:
        cx, cy = win.winfo_screenwidth() // 2, win.winfo_screenheight() // 2
    x = max(0, min(cx - w // 2, win.winfo_vrootwidth() - w))
    y = max(0, min(cy - h // 2, win.winfo_vrootheight() - h - 40))
    win.geometry(f"{w}x{h}+{x}+{y}" if size else f"+{x}+{y}")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()  # until the dark title bar is set, or it can stay white
        self.title("Downers")
        self.configure(bg=BG)
        try:
            self.iconbitmap(default=str(ICON))  # also applies to the other windows
        except tk.TclError:
            pass
        DATA_DIR.mkdir(parents=True, exist_ok=True)

        # Worker threads report here and the UI thread applies it in _pump. Changes for
        # a job merge into one entry, and new log lines go into a capped buffer, so
        # however fast downloads report, what's waiting stays small.
        self.lock = threading.Lock()
        self.pending: dict[int, dict] = {}
        self.pending_log: deque[tuple[int, str, str]] = deque(maxlen=LOG_LIMIT)
        self.pending_calls: deque = deque()  # results from other threads, run in _pump
        self.jobs: dict[int, Job] = {}
        self.threads: dict[int, threading.Thread] = {}
        self.remove_when_stopped: set[int] = set()
        self.log_lines: deque[tuple[str, str]] = deque(maxlen=LOG_LIMIT)
        self.log_window: tk.Toplevel | None = None
        self.options_window: tk.Toplevel | None = None

        saved = self._read_json(SETTINGS_FILE, {})
        s = Settings.from_dict(saved)
        self.v = {
            "mode": tk.StringVar(value=s.mode),
            "video_quality": tk.StringVar(value=label_for(VIDEO_Q, s.video_quality)),
            "container": tk.StringVar(value=label_for(CONTAINER, s.container)),
            "audio_format": tk.StringVar(value=label_for(AUDIO_FMT, s.audio_format)),
            "audio_quality": tk.StringVar(value=label_for(AUDIO_Q, s.audio_quality)),
            "cookies": tk.StringVar(value=label_for(BROWSER, s.cookies)),
            **{k: tk.BooleanVar(value=getattr(s, k)) for k in
               ("thumbnail", "metadata", "sponsorblock", "whole_playlist",
                "compatible", "archive")},
            **{k: tk.StringVar(value=getattr(s, k)) for k in
               ("subtitles", "items", "proxy", "rate_limit", "cookies_file", "output_dir")},
        }
        self.parallel = tk.IntVar(value=saved.get("parallel", 1))
        self.auto_retry = tk.BooleanVar(value=saved.get("auto_retry", True))
        self.check_updates = tk.BooleanVar(value=saved.get("check_updates", True))
        self.last_update_check = saved.get("last_update_check", 0)
        self.url = tk.StringVar()
        self.status = tk.StringVar()

        self._style()
        self._build()
        self.v["mode"].trace_add("write", lambda *_: self._sync_controls())
        self.v["audio_format"].trace_add("write", lambda *_: self._sync_controls())
        self._sync_controls()

        for data in self._read_json(QUEUE_FILE, []):
            try:
                self._add_job(Job.from_dict(data))
            except Exception:
                pass
        # Resume files whose link is gone, e.g. after a crash
        keys = {j.key for j in self.jobs.values()}
        for f in RESUME_DIR.glob("*.txt"):
            if f.stem not in keys:
                f.unlink(missing_ok=True)
        self._log(f"Downers {__version__}, yt-dlp {yt_dlp.version.__version__}"
                  + (" (updated copy)" if updater.active else ""), "muted")
        if not engine.ffmpeg_path():
            self._log("ffmpeg.exe not found beside Downers: merging, MP3 and thumbnails "
                      "will fail. From source, run: python tools/fetch_ffmpeg.py", "warn")
        elif not engine.ffprobe_path():
            self._log("ffprobe.exe not found beside ffmpeg.exe: MKV thumbnails and Cut "
                      "sponsors will fail. From source, run: python tools/fetch_ffmpeg.py",
                      "warn")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()
        # Never smaller than the controls, so it fits at any display scaling
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.normal_geometry = saved.get("geometry", "")
        if on_screen(self.normal_geometry):
            self.geometry(self.normal_geometry)
        else:
            # A fixed size, or the window would widen whenever the status text grows
            width = max(self.winfo_reqwidth(), self.ui_font.measure("x" * 140))
            center_over(self, size=(width, self.winfo_reqheight()))
        dark_title_bar(self)
        self.bind("<Configure>", self._track_geometry, add=True)
        self.deiconify()
        if saved.get("maximized"):
            self.update_idletasks()  # place it first, so un-maximizing returns there
            self.state("zoomed")
        self.pump_job = self.after(100, self._pump)
        self._schedule()
        self.after(15000, self._maybe_check_for_update)
        if FROZEN:
            # Later, so a copy that's just restarted us has finished exiting
            self.after(30000, lambda: threading.Thread(target=clean_stale_unpacks,
                                                       daemon=True).start())

    # ------------------------------------------------------------------ look

    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=FIELD,
                     bordercolor=FIELD, lightcolor=FIELD, darkcolor=FIELD,
                     troughcolor=PANEL, arrowcolor=FG, selectbackground=ACCENT,
                     selectforeground="white", insertcolor=FG, font=("Segoe UI", 9))
        st.map(".", foreground=[("disabled", "#62646b")])
        st.configure("TButton", background=FIELD, padding=(8, 2), borderwidth=0, width=-4)
        st.map("TButton", background=[("pressed", ACCENT), ("active", HOVER)])
        st.configure("Accent.TButton", background=ACCENT, foreground="white")
        st.map("Accent.TButton", background=[("active", "#4752c4")])
        st.configure("Muted.TLabel", foreground=MUTED)
        st.configure("Bar.TFrame", background=PANEL)
        st.configure("Bar.TLabel", background=PANEL, foreground=MUTED)
        st.configure("Hint.TLabel", background=PANEL, foreground=MUTED)
        st.configure("Placeholder.TLabel", background=FIELD, foreground=MUTED)
        st.configure("Section.TLabel", foreground=FG, font=("Segoe UI", 9, "bold"))
        st.configure("TSeparator", background=HOVER)
        for w in ("TCheckbutton", "TRadiobutton"):
            st.configure(w, background=BG, indicatorbackground=FIELD,
                         indicatorforeground="white", indicatormargin=(0, 0, 4, 0))
            st.map(w, background=[("active", BG)],
                   indicatorbackground=[("selected", ACCENT), ("disabled", PANEL)])
        st.configure("TCombobox", arrowsize=12, padding=(4, 1))
        st.map("TCombobox", fieldbackground=[("readonly", FIELD), ("disabled", PANEL)],
               foreground=[("readonly", FG), ("disabled", "#62646b")],
               selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        st.configure("TEntry", padding=(4, 2))
        st.configure("TSpinbox", arrowsize=10, padding=(4, 1))
        # Pixel sizes below are measured from the font so they follow display scaling
        self.ui_font = font.Font(family="Segoe UI", size=9)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG,
                     rowheight=self.ui_font.metrics("linespace") + 6, borderwidth=0)
        st.map("Treeview", background=[("selected", ACCENT)])
        st.configure("Treeview.Heading", background=FIELD, foreground=MUTED,
                     relief="flat", padding=(4, 2))
        st.map("Treeview.Heading", background=[("active", HOVER)])
        st.configure("Vertical.TScrollbar", background=FIELD, troughcolor=PANEL,
                     bordercolor=PANEL, lightcolor=FIELD, darkcolor=FIELD,
                     arrowcolor=MUTED, gripcount=0, arrowsize=12)
        st.map("Vertical.TScrollbar", background=[("active", HOVER)])
        # The drop-down lists of comboboxes are plain tk widgets
        self.option_add("*TCombobox*Listbox.background", FIELD)
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 9))
        self.option_add("*Menu.background", PANEL)
        self.option_add("*Menu.foreground", FG)
        self.option_add("*Menu.activeBackground", ACCENT)
        self.option_add("*Menu.relief", "flat")

    def _combo(self, parent, key, mapping, width):
        return ttk.Combobox(parent, textvariable=self.v[key], values=list(mapping),
                            state="readonly", width=width)

    def _build(self):
        # Toolbar: the buttons that act on the queue, in groups, then the window ones
        bar = ttk.Frame(self, style="Bar.TFrame", padding=(10, 6))
        bar.pack(fill="x")
        groups = (
            (("Pause", lambda: self._for_selected(self._pause),
              "Pause the selected downloads. What's downloaded so far is kept."),
             ("Pause all", lambda: self._for_all(self._pause),
              "Pause everything in the list.")),
            (("Resume", lambda: self._for_selected(self._resume),
              "Carry on with the selected downloads, or retry them if they failed."),
             ("Resume all", lambda: self._for_all(self._resume),
              "Carry on with everything paused, and retry everything that failed."),
             ("Download again", lambda: self._for_selected(self._again),
              "Download the selected finished rows from scratch, replacing the files.")),
            (("Remove", self._remove_selected,
              "Take the selected rows off the list (Delete key). Finished files stay; "
              "for unfinished ones it asks, then deletes the partial files."),
             ("Clear done", self._clear_done,
              "Take every finished row off the list. The files stay.")),
        )
        for i, group in enumerate(groups):
            if i:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=8)
            for text, command, help_text in group:
                tip(ttk.Button(bar, text=text, command=command), help_text).pack(
                    side="left", padx=(0, 4))
        tip(ttk.Button(bar, text="Log", command=self._show_log),
            "Everything yt-dlp reported, including why something failed.").pack(side="right")
        tip(ttk.Button(bar, text="Options", command=self._show_options),
            "Login, proxy, subtitles, speed limit, updating yt-dlp.").pack(side="right", padx=4)
        ttk.Separator(bar, orient="vertical").pack(side="right", fill="y", padx=8)
        ttk.Label(bar, textvariable=self.status, style="Bar.TLabel").pack(side="right")

        root = ttk.Frame(self, padding=(10, 10, 10, 10))
        root.pack(fill="both", expand=True)

        # The link box, with a hint inside it while it's empty
        add = ttk.Frame(root)
        add.pack(fill="x")
        self.entry = entry = ttk.Entry(add, textvariable=self.url)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._add_from_text(self.url.get()))
        entry.focus_set()
        placeholder = ttk.Label(entry, style="Placeholder.TLabel",
                                text="Paste a link to a video, playlist or channel, then press Enter")
        placeholder.bind("<Button-1>", lambda e: entry.focus_set())

        def show_placeholder(*_):
            if self.url.get():
                placeholder.place_forget()
            else:
                placeholder.place(x=5, rely=0.5, anchor="w")
        self.url.trace_add("write", show_placeholder)
        show_placeholder()
        tip(ttk.Button(add, text="Add", style="Accent.TButton",
                       command=lambda: self._add_from_text(self.url.get())),
            "Add the link above (Enter).").pack(side="left", padx=(6, 0))
        tip(ttk.Button(add, text="Paste", command=self._paste),
            "Add every link on the clipboard (Ctrl+V anywhere in the window)."
            ).pack(side="left", padx=(4, 0))

        # What to download: each choice beside the drop-downs it unlocks
        fmt = ttk.Frame(root)
        fmt.pack(fill="x", pady=(10, 0))
        ttk.Radiobutton(fmt, text="Video", value="video", variable=self.v["mode"]).pack(side="left")
        self.c_vq = tip(self._combo(fmt, "video_quality", VIDEO_Q, 9),
                        "The highest resolution to get. Best takes the top one available.")
        self.c_vq.pack(side="left", padx=(6, 4))
        self.c_ct = tip(self._combo(fmt, "container", CONTAINER, 5),
                        "MP4 plays almost everywhere. MKV can hold any format, subtitles "
                        "included.")
        self.c_ct.pack(side="left")
        ttk.Separator(fmt, orient="vertical").pack(side="left", fill="y", padx=14)
        ttk.Radiobutton(fmt, text="Audio only", value="audio",
                        variable=self.v["mode"]).pack(side="left")
        self.c_af = tip(self._combo(fmt, "audio_format", AUDIO_FMT, 8),
                        "Original keeps the site's own audio, untouched: the best quality, "
                        "usually as .opus or .m4a. MP3 plays everywhere.")
        self.c_af.pack(side="left", padx=(6, 4))
        self.c_aq = tip(self._combo(fmt, "audio_quality", AUDIO_Q, 6),
                        "Bitrate when converting. Best keeps the most detail.")
        self.c_aq.pack(side="left")

        checks = ttk.Frame(root)
        checks.pack(fill="x", pady=(8, 0))
        for key, text, help_text in (
                ("thumbnail", "Cover art", "Put the video's thumbnail inside the file."),
                ("metadata", "Tags & chapters",
                 "Save the title, channel and date in the file, and chapter markers."),
                ("sponsorblock", "Cut sponsors",
                 "Cut out sponsor segments, self-promotion and \"like and subscribe\" "
                 "reminders, using SponsorBlock's community list."),
                ("whole_playlist", "Whole playlist",
                 "When a video link is part of a playlist (…watch?v=…&list=…), download "
                 "the whole playlist instead of just that video.")):
            tip(ttk.Checkbutton(checks, text=text, variable=self.v[key]), help_text).pack(
                side="left", padx=(0, 14))

        out = ttk.Frame(root)
        out.pack(fill="x", pady=(8, 10))
        ttk.Label(out, text="Save to", style="Muted.TLabel").pack(side="left")
        ttk.Entry(out, textvariable=self.v["output_dir"]).pack(side="left", fill="x",
                                                               expand=True, padx=6)
        tip(ttk.Button(out, text="Browse", command=self._browse),
            "Choose where downloads go.").pack(side="left")
        tip(ttk.Button(out, text="Open", command=lambda: self._open(self.v["output_dir"].get())),
            "Open the download folder.").pack(side="left", padx=(4, 0))

        table = ttk.Frame(root)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("title", "format", "progress"),
                                 show="headings", selectmode="extended", height=8)
        m = self.ui_font.measure
        for col, text, width, stretch in (("title", "Title", m("x" * 34), True),
                                          ("format", "Format", m("1080p MKV    "), False),
                                          ("progress", "Status", m("x" * 46), True)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        for tag, color in (("Done", GOOD), ("Error", BAD), ("Paused", WARN), ("Waiting", WARN),
                           ("Queued", MUTED)):
            self.tree.tag_configure(tag, foreground=color)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.empty_hint = ttk.Label(self.tree, style="Hint.TLabel", justify="center",
                                    text="Your downloads will show here.\n"
                                         "Right-click one for more, double-click to open "
                                         "its folder.")
        self.tree.bind("<Button-3>", self._context_menu)
        self.tree.bind("<Double-1>", self._double_click)
        self.tree.bind("<Delete>", lambda e: self._remove_selected())
        self.tree.bind("<Control-a>", lambda e: self.tree.selection_set(self.tree.get_children()))
        self.bind("<Control-v>", self._ctrl_v)

        self.menu = tk.Menu(self, tearoff=False)
        for text, fn in (("Pause", self._pause), ("Resume / retry", self._resume),
                         ("Download again", self._again), ("Remove", None), (None, None),
                         ("Open folder", self._open_job_folder), ("Copy link", self._copy_link),
                         ("Copy yt-dlp command", self._copy_command)):
            if text is None:
                self.menu.add_separator()
            else:
                self.menu.add_command(label=text, command=self._remove_selected if fn is None
                                      else lambda f=fn: self._for_selected(f))

    def _ctrl_v(self, event):
        # In a text box, Ctrl+V pastes as usual; anywhere else it adds the links
        if not isinstance(event.widget, (ttk.Entry, tk.Entry, ttk.Combobox, tk.Text)):
            self._paste()

    def _sync_controls(self):
        video = self.v["mode"].get() == "video"
        lossless = self.v["audio_format"].get() in ("FLAC", "WAV")
        for combo, on in ((self.c_vq, video), (self.c_ct, video), (self.c_af, not video),
                          (self.c_aq, not video and not lossless)):
            combo.configure(state="readonly" if on else "disabled")

    # -------------------------------------------------------------- settings

    def current_settings(self) -> Settings:
        v = self.v
        return Settings(
            mode=v["mode"].get(),
            video_quality=VIDEO_Q[v["video_quality"].get()],
            container=CONTAINER[v["container"].get()],
            audio_format=AUDIO_FMT[v["audio_format"].get()],
            audio_quality=AUDIO_Q[v["audio_quality"].get()],
            cookies=BROWSER[v["cookies"].get()],
            **{k: v[k].get() for k in ("thumbnail", "metadata", "sponsorblock",
                                       "whole_playlist", "compatible", "archive",
                                       "subtitles", "items", "proxy", "rate_limit", "cookies_file")},
            output_dir=v["output_dir"].get().strip() or Settings().output_dir,
        )

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _track_geometry(self, event):
        # Only while normal: maximized, keep the size to come back to
        if event.widget is self and self.state() == "normal":
            self.normal_geometry = self.geometry()

    def _save(self):
        try:
            data = self.current_settings().to_dict() | {
                "parallel": self.parallel.get(), "geometry": self.normal_geometry,
                "maximized": self.state() == "zoomed", "auto_retry": self.auto_retry.get(),
                "check_updates": self.check_updates.get(),
                "last_update_check": self.last_update_check}
            write_json(SETTINGS_FILE, data)
            jobs = [j.to_dict() for j in self.jobs.values()
                    if j.status != "Done" and j.id not in self.remove_when_stopped]
            write_json(QUEUE_FILE, jobs)
        except (OSError, tk.TclError):
            pass

    # ------------------------------------------------------------------ queue

    def _add_from_text(self, text: str):
        urls = [w for w in text.split() if w.startswith(("http://", "https://"))]
        if not urls:
            self.status.set("That isn't a link: it should start with http")
            return
        settings = self.current_settings()
        # The same link with the same settings, not yet finished, is already in hand
        pending = {(j.url, j.settings.summary()) for j in self.jobs.values()
                   if j.status != "Done" and j.id not in self.remove_when_stopped}
        added = 0
        for url in dict.fromkeys(urls):
            if (url, settings.summary()) not in pending:
                self._add_job(Job(url, Settings.from_dict(settings.to_dict())))
                added += 1
        self.url.set("")
        if added and settings.items:
            self.v["items"].set("")  # "Items" is for the next link, not every link after
        self._save()
        self._schedule()
        skipped = len(set(urls)) - added
        if skipped:
            self.status.set("Already in the list" if not added else
                            f"Added {added}; {skipped} already in the list")

    def _add_job(self, job: Job):
        self.jobs[job.id] = job
        self.tree.insert("", "end", iid=str(job.id), values=self._row(job), tags=(job.status,))

    def _row(self, job: Job):
        return (job.title or job.url, job.settings.summary(), job.progress or job.status)

    def _update_row(self, job: Job):
        if self.tree.exists(str(job.id)):
            self.tree.item(str(job.id), values=self._row(job), tags=(job.status,))

    def _schedule(self):
        """Start queued jobs while there are free download slots."""
        for job in self.jobs.values():
            if len(self.threads) >= max(1, self.parallel.get()):
                break
            if job.status == "Queued" and job.id not in self.threads:
                live = self.current_settings()
                job.settings = replace(job.settings, **{k: getattr(live, k) for k in LIVE})
                job.stop.clear()
                job.status, job.progress = "Starting", ""
                self._update_row(job)
                t = threading.Thread(target=self._worker, args=(job,), daemon=True)
                self.threads[job.id] = t
                t.start()
        self._refresh_status()

    @staticmethod
    def _resume_file(job: Job) -> Path:
        return RESUME_DIR / f"{job.key}.txt"

    def _worker(self, job: Job):
        # "Remember finished videos" already skips by ID; otherwise this link's own list.
        # Download again uses only its own (emptied) list, so nothing is skipped.
        if job.settings.archive and not job.fresh:
            archive = ARCHIVE_FILE
        else:
            RESUME_DIR.mkdir(parents=True, exist_ok=True)
            archive = self._resume_file(job)
        try:
            engine.run(job, self._emit, archive)
        except Exception as e:
            self._emit(job, status="Error", progress=str(e)[:120], log=f"Failed: {e}",
                       level="error")
        self._emit(job, _finished=True)

    def _emit(self, job: Job, **changes):
        """Called from worker threads; the UI thread applies it in _pump."""
        with self.lock:
            if "log" in changes:
                self.pending_log.append((job.id, changes.pop("log")[:LOG_LINE_LIMIT],
                                         changes.pop("level", "info")))
            changes.pop("level", None)
            if changes:
                self.pending.setdefault(job.id, {}).update(changes)

    def _call_soon(self, fn, *args):
        """Thread-safe: run fn(*args) on the UI thread. Tk mustn't be touched from
        any other thread."""
        with self.lock:
            self.pending_calls.append((fn, args))

    def _pump(self):
        with self.lock:
            pending, self.pending = self.pending, {}
            logs = list(self.pending_log)
            self.pending_log.clear()
            calls = list(self.pending_calls)
            self.pending_calls.clear()
        for fn, args in calls:
            fn(*args)
        self._log_many([(f"#{job_id} {text}", level) for job_id, text, level in logs])
        finished = False
        for job_id, ch in pending.items():
            job = self.jobs.get(job_id)
            if job is None:
                continue
            for key in ("status", "progress", "title", "folder"):
                if key in ch:
                    setattr(job, key, ch[key])
            if ch.get("_finished"):
                finished = True
                self.threads.pop(job_id, None)
                job.fresh = False
                self._after_run(job)
                if job_id in self.remove_when_stopped:
                    job.discard_partials()
                    self._forget(job)
                    continue
            self._update_row(job)
        if self._tick_waiting():
            finished = True
        if finished:
            self._save()
            self._schedule()
        elif pending:
            self._refresh_status()
        self.pump_job = self.after(150, self._pump)

    def _after_run(self, job: Job):
        """A run has ended. Retry it later if that could help, else settle it."""
        failed = job.status == "Error" or (job.status == "Done" and job.errors)
        if (failed and self.auto_retry.get() and job.retries < RETRIES
                and job.id not in self.remove_when_stopped and not job.stop.is_set()
                and engine.retryable(job.last_error)):
            job.retries += 1
            job.status, job.retry_at = "Waiting", time.time() + RETRY_DELAY
            self._log(f"#{job.id} Retrying in {RETRY_DELAY}s "
                      f"(try {job.retries + 1} of {RETRIES + 1})", "warn")
            return
        if job.status == "Done":
            job.retries = 0
            if not job.errors:  # with problems, keep it so a retry skips what worked
                self._resume_file(job).unlink(missing_ok=True)

    def _tick_waiting(self) -> bool:
        """Count down waiting retries; requeue the ones whose time has come."""
        requeued = False
        now = time.time()
        for job in self.jobs.values():
            if job.status != "Waiting":
                continue
            left = job.retry_at - now
            if left <= 0:
                job.status, job.progress = "Queued", ""
                requeued = True
            else:
                text = f"Retrying in {left:.0f}s (try {job.retries + 1} of {RETRIES + 1})"
                if text != job.progress:
                    job.progress = text
                    self._update_row(job)
        return requeued

    def _refresh_status(self):
        counts: dict[str, int] = {}
        for j in self.jobs.values():
            key = "active" if j.active else j.status
            counts[key] = counts.get(key, 0) + 1
        parts = [f"{counts[k]} {label}" for k, label in
                 (("active", "downloading"), ("Queued", "queued"), ("Waiting", "retrying"),
                  ("Paused", "paused"), ("Error", "failed"), ("Done", "done"))
                 if counts.get(k)]
        self.status.set(" · ".join(parts))
        # The taskbar shows how it's going while the window is minimized
        active = counts.get("active", 0)
        self.title(f"Downers · {active} downloading" if active else "Downers")
        if self.jobs:
            self.empty_hint.place_forget()
        else:
            self.empty_hint.place(relx=0.5, rely=0.45, anchor="center")

    # ---------------------------------------------------------- job actions

    def _for_selected(self, fn):
        if not self.tree.selection():
            self.status.set("Select a row first")
            return
        self._apply(fn, self.tree.selection())

    def _for_all(self, fn):
        self._apply(fn, self.tree.get_children())

    def _apply(self, fn, ids):
        for iid in ids:
            if (job := self.jobs.get(int(iid))) is not None:
                fn(job)
        self._save()
        self._schedule()

    def _pause(self, job: Job):
        if job.id in self.threads:
            job.stop.set()
            job.progress = "Pausing…"
        elif job.status in ("Queued", "Waiting"):
            job.status, job.progress = "Paused", ""
        self._update_row(job)

    def _resume(self, job: Job):
        # Not "Done": Resume all would re-run every finished link (Download again does)
        if job.id not in self.threads and job.status in ("Paused", "Error", "Waiting"):
            job.status, job.progress, job.retries = "Queued", "", 0
            self._update_row(job)

    def _again(self, job: Job):
        if job.id not in self.threads and job.status in ("Done", "Error"):
            self._resume_file(job).unlink(missing_ok=True)
            job.fresh, job.retries = True, 0
            job.status, job.progress = "Queued", ""
            self._update_row(job)

    def _remove_selected(self):
        if not self.tree.selection():
            self.status.set("Select a row first")
            return
        jobs = [self.jobs[int(i)] for i in self.tree.selection() if int(i) in self.jobs]
        unfinished = [j for j in jobs if j.partials or j.id in self.threads]
        if unfinished and not messagebox.askyesno(
                "Downers", f"Remove {len(unfinished)} unfinished download(s) and delete "
                           "their partial files?\nFinished files are kept.", parent=self):
            return
        for job in jobs:
            self._remove(job)
        self._save()
        self._schedule()

    def _remove(self, job: Job):
        if job.id in self.threads:
            # Its files are still open; _pump deletes them once it has stopped
            job.stop.set()
            self.remove_when_stopped.add(job.id)
            self.tree.delete(str(job.id))
        else:
            job.discard_partials()
            self._forget(job)

    def _forget(self, job: Job):
        self._resume_file(job).unlink(missing_ok=True)
        self.jobs.pop(job.id, None)
        self.remove_when_stopped.discard(job.id)
        if self.tree.exists(str(job.id)):
            self.tree.delete(str(job.id))

    def _clear_done(self):
        for job in [j for j in self.jobs.values() if j.status == "Done"]:
            self._forget(job)
        self._save()
        self._refresh_status()

    def _open_job_folder(self, job: Job):
        # A playlist's own folder once a file has landed, else the download folder
        self._open(job.folder if job.folder and Path(job.folder).is_dir()
                   else job.settings.output_dir)

    def _copy_link(self, job: Job):
        self.clipboard_clear()
        self.clipboard_append(job.url)

    def _copy_command(self, job: Job):
        archive = ARCHIVE_FILE if job.settings.archive else None
        self.clipboard_clear()
        self.clipboard_append(engine.command_line(job.settings, job.url, archive))
        self.status.set("yt-dlp command copied")

    def _double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == "cell":
            self._for_selected(self._open_job_folder)

    def _context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    # ---------------------------------------------------------------- misc

    def _paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            text = ""
        if "http" not in text:
            self.status.set("No link on the clipboard")
            return
        self._add_from_text(text)

    def _browse(self):
        folder = filedialog.askdirectory(parent=self,
                                         initialdir=self.v["output_dir"].get() or None)
        if folder:
            self.v["output_dir"].set(str(Path(folder)))

    def _open(self, folder: str):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _log(self, text: str, level: str = "info"):
        self._log_many([(text, level)])

    def _log_many(self, lines: list[tuple[str, str]]):
        lines = lines[-LOG_LIMIT:]
        self.log_lines.extend(lines)  # a deque: the oldest drop off
        if self.log_window is not None and lines:
            self._append_log(lines)

    def _append_log(self, lines):
        box = self.log_text
        at_end = box.yview()[1] > 0.99
        box.configure(state="normal")
        # One insert for the whole batch: text, tag, text, tag, ...
        box.insert("end", *(x for text, level in lines for x in (text + "\n", level)))
        # The open window obeys the same limit as the stored log
        excess = int(box.index("end-1c").split(".")[0]) - 1 - LOG_LIMIT
        if excess > 0:
            box.delete("1.0", f"{excess + 1}.0")
        box.configure(state="disabled")
        if at_end:
            box.see("end")

    def _toplevel(self, title, attr, size=None):
        """A popup window, centered over this one once the caller has filled it."""
        if getattr(self, attr) is not None:
            getattr(self, attr).deiconify()
            getattr(self, attr).lift()
            getattr(self, attr).focus_set()
            return None
        win = tk.Toplevel(self, bg=BG)
        win.withdraw()  # dark title bar only takes if set before the window shows

        def show():
            center_over(win, self, size)
            dark_title_bar(win)
            win.deiconify()
            win.focus_set()
        win.after(20, show)
        win.title(title)
        win.transient(self)

        win.protocol("WM_DELETE_WINDOW", lambda: self._close_toplevel(attr))
        win.bind("<Escape>", lambda e: self._close_toplevel(attr))
        setattr(self, attr, win)
        return win

    def _close_toplevel(self, attr):
        win = getattr(self, attr)
        setattr(self, attr, None)
        if win is not None:
            win.destroy()
        self._save()

    def _show_log(self):
        char, line = self.ui_font.measure("x"), self.ui_font.metrics("linespace")
        win = self._toplevel("Downers log", "log_window", size=(char * 110, line * 22))
        if win is None:
            return
        frame = ttk.Frame(win, padding=6)
        frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(frame, bg=PANEL, fg=FG, relief="flat", wrap="word",
                                font=("Consolas", 9), state="disabled", borderwidth=0)
        scroll = ttk.Scrollbar(frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        for tag, color in (("info", FG), ("muted", MUTED), ("warn", WARN), ("error", BAD)):
            self.log_text.tag_configure(tag, foreground=color)
        if self.log_lines:
            self._append_log(list(self.log_lines))
        self.log_text.see("end")

    def _file_picker(self, parent, key):
        frame = ttk.Frame(parent)
        ttk.Entry(frame, textvariable=self.v[key], width=24).pack(side="left")

        def browse():
            path = filedialog.askopenfilename(
                parent=self.options_window, filetypes=[("Cookies", "*.txt"), ("All files", "*")])
            if path:
                self.v[key].set(str(Path(path)))
        ttk.Button(frame, text="Browse", command=browse).pack(side="left", padx=(4, 0))
        return frame

    def _show_options(self):
        win = self._toplevel("Downers options", "options_window")
        if win is None:
            return
        win.resizable(False, False)
        f = ttk.Frame(win, padding=(16, 12))
        f.pack(fill="both", expand=True)
        f.columnconfigure(1, weight=1)
        row = 0

        def section(title):
            nonlocal row
            ttk.Label(f, text=title, style="Section.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(10 if row else 0, 4))
            row += 1

        def field(label, widget, hint):
            nonlocal row
            ttk.Label(f, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12))
            widget.grid(row=row, column=1, sticky="w")
            ttk.Label(f, text=hint, style="Muted.TLabel").grid(row=row + 1, column=1,
                                                               sticky="w", pady=(0, 6))
            row += 2

        def check(key, text):
            nonlocal row
            ttk.Checkbutton(f, text=text, variable=self.v[key]).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=2)
            row += 1

        section("Downloads")
        field("At once", ttk.Spinbox(f, from_=1, to=4, textvariable=self.parallel, width=5,
                                     state="readonly", command=self._schedule),
              "How many links download at the same time")
        field("Speed limit", ttk.Entry(f, textvariable=self.v["rate_limit"], width=24),
              "Such as 2M for 2 MB/s. Blank for no limit")
        field("Subtitles", ttk.Entry(f, textvariable=self.v["subtitles"], width=24),
              "Languages to put inside videos, such as en or en,es")
        field("Items", ttk.Entry(f, textvariable=self.v["items"], width=24),
              "Part of a playlist, such as 1-20. Only for the next link you add")
        check("compatible", "Prefer H.264 video (for old TVs and phones; usually 1080p at most)")
        check("archive", "Remember finished videos and skip them next time")
        ttk.Checkbutton(f, text=f"Retry failed downloads automatically ({RETRIES} more tries, "
                                f"a minute apart)", variable=self.auto_retry).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=2)
        row += 1

        section("Access")
        field("Login from", self._combo(f, "cookies", BROWSER, 22),
              "Use a browser's YouTube login. Close that browser first")
        field("Cookies file", self._file_picker(f, "cookies_file"),
              "Or an exported cookies.txt, used instead")
        field("Proxy", ttk.Entry(f, textvariable=self.v["proxy"], width=24),
              "Such as socks5://127.0.0.1:1080")

        section("yt-dlp")
        line = ttk.Frame(f)
        line.grid(row=row, column=0, columnspan=2, sticky="we")
        ttk.Label(line, text=f"Version {yt_dlp.version.__version__}"
                  + (" (updated)" if updater.active else "")).pack(side="left")
        self.update_btn = tip(ttk.Button(line, text="Update yt-dlp", command=self._update_ytdlp),
                              "Sites change often. If downloads start failing, update.")
        self.update_btn.pack(side="right")
        row += 1
        ttk.Checkbutton(f, text="Check for a new yt-dlp once a week", variable=self.check_updates
                        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(6, 0))
        row += 1

        ttk.Separator(f).grid(row=row, column=0, columnspan=2, sticky="we", pady=12)
        row += 1
        ttk.Label(f, style="Muted.TLabel", justify="left", wraplength=self.ui_font.measure("x" * 70),
                  text="Login, proxy and speed limit apply to every download as it starts. "
                       "The rest apply to links added after you change them."
                  ).grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1
        ttk.Button(f, text="Close", command=lambda: self._close_toplevel("options_window")
                   ).grid(row=row, column=1, sticky="e", pady=(10, 0))

    def _maybe_check_for_update(self):
        """Once a week, look for a newer yt-dlp in the background and offer it."""
        if not self.check_updates.get() or time.time() - self.last_update_check < UPDATE_CHECK_EVERY:
            return
        current, proxy = yt_dlp.version.__version__, self.v["proxy"].get().strip()

        def work():
            try:
                latest = updater.latest_version(proxy)
            except Exception as e:
                self._call_soon(self._log, f"Couldn't check for a yt-dlp update: {e}", "warn")
                return
            self._call_soon(offer, latest)

        def offer(latest):
            self.last_update_check = time.time()  # asked or not, not again for a week
            self._save()
            if updater.is_newer(latest, current) and messagebox.askyesno(
                    "Downers", f"A new yt-dlp is out: {latest} (you have {current}).\n"
                               "Sites change often, so newer is usually better. "
                               "Update now?", parent=self):
                self._update_ytdlp()

        threading.Thread(target=work, daemon=True).start()

    def _update_ytdlp(self):
        self._set_update_button("disabled", "Checking…")
        current, proxy = yt_dlp.version.__version__, self.v["proxy"].get().strip()

        def work():
            try:
                result = updater.update(current, proxy)
            except Exception as e:
                result = None, f"Update failed: {e}"
            self._call_soon(done, *result)

        def done(changed, message):
            self._log(message, "error" if changed is None else "info")
            self._set_update_button("normal", "Update yt-dlp")
            running = "\nDownloads in progress pause and carry on after." if self.threads else ""
            if changed and messagebox.askyesno(
                    "Downers", f"{message}\nRestart Downers now to use it?{running}",
                    parent=self):
                self._restart()
            elif not changed:
                messagebox.showinfo("Downers", message, parent=self)

        threading.Thread(target=work, daemon=True).start()

    def _set_update_button(self, state, text):
        # The button lives in Options, which may be closed
        try:
            self.update_btn.configure(state=state, text=text)
        except (AttributeError, tk.TclError):
            pass

    def _restart(self):
        self._pause_everything()
        self._save()
        command = [sys.executable] if FROZEN else [sys.executable, "-m", "downers"]
        # Without this, the new exe would reuse this one's unpack folder, which is
        # deleted as this one exits
        env = os.environ | {"PYINSTALLER_RESET_ENVIRONMENT": "1"}
        release_instance()  # or the new one would see this one and bow out
        subprocess.Popen(command, cwd=APP_DIR, env=env)
        self.destroy()

    def _on_close(self):
        if self.threads:
            if not messagebox.askyesno(
                    "Downers", "Downloads are running. Pause them and quit?\n"
                               "They'll carry on next time you open Downers.", parent=self):
                return
            self._pause_everything()
        self._save()
        self.destroy()

    def _pause_everything(self):
        """Stop what's running, and queue it, so it carries on next time Downers opens."""
        for job in self.jobs.values():
            job.stop.set()
        for t in list(self.threads.values()):
            t.join(timeout=3)
        for job in self.jobs.values():
            if job.active:
                job.status = "Queued"

    def destroy(self):
        self.after_cancel(self.pump_job)
        super().destroy()


def clean_stale_unpacks() -> None:
    """Downers.exe unpacks itself into %TEMP%\\_MEI…, removed again on a normal exit.
    A crash or a forced close leaves it behind (about 35 MB each), so clear out
    earlier ones. Only folders holding our icon, never our own, and anything
    still in use simply fails to delete."""
    own = Path(getattr(sys, "_MEIPASS", "")).resolve()
    for folder in Path(os.environ.get("TEMP", "")).glob("_MEI*"):
        if folder.resolve() != own and (folder / ICON.name).exists():
            shutil.rmtree(folder, ignore_errors=True)


_instance_lock = None


def single_instance() -> bool:
    """Claim the one-Downers-at-a-time lock. Two copies would resume the same queue
    into the same partial files. If another has it, bring that one forward."""
    global _instance_lock
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _instance_lock = kernel32.CreateMutexW(None, False, "Local\\Downers.SingleInstance")
        if ctypes.get_last_error() != 183:  # ERROR_ALREADY_EXISTS
            return True
    except Exception:
        return True
    user32 = ctypes.windll.user32
    hwnd = find_main_window()
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return False


def find_main_window() -> int:
    """The main window's handle. Its title changes ("Downers · 2 downloading"), so
    match on that rather than on an exact title."""
    found = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def check(hwnd, _):
        buffer = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buffer, 256)
        if buffer.value == "Downers" or buffer.value.startswith("Downers · "):
            found.append(hwnd)
            return False
        return True
    ctypes.windll.user32.EnumWindows(proto(check), 0)
    return found[0] if found else 0


def release_instance() -> None:
    global _instance_lock
    if _instance_lock:
        ctypes.windll.kernel32.CloseHandle(_instance_lock)
        _instance_lock = None


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
    except Exception:
        pass
    if not single_instance():
        return 0
    App().mainloop()
    return 0
