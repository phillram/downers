"""The download side of Downers: no tkinter in here.

A Job is one link (a video, a playlist or a whole channel) plus the Settings
that were chosen when it was added. Settings turn into a yt-dlp command line
(`build_argv`), which yt-dlp's own parser turns into options. Going through the
command line keeps every post-processor wired up exactly as yt-dlp intends, and
gives a command the user can copy and run themselves.

Pausing is a cancel that keeps the partial files. yt-dlp resumes .part files and
skips anything already finished, so resuming is just running the job again.
"""

from __future__ import annotations

import itertools
import shlex
import shutil
import threading
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.utils import DownloadCancelled

VIDEO_QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360"]
CONTAINERS = ["mp4", "mkv"]
AUDIO_FORMATS = ["original", "mp3", "m4a", "opus", "flac", "wav"]
AUDIO_QUALITIES = ["best", "320", "256", "192", "128", "96"]
BROWSERS = ["", "firefox", "chrome", "edge", "brave", "opera", "vivaldi"]
SPONSOR_CATEGORIES = "sponsor,selfpromo,interaction"

SINGLE_TEMPLATE = "%(title)s.%(ext)s"
PLAYLIST_TEMPLATE = "%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s"

# Formats whose files can carry cover art
THUMBNAIL_AUDIO = {"original", "mp3", "m4a", "opus", "flac"}


@dataclass
class Settings:
    mode: str = "video"                 # "video" or "audio"
    video_quality: str = "best"         # "best" or a max height
    container: str = "mp4"
    compatible: bool = False            # prefer H.264/AAC for old players and TVs
    audio_format: str = "original"      # "original" keeps YouTube's stream, no re-encode
    audio_quality: str = "best"         # "best" or kbps
    thumbnail: bool = True
    metadata: bool = True               # title/artist/date tags plus chapters
    subtitles: str = ""                 # e.g. "en" or "en,es"; blank for none
    sponsorblock: bool = False
    whole_playlist: bool = False        # a watch?v=…&list=… link takes the playlist
    items: str = ""                     # e.g. "1-10" or "1,4,7-9"
    archive: bool = False
    cookies: str = ""                   # browser to borrow a login from
    proxy: str = ""
    rate_limit: str = ""                # e.g. "2M"
    output_dir: str = str(Path.home() / "Downloads" / "Downers")

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        """Short label for the queue, e.g. '1080p MP4' or 'MP3 320k'."""
        if self.mode == "video":
            q = "Best" if self.video_quality == "best" else f"{self.video_quality}p"
            return f"{q} {self.container.upper()}"
        fmt = "Audio" if self.audio_format == "original" else self.audio_format.upper()
        if self.audio_format in ("flac", "wav"):
            return fmt
        q = "best" if self.audio_quality == "best" else f"{self.audio_quality}k"
        return f"{fmt} {q}"


# ---------------------------------------------------------------- helpers

def _bundled(name: str, module: str, finder: str) -> str | None:
    """A program from PATH, else the copy a pip package ships."""
    found = shutil.which(name)
    if found:
        return found
    try:
        return getattr(__import__(module), finder)()
    except Exception:
        return None


def ffmpeg_path() -> str | None:
    return _bundled("ffmpeg", "imageio_ffmpeg", "get_ffmpeg_exe")


def deno_path() -> str | None:
    return _bundled("deno", "deno", "find_deno_bin")


def build_argv(s: Settings, archive_file: Path | None = None) -> list[str]:
    """The yt-dlp command line (without the URL) for these settings."""
    argv = ["--ignore-config", "--ignore-errors", "--no-write-playlist-metafiles",
            "--concurrent-fragments", "4", "-P", s.output_dir, "-o", SINGLE_TEMPLATE]

    if ffmpeg := ffmpeg_path():
        argv += ["--ffmpeg-location", ffmpeg]
    if deno := deno_path():
        argv += ["--js-runtimes", f"deno:{deno}"]

    sort = []
    if s.mode == "video":
        if s.video_quality != "best":
            sort.append(f"res:{s.video_quality}")
        if s.compatible:
            sort += ["vcodec:h264", "acodec:m4a"]
        if s.audio_quality != "best":
            sort.append(f"abr:{s.audio_quality}")
        argv += ["-f", "bv*+ba/b", "--merge-output-format", s.container]
        if s.subtitles.strip():
            argv += ["--write-subs", "--sub-langs", s.subtitles.strip(), "--embed-subs"]
    else:
        if s.audio_quality != "best":
            sort.append(f"abr:{s.audio_quality}")
        fmt = "best" if s.audio_format == "original" else s.audio_format
        quality = "0" if s.audio_quality == "best" else f"{s.audio_quality}K"
        argv += ["-f", "ba/b", "-x", "--audio-format", fmt, "--audio-quality", quality]
    if sort:
        argv += ["-S", ",".join(sort)]

    if s.thumbnail and (s.mode == "video" or s.audio_format in THUMBNAIL_AUDIO):
        # YouTube serves WebP, which MP4 and MP3 can't hold
        argv += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]
    if s.metadata:
        argv += ["--embed-metadata", "--embed-chapters"]
    if s.sponsorblock:
        argv += ["--sponsorblock-remove", SPONSOR_CATEGORIES]
    argv.append("--yes-playlist" if s.whole_playlist else "--no-playlist")
    if s.items.strip():
        argv += ["-I", s.items.strip()]
    if s.archive and archive_file:
        argv += ["--download-archive", str(archive_file)]
    if s.cookies:
        argv += ["--cookies-from-browser", s.cookies]
    if s.proxy.strip():
        argv += ["--proxy", s.proxy.strip()]
    if s.rate_limit.strip():
        argv += ["-r", s.rate_limit.strip()]
    return argv


