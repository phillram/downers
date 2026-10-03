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

import gc
import glob
import itertools
import shlex
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Callable

import yt_dlp
from yt_dlp.utils import DownloadCancelled

from downers.paths import APP_DIR

VIDEO_QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360"]
CONTAINERS = ["mp4", "mkv"]
AUDIO_FORMATS = ["original", "mp3", "m4a", "opus", "flac", "wav"]
AUDIO_QUALITIES = ["best", "320", "256", "192", "128", "96"]
BROWSERS = ["", "firefox", "chrome", "edge", "brave", "opera", "vivaldi"]
SPONSOR_CATEGORIES = "sponsor,selfpromo,interaction"
PROGRESS_INTERVAL = 0.25  # seconds between progress reports per download

SINGLE_TEMPLATE = "%(title)s.%(ext)s"
PLAYLIST_TEMPLATE = "%(playlist_title)s/%(playlist_index)s - %(title)s.%(ext)s"

# Formats whose files can carry cover art
THUMBNAIL_AUDIO = {"original", "mp3", "m4a", "opus", "flac"}

# yt-dlp's post-processor names, in words
STEPS = {"Merger": "Merging video and sound", "ExtractAudio": "Converting audio",
         "ThumbnailsConvertor": "Preparing cover", "EmbedThumbnail": "Adding cover",
         "Metadata": "Adding tags", "FFmpegMetadata": "Adding tags",
         "SponsorBlock": "Finding sponsors", "ModifyChapters": "Cutting sponsors",
         "EmbedSubtitle": "Adding subtitles", "MoveFiles": "Finishing"}

# Recognizable failures: what to tell the user, and whether trying again could help
HINTS = (
    ("not a bot", "YouTube wants a login: set Login from in Options, then Resume", False),
    ("confirm your age", "Age-restricted: set Login from in Options, then Resume", False),
    ("members-only", "Members only: set Login from in Options, then Resume", False),
    ("private video", "Private video: needs a login that can see it", False),
    ("video unavailable", "This video isn't available", False),
    ("not available in your country", "Blocked in your country: try a Proxy in Options", False),
    ("unable to download webpage", "Couldn't reach the site: check the connection or Proxy",
     True),
    ("unsupported url", "Downers can't download from this link", False),
    ("http error 403", "The site refused the download: try Update yt-dlp in Options", True),
)


def hint(message: str) -> str | None:
    lowered = message.lower()
    return next((text for needle, text, _ in HINTS if needle in lowered), None)


def retryable(message: str) -> bool:
    """Could trying again help? Not for a login wall or a video that's gone; yes for
    anything unrecognized, which is usually the connection."""
    lowered = message.lower()
    return next((retry for needle, _, retry in HINTS if needle in lowered), True)


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
    cookies_file: str = ""              # or an exported cookies.txt, which wins
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

def _program(name: str) -> str | None:
    """A program beside Downers.exe (or the project, from source), else on PATH."""
    beside = APP_DIR / f"{name}.exe"
    return str(beside) if beside.exists() else shutil.which(name)


def ffmpeg_path() -> str | None:
    return _program("ffmpeg")


def ffprobe_path() -> str | None:
    """yt-dlp looks for ffprobe in ffmpeg's folder; this checks it's there."""
    ffmpeg = ffmpeg_path()
    probe = ffmpeg and Path(ffmpeg).with_name("ffprobe.exe")
    return str(probe) if probe and probe.exists() else None


def deno_path() -> str | None:
    found = _program("deno")
    if found:
        return found
    try:
        import deno  # the pip package, when running from source
        return deno.find_deno_bin()
    except Exception:
        return None


def build_argv(s: Settings, archive_file: Path | None = None,
               fresh: bool = False) -> list[str]:
    """The yt-dlp command line (without the URL) for these settings. fresh downloads
    everything again, replacing files already there."""
    argv = ["--ignore-config", "--ignore-errors", "--no-write-playlist-metafiles",
            "--concurrent-fragments", "4", "-P", s.output_dir, "-o", SINGLE_TEMPLATE]
    if fresh:
        argv.append("--force-overwrites")

    if ffmpeg := ffmpeg_path():
        # The folder, so yt-dlp finds ffprobe beside ffmpeg
        argv += ["--ffmpeg-location", str(Path(ffmpeg).parent)]
    if deno := deno_path():
        argv += ["--js-runtimes", f"deno:{deno}"]

    sort = []
    if s.mode == "video":
        if s.video_quality != "best":
            sort.append(f"res:{s.video_quality}")
        if s.compatible:
            sort += ["vcodec:h264", "acodec:m4a"]
        # Audio quality is an audio-only setting: a video always gets the best sound
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
    if archive_file:
        argv += ["--download-archive", str(archive_file)]
    if s.cookies_file.strip():
        argv += ["--cookies", s.cookies_file.strip()]
    elif s.cookies:
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
    folder: str = ""            # where its files are going, once known
    last_error: str = ""
    retries: int = 0            # automatic retries used since it last succeeded
    retry_at: float = 0.0       # when a "Waiting" job goes back in the queue
    fresh: bool = False         # "Download again": replace what's already there
    id: int = field(default_factory=lambda: next(_ids))
    stop: threading.Event = field(default_factory=threading.Event, repr=False)
    key: str = field(default_factory=lambda: uuid.uuid4().hex)  # names its resume file
    # .part files of downloads in progress, so Remove can delete them. Each leaves
    # the set as it finishes, so a long channel doesn't grow it.
    partials: set[str] = field(default_factory=set, repr=False)

    @property
    def active(self) -> bool:
        return self.status in ("Starting", "Downloading", "Processing")

    def to_dict(self) -> dict:
        return {"url": self.url, "title": self.title, "status": self.status,
                "settings": self.settings.to_dict(), "partials": sorted(self.partials),
                "key": self.key}

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        status = data.get("status", "Queued")
        if status == "Waiting":
            status = "Queued"   # was waiting to retry: retry now
        elif status not in ("Done", "Error", "Paused", "Queued"):
            status = "Paused"   # was running when the app closed
        return cls(url=data["url"], title=data.get("title", ""), status=status,
                   settings=Settings.from_dict(data.get("settings", {})),
                   partials=set(data.get("partials", [])),
                   **({"key": data["key"]} if data.get("key") else {}))

    def discard_partials(self) -> int:
        """Delete this job's unfinished files. Only names yt-dlp gives partial
        files, so a finished download can never be hit."""
        count = 0
        for part in self.partials:
            path = Path(part)
            if not path.name.endswith(".part"):
                continue
            final = path.with_name(path.name[:-len(".part")])
            for f in [path, final.with_name(final.name + ".ytdl"),
                      *path.parent.glob(glob.escape(path.name) + "-Frag*")]:
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
        self.partials.clear()
        return count


