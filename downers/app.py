"""The Downers window: a compact, dark tkinter front end for yt-dlp."""

from __future__ import annotations

import ctypes
import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, font, messagebox, ttk

import yt_dlp

from downers import __version__, engine, updater
from downers.engine import Job, Settings
from downers.paths import (APP_DIR, ARCHIVE_FILE, DATA_DIR, FROZEN, ICON, QUEUE_FILE,
                           SETTINGS_FILE)

BG, PANEL, FIELD, HOVER = "#1e1f22", "#2b2d31", "#383a40", "#4e5058"
FG, MUTED, ACCENT = "#e6e6e6", "#9a9ca3", "#5865f2"
GOOD, WARN, BAD = "#3ba55d", "#faa61a", "#ed4245"

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

        self.events: queue.Queue = queue.Queue()
        self.jobs: dict[int, Job] = {}
        self.threads: dict[int, threading.Thread] = {}
        self.remove_when_stopped: set[int] = set()
        self.log_lines: list[tuple[str, str]] = []
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
        self._log(f"Downers {__version__}, yt-dlp {yt_dlp.version.__version__}"
                  + (" (updated copy)" if updater.active else ""), "muted")
        if not engine.ffmpeg_path():
            self._log("ffmpeg not found: merging, MP3 and thumbnails will fail. "
                      "Run: pip install imageio-ffmpeg", "warn")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh_status()
        # Never smaller than the controls, so it fits at any display scaling
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.normal_geometry = saved.get("geometry", "")
        if on_screen(self.normal_geometry):
            self.geometry(self.normal_geometry)
        dark_title_bar(self)
        self.bind("<Configure>", self._track_geometry, add=True)
        self.deiconify()
        if saved.get("maximized"):
            self.update_idletasks()  # place it first, so un-maximizing returns there
            self.state("zoomed")
        self.pump_job = self.after(100, self._pump)
        self._schedule()

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
        root = ttk.Frame(self, padding=(10, 8, 10, 6))
        root.pack(fill="both", expand=True)

        add = ttk.Frame(root)
        add.pack(fill="x")
        entry = ttk.Entry(add, textvariable=self.url)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._add_from_text(self.url.get()))
        entry.focus_set()
        ttk.Button(add, text="Add", style="Accent.TButton",
                   command=lambda: self._add_from_text(self.url.get())).pack(side="left", padx=(6, 0))
        ttk.Button(add, text="Paste", command=self._paste).pack(side="left", padx=(4, 0))

        fmt = ttk.Frame(root)
        fmt.pack(fill="x", pady=(8, 0))
        ttk.Radiobutton(fmt, text="Video", value="video", variable=self.v["mode"]).pack(side="left")
        ttk.Radiobutton(fmt, text="Audio only", value="audio",
                        variable=self.v["mode"]).pack(side="left", padx=(8, 16))
        ttk.Label(fmt, text="Video", style="Muted.TLabel").pack(side="left")
        self.c_vq = self._combo(fmt, "video_quality", VIDEO_Q, 9)
        self.c_vq.pack(side="left", padx=(4, 2))
        self.c_ct = self._combo(fmt, "container", CONTAINER, 5)
        self.c_ct.pack(side="left", padx=(0, 16))
        ttk.Label(fmt, text="Audio", style="Muted.TLabel").pack(side="left")
        self.c_af = self._combo(fmt, "audio_format", AUDIO_FMT, 8)
        self.c_af.pack(side="left", padx=(4, 2))
        self.c_aq = self._combo(fmt, "audio_quality", AUDIO_Q, 6)
        self.c_aq.pack(side="left")

        checks = ttk.Frame(root)
        checks.pack(fill="x", pady=(6, 0))
        for key, text in (("thumbnail", "Embed thumbnail"), ("metadata", "Tags & chapters"),
                          ("sponsorblock", "Cut sponsors"),
                          ("whole_playlist", "Full playlist from video links")):
            ttk.Checkbutton(checks, text=text, variable=self.v[key]).pack(side="left", padx=(0, 12))

        out = ttk.Frame(root)
        out.pack(fill="x", pady=(6, 8))
        ttk.Label(out, text="Save to", style="Muted.TLabel").pack(side="left")
        ttk.Entry(out, textvariable=self.v["output_dir"]).pack(side="left", fill="x",
                                                               expand=True, padx=6)
        ttk.Button(out, text="Browse", command=self._browse).pack(side="left")
        ttk.Button(out, text="Open", command=lambda: self._open(self.v["output_dir"].get())
                   ).pack(side="left", padx=(4, 0))

        table = ttk.Frame(root)
        table.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(table, columns=("title", "format", "progress"),
                                 show="headings", selectmode="extended", height=8)
        m = self.ui_font.measure
        for col, text, width, stretch in (("title", "Title", m("x" * 40), True),
                                          ("format", "Format", m("Audio best  "), False),
                                          ("progress", "Progress", m("x" * 34), True)):
            self.tree.heading(col, text=text, anchor="w")
            self.tree.column(col, width=width, stretch=stretch, anchor="w")
        for tag, color in (("Done", GOOD), ("Error", BAD), ("Paused", WARN), ("Queued", MUTED)):
            self.tree.tag_configure(tag, foreground=color)
        scroll = ttk.Scrollbar(table, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Button-3>", self._context_menu)
        self.tree.bind("<Double-1>", lambda e: self._for_selected(self._open_job_folder))
        self.tree.bind("<Delete>", lambda e: self._for_selected(self._remove))

        bar = ttk.Frame(root)
        bar.pack(fill="x", pady=(6, 0))
        # Pause and Resume act on the selection, or on everything if nothing is selected
        for text, cmd in (("Pause", lambda: self._for_selected(self._pause, all_if_none=True)),
                          ("Resume", lambda: self._for_selected(self._resume, all_if_none=True)),
                          ("Remove", lambda: self._for_selected(self._remove)),
                          ("Clear done", self._clear_done)):
            ttk.Button(bar, text=text, command=cmd).pack(side="left", padx=(0, 4))
        ttk.Button(bar, text="Log", command=self._show_log).pack(side="right")
        ttk.Button(bar, text="Options", command=self._show_options).pack(side="right", padx=4)
        ttk.Label(bar, textvariable=self.status, style="Muted.TLabel").pack(side="right", padx=8)

        self.menu = tk.Menu(self, tearoff=False)
        for text, fn in (("Pause", self._pause), ("Resume / retry", self._resume),
                         ("Remove", self._remove), (None, None),
                         ("Open folder", self._open_job_folder), ("Copy link", self._copy_link),
                         ("Copy yt-dlp command", self._copy_command)):
            if text is None:
                self.menu.add_separator()
            else:
                self.menu.add_command(label=text, command=lambda f=fn: self._for_selected(f))

    def _sync_controls(self):
        video = self.v["mode"].get() == "video"
        lossless = self.v["audio_format"].get() in ("FLAC", "WAV")
        for combo, on in ((self.c_vq, video), (self.c_ct, video), (self.c_af, not video),
                          (self.c_aq, video or not lossless)):
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
                "maximized": self.state() == "zoomed"}
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            jobs = [j.to_dict() for j in self.jobs.values()
                    if j.status != "Done" and j.id not in self.remove_when_stopped]
            QUEUE_FILE.write_text(json.dumps(jobs, indent=2), encoding="utf-8")
        except (OSError, tk.TclError):
            pass

    # ------------------------------------------------------------------ queue

    def _add_from_text(self, text: str):
        urls = [w for w in text.split() if w.startswith(("http://", "https://"))]
        if not urls:
            self.status.set("Paste a link (http…) first.")
            return
        settings = self.current_settings()
        for url in urls:
            self._add_job(Job(url, Settings.from_dict(settings.to_dict())))
        self.url.set("")
        self._save()
        self._schedule()

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
                job.stop.clear()
                job.status, job.progress = "Starting", ""
                self._update_row(job)
                t = threading.Thread(target=self._worker, args=(job,), daemon=True)
                self.threads[job.id] = t
                t.start()
        self._refresh_status()

    def _worker(self, job: Job):
        archive = ARCHIVE_FILE if job.settings.archive else None
        try:
            engine.run(job, self._emit, archive)
        except Exception as e:
            self._emit(job, status="Error", progress=str(e)[:120], log=f"Failed: {e}",
                       level="error")
        self.events.put((job.id, {"_finished": True}))

    def _emit(self, job: Job, **changes):
        """Called from worker threads; the UI thread applies it in _pump."""
        self.events.put((job.id, changes))

    def _pump(self):
        try:
            while True:
                job_id, ch = self.events.get_nowait()
                job = self.jobs.get(job_id)
                if job is None:
                    continue
                if "log" in ch:
                    level = ch.get("level", "info")
                    self._log(f"#{job.id} {ch['log']}", level)
                for key in ("status", "progress", "title"):
                    if key in ch:
                        setattr(job, key, ch[key])
                if ch.get("_finished"):
                    self.threads.pop(job_id, None)
                    if job_id in self.remove_when_stopped:
                        self._forget(job)
                    self._save()
                    self._schedule()
                else:
                    self._update_row(job)
                    if "status" in ch:
                        self._refresh_status()
        except queue.Empty:
            pass
        self.pump_job = self.after(150, self._pump)

    def _refresh_status(self):
        counts: dict[str, int] = {}
        for j in self.jobs.values():
            key = "active" if j.active else j.status
            counts[key] = counts.get(key, 0) + 1
        parts = [f"{counts[k]} {label}" for k, label in
                 (("active", "downloading"), ("Queued", "queued"), ("Paused", "paused"),
                  ("Error", "failed"), ("Done", "done")) if counts.get(k)]
        self.status.set(" · ".join(parts) or "Paste a link to start")

    # ---------------------------------------------------------- job actions

    def _for_selected(self, fn, all_if_none=False):
        ids = self.tree.selection() or (self.tree.get_children() if all_if_none else ())
        for iid in ids:
            if (job := self.jobs.get(int(iid))) is not None:
                fn(job)
        self._save()
        self._schedule()

    def _pause(self, job: Job):
        if job.id in self.threads:
            job.stop.set()
            job.progress = "Pausing…"
        elif job.status == "Queued":
            job.status, job.progress = "Paused", ""
        self._update_row(job)

    def _resume(self, job: Job):
        if job.id not in self.threads and job.status in ("Paused", "Error", "Done"):
            job.status, job.progress = "Queued", ""
            self._update_row(job)

    def _remove(self, job: Job):
        if job.id in self.threads:
            job.stop.set()
            self.remove_when_stopped.add(job.id)
            self.tree.delete(str(job.id))
        else:
            self._forget(job)

    def _forget(self, job: Job):
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
        self._open(job.settings.output_dir)

    def _copy_link(self, job: Job):
        self.clipboard_clear()
        self.clipboard_append(job.url)

    def _copy_command(self, job: Job):
        archive = ARCHIVE_FILE if job.settings.archive else None
        self.clipboard_clear()
        self.clipboard_append(engine.command_line(job.settings, job.url, archive))
        self.status.set("yt-dlp command copied")

    def _context_menu(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.tk_popup(event.x_root, event.y_root)

    # ---------------------------------------------------------------- misc

    def _paste(self):
        try:
            self._add_from_text(self.clipboard_get())
        except tk.TclError:
            self.status.set("Clipboard is empty.")

    def _browse(self):
        folder = filedialog.askdirectory(initialdir=self.v["output_dir"].get() or None)
        if folder:
            self.v["output_dir"].set(str(Path(folder)))

    def _open(self, folder: str):
        path = Path(folder)
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)

    def _log(self, text: str, level: str = "info"):
        self.log_lines.append((text, level))
        del self.log_lines[:-3000]
        if self.log_window is not None:
            self._append_log(text, level)

    def _append_log(self, text, level):
        box = self.log_text
        at_end = box.yview()[1] > 0.99
        box.configure(state="normal")
        box.insert("end", text + "\n", level)
        box.configure(state="disabled")
        if at_end:
            box.see("end")

    def _toplevel(self, title, geometry, attr):
        if getattr(self, attr) is not None:
            getattr(self, attr).deiconify()
            getattr(self, attr).lift()
            return None
        win = tk.Toplevel(self, bg=BG)
        win.withdraw()  # dark title bar only takes if set before the window shows
        win.after(20, lambda: (dark_title_bar(win), win.deiconify()))
        win.title(title)
        win.geometry(geometry)
        win.transient(self)

        win.protocol("WM_DELETE_WINDOW", lambda: self._close_toplevel(attr))
        setattr(self, attr, win)
        return win

    def _close_toplevel(self, attr):
        win = getattr(self, attr)
        setattr(self, attr, None)
        if win is not None:
            win.destroy()
        self._save()

    def _show_log(self):
        win = self._toplevel("Downers log", "700x320", "log_window")
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
        for text, level in self.log_lines:
            self._append_log(text, level)
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
        win = self._toplevel("Downers options", "", "options_window")
        if win is None:
            return
        win.resizable(False, False)
        f = ttk.Frame(win, padding=12)
        f.pack(fill="both", expand=True)
        rows = (
            ("Subtitles", ttk.Entry(f, textvariable=self.v["subtitles"], width=24),
             "Languages to embed, e.g. en or en,es (video only)"),
            ("Items", ttk.Entry(f, textvariable=self.v["items"], width=24),
             "Which playlist/channel items, e.g. 1-20 or 1,5,8-10"),
            ("Login from", self._combo(f, "cookies", BROWSER, 22),
             "Use a browser's YouTube login (age-restricted, members, private)"),
            ("Cookies file", self._file_picker(f, "cookies_file"),
             "Or an exported cookies.txt; used instead of the browser"),
            ("Proxy", ttk.Entry(f, textvariable=self.v["proxy"], width=24),
             "e.g. socks5://127.0.0.1:1080 or http://host:port"),
            ("Speed limit", ttk.Entry(f, textvariable=self.v["rate_limit"], width=24),
             "e.g. 2M for 2 MB/s; blank for no limit"),
            ("At once", ttk.Spinbox(f, from_=1, to=4, textvariable=self.parallel, width=5,
                                    state="readonly", command=self._schedule),
             "How many links download at the same time"),
        )
        for r, (label, widget, hint) in enumerate(rows):
            ttk.Label(f, text=label).grid(row=r * 2, column=0, sticky="w", padx=(0, 10))
            widget.grid(row=r * 2, column=1, sticky="w")
            ttk.Label(f, text=hint, style="Muted.TLabel").grid(row=r * 2 + 1, column=1,
                                                               sticky="w", pady=(0, 6))
        r = len(rows) * 2
        ttk.Checkbutton(f, text="Prefer H.264/AAC (plays on old TVs and phones; may cap at 1080p)",
                        variable=self.v["compatible"]).grid(row=r, column=0, columnspan=2,
                                                            sticky="w", pady=2)
        ttk.Checkbutton(f, text="Remember finished videos and skip them next time "
                               "(handy for keeping a channel in sync)",
                        variable=self.v["archive"]).grid(row=r + 1, column=0, columnspan=2,
                                                         sticky="w", pady=2)
        ttk.Label(f, text="Options apply to links added after you change them.",
                  style="Muted.TLabel").grid(row=r + 2, column=0, columnspan=2, sticky="w",
                                             pady=(8, 0))
        btns = ttk.Frame(f)
        btns.grid(row=r + 3, column=0, columnspan=2, sticky="we", pady=(10, 0))
        self.update_btn = ttk.Button(btns, text="Update yt-dlp", command=self._update_ytdlp)
        self.update_btn.pack(side="left")
        ttk.Button(btns, text="Close",
                   command=lambda: self._close_toplevel("options_window")).pack(side="right")

    def _update_ytdlp(self):
        if self.threads:
            messagebox.showinfo("Downers", "Pause or finish the downloads first.", parent=self)
            return
        self.update_btn.configure(state="disabled", text="Checking…")
        current, proxy = yt_dlp.version.__version__, self.v["proxy"].get().strip()

        def work():
            try:
                result = updater.update(current, proxy)
            except Exception as e:
                result = None, f"Update failed: {e}"
            self.after(0, done, *result)

        def done(changed, message):
            self._log(message, "error" if changed is None else "info")
            try:
                self.update_btn.configure(state="normal", text="Update yt-dlp")
            except tk.TclError:
                pass
            if changed and messagebox.askyesno(
                    "Downers", f"{message}\nRestart Downers now to use it?", parent=self):
                self._restart()
            elif not changed:
                messagebox.showinfo("Downers", message, parent=self)

        threading.Thread(target=work, daemon=True).start()

    def _restart(self):
        self._save()
        command = [sys.executable] if FROZEN else [sys.executable, "-m", "downers"]
        subprocess.Popen(command, cwd=APP_DIR)
        self.destroy()

    def _on_close(self):
        if self.threads:
            if not messagebox.askyesno(
                    "Downers", "Downloads are running. Pause them and quit?\n"
                               "They'll carry on next time you open Downers.", parent=self):
                return
            for job in self.jobs.values():
                job.stop.set()
            for t in list(self.threads.values()):
                t.join(timeout=3)
            for job in self.jobs.values():
                if job.active:
                    job.status = "Paused"
        self._save()
        self.destroy()

    def destroy(self):
        self.after_cancel(self.pump_job)
        super().destroy()


def main() -> int:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # crisp text on scaled displays
    except Exception:
        pass
    App().mainloop()
    return 0