def command_line(s: Settings, url: str, archive_file: Path | None = None) -> str:
    """Something the user can paste into a terminal."""
    return "yt-dlp " + shlex.join(build_argv(s, archive_file) + [url])


def looks_like_collection(url: str) -> bool:
    return any(m in url for m in ("list=", "/playlist", "/@", "/channel/", "/user/", "/c/"))


# -------------------------------------------------------------------- jobs

_ids = itertools.count(1)


@dataclass
class Job:
    url: str
    settings: Settings
    title: str = ""
    status: str = "Queued"      # Queued, Starting, Downloading, Processing, Paused, Done, Error
    progress: str = ""
    errors: int = 0
    id: int = field(default_factory=lambda: next(_ids))
    stop: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("Starting", "Downloading", "Processing")

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "status": self.status,
                "settings": self.settings.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        status = data.get("status", "Queued")
        if status not in ("Done", "Error", "Paused", "Queued"):
            status = "Paused"   # was running when the app closed
        return cls(url=data["url"], title=data.get("title", ""), status=status,
                   settings=Settings.from_dict(data.get("settings", {})))


class _Logger:
    def __init__(self, job: Job, emit: Callable):
        self.job, self.emit = job, emit

    def debug(self, msg):
        if msg.startswith("[debug] "):
            return
        # Per-chunk progress lines are shown in the queue instead
        if msg.startswith("[download]") and "% of" in msg:
            return
        self.emit(self.job, log=msg)

    def info(self, msg):
        self.debug(msg)

    def warning(self, msg):
        self.emit(self.job, log=f"WARNING: {msg}", level="warn")

    def error(self, msg):
        self.job.errors += 1
        self.emit(self.job, log=msg, level="error")


def run(job: Job, emit: Callable, archive_file: Path | None = None) -> None:
    """Download one job on the calling thread, reporting through emit(job, **changes).

    Returns when the job is finished, failed, or job.stop was set (paused).
    """
    s = job.settings
    Path(s.output_dir).mkdir(parents=True, exist_ok=True)
    opts = yt_dlp.parse_options(build_argv(s, archive_file)).ydl_opts
    job.errors = 0

    def item_label(info: dict) -> str:
        i, n = info.get("playlist_index"), info.get("n_entries") or info.get("playlist_count")
        return f"[{i}/{n}] " if i and n else ""

    def progress(d):
        if job.stop.is_set():
            raise DownloadCancelled("Paused")
        info = d.get("info_dict", {})
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            pct = f"{d['downloaded_bytes'] / total * 100:.0f}%" if total else ""
            speed = (d.get("_speed_str") or "").strip()
            eta = (d.get("_eta_str") or "").strip()
            stream = " audio" if info.get("vcodec") == "none" and s.mode == "video" else ""
            emit(job, status="Downloading",
                 progress=f"{item_label(info)}{pct}{stream}  {speed}  ETA {eta}".strip())
        elif d["status"] == "finished":
            emit(job, status="Processing", progress=f"{item_label(info)}processing")

    def postprocess(d):
        if job.stop.is_set():
            raise DownloadCancelled("Paused")
        if d["status"] == "started":
            emit(job, status="Processing",
                 progress=f"{item_label(d.get('info_dict', {}))}{d['postprocessor']}")

    opts.update(progress_hooks=[progress], postprocessor_hooks=[postprocess],
                logger=_Logger(job, emit), noprogress=True)

    emit(job, status="Starting", progress="Looking up link…")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False, process=False)
            if job.stop.is_set():
                raise DownloadCancelled("Paused")
            if info is None:
                emit(job, status="Error", progress="Couldn't read this link (see log)")
                return
            collection = info.get("_type") in ("playlist", "multi_video") or (
                info.get("_type") in ("url", "url_transparent") and s.whole_playlist
                and looks_like_collection(job.url))
            if collection:
                ydl.params["outtmpl"]["default"] = PLAYLIST_TEMPLATE
            if not job.title:
                emit(job, title=info.get("title") or info.get("id") or job.url)
            ydl.process_ie_result(info, download=True)
    except DownloadCancelled:
        emit(job, status="Paused", progress="Paused: resume to continue")
        return
    except Exception as e:  # anything yt-dlp lets escape
        if job.stop.is_set():
            emit(job, status="Paused", progress="Paused: resume to continue")
        else:
            job.errors += 1
            emit(job, status="Error", progress=str(e).splitlines()[0][:120], level="error",
                 log=f"Failed: {e}")
        return

    if job.stop.is_set():
        emit(job, status="Paused", progress="Paused: resume to continue")
    elif job.errors:
        emit(job, status="Done", progress=f"Finished with {job.errors} problem(s), see log")
    else:
        emit(job, status="Done", progress="Done")