def describe(d: dict, s: Settings) -> str:
    """A progress report in words: "42% · 4.1 MB/s · 21s left"."""
    total = d.get("total_bytes") or d.get("total_bytes_estimate")
    parts = [f"{d['downloaded_bytes'] / total * 100:.0f}%" if total else "Downloading"]
    if d.get("info_dict", {}).get("vcodec") == "none" and s.mode == "video":
        parts[0] += " (sound)"  # the second of a video's two streams
    if speed := d.get("speed"):
        parts.append(f"{speed / 1e6:.1f} MB/s" if speed >= 1e6 else f"{speed / 1e3:.0f} KB/s")
    if (eta := d.get("eta")) is not None:
        m, sec = divmod(int(eta), 60)
        h, m = divmod(m, 60)
        parts.append(f"{h}h {m:02d}m left" if h else f"{m}m {sec:02d}s left" if m
                     else f"{sec}s left")
    return " · ".join(parts)


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
        self.job.last_error = msg
        self.emit(self.job, log=msg, level="error")


def run(job: Job, emit: Callable, archive_file: Path | None = None) -> None:
    """Download one job on the calling thread, reporting through emit(job, **changes).

    Returns when the job is finished, failed, or job.stop was set (paused).
    """
    s = job.settings
    Path(s.output_dir).mkdir(parents=True, exist_ok=True)
    opts = yt_dlp.parse_options(build_argv(s, archive_file, job.fresh)).ydl_opts
    job.errors = 0

    def item_label(info: dict) -> str:
        i, n = info.get("playlist_index"), info.get("n_entries") or info.get("playlist_count")
        return f"{i} of {n} · " if i and n else ""

    last_report = [0.0]

    def progress(d):
        if job.stop.is_set():
            raise DownloadCancelled("Paused")
        info = d.get("info_dict", {})
        if d["status"] == "downloading" and d.get("tmpfilename"):
            job.partials.add(d["tmpfilename"])
        elif d["status"] != "downloading":
            job.partials.discard(d.get("tmpfilename") or f"{d.get('filename')}.part")
        if d["status"] == "downloading":
            # yt-dlp calls this for every chunk, hundreds of times a second when fast
            now = time.monotonic()
            if now - last_report[0] < PROGRESS_INTERVAL:
                return
            last_report[0] = now
            emit(job, status="Downloading", progress=item_label(info) + describe(d, s))
        elif d["status"] == "finished":
            if d.get("filename"):
                emit(job, folder=str(Path(d["filename"]).parent))
            emit(job, status="Processing", progress=f"{item_label(info)}Processing")

    def postprocess(d):
        if job.stop.is_set():
            raise DownloadCancelled("Paused")
        if d["status"] == "started":
            step = STEPS.get(d["postprocessor"], "Processing")
            emit(job, status="Processing", progress=f"{item_label(d.get('info_dict', {}))}{step}")

    opts.update(progress_hooks=[progress], postprocessor_hooks=[postprocess],
                logger=_Logger(job, emit), noprogress=True)

    job.last_error = ""
    emit(job, status="Starting", progress="Looking up link…")
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=False, process=False)
            if job.stop.is_set():
                raise DownloadCancelled("Paused")
            if info is None:
                emit(job, status="Error",
                     progress=hint(job.last_error) or "Couldn't read this link (see Log)")
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
        emit(job, status="Paused", progress="Paused")
        return
    except Exception as e:  # anything yt-dlp lets escape
        if job.stop.is_set():
            emit(job, status="Paused", progress="Paused")
        else:
            job.errors += 1
            emit(job, status="Error", level="error", log=f"Failed: {e}",
                 progress=hint(str(e)) or str(e).splitlines()[0][:120])
        return
    finally:
        # Interrupting a download that comes in pieces (HLS, DASH) leaves yt-dlp's
        # half-written file open inside a reference cycle, and resuming then fails
        # with "file in use". Collecting the cycle closes it.
        gc.collect()

    if job.stop.is_set():
        emit(job, status="Paused", progress="Paused")
    elif job.errors:
        problems = "1 problem" if job.errors == 1 else f"{job.errors} problems"
        emit(job, status="Done",
             progress=f"Done · {problems}: {hint(job.last_error) or 'see Log'}")
    else:
        emit(job, status="Done", progress="Done")
