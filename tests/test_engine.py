"""Settings -> yt-dlp options. No network."""

import yt_dlp

from downers.engine import Job, Settings, build_argv, command_line, describe, hint


def opts(**kw):
    return yt_dlp.parse_options(build_argv(Settings(output_dir="out", **kw))).ydl_opts


def pp_keys(o):
    return [p["key"] for p in o["postprocessors"]]


def test_video_defaults_to_best_with_thumbnail():
    o = opts()
    assert o["format"] == "bv*+ba/b"
    assert not o.get("format_sort")
    assert o["merge_output_format"] == "mp4"
    assert o["writethumbnail"]
    assert {"EmbedThumbnail", "FFmpegThumbnailsConvertor", "FFmpegMetadata"} <= set(pp_keys(o))
    assert o["noplaylist"] is True


def test_video_quality_caps_resolution_but_never_the_sound():
    # Audio quality left at 128k from an earlier MP3 must not cap a video's soundtrack
    o = opts(video_quality="1080", audio_quality="128", compatible=True, container="mkv")
    assert o["format_sort"] == ["res:1080", "vcodec:h264", "acodec:m4a"]
    assert o["merge_output_format"] == "mkv"


def test_progress_and_errors_read_as_words():
    d = {"downloaded_bytes": 42, "total_bytes": 100, "speed": 4_100_000, "eta": 81,
         "info_dict": {}}
    assert describe(d, Settings()) == "42% · 4.1 MB/s · 1m 21s left"
    assert describe({"downloaded_bytes": 5, "eta": 9, "info_dict": {"vcodec": "none"}},
                    Settings()) == "Downloading (sound) · 9s left"
    assert "Login from" in hint("ERROR: Sign in to confirm you're not a bot")
    assert hint("something nobody has seen") is None


def test_audio_best_keeps_original_stream():
    o = opts(mode="audio")
    extract = next(p for p in o["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert o["format"] == "ba/b"
    assert extract["preferredcodec"] == "best"
    assert extract["preferredquality"] == "0"


def test_audio_mp3_at_chosen_bitrate():
    o = opts(mode="audio", audio_format="mp3", audio_quality="192")
    extract = next(p for p in o["postprocessors"] if p["key"] == "FFmpegExtractAudio")
    assert extract["preferredcodec"] == "mp3"
    assert extract["preferredquality"] == "192"  # kbps
    assert o["format_sort"] == ["abr:192"]


def test_wav_skips_thumbnail_it_cannot_hold():
    assert "EmbedThumbnail" not in pp_keys(opts(mode="audio", audio_format="wav"))


def test_extras():
    o = opts(sponsorblock=True, whole_playlist=True, items="1-5", subtitles="en",
             proxy="socks5://127.0.0.1:1080", rate_limit="2M", cookies="firefox")
    assert "SponsorBlock" in pp_keys(o)
    assert "FFmpegEmbedSubtitle" in pp_keys(o)
    assert o["noplaylist"] is False
    assert o["playlist_items"] == "1-5"
    assert o["proxy"] == "socks5://127.0.0.1:1080"
    assert o["ratelimit"] == 2 * 1024 * 1024
    assert o["cookiesfrombrowser"][0] == "firefox"


def test_settings_round_trip_ignores_unknown_keys():
    s = Settings(mode="audio", audio_format="flac")
    assert Settings.from_dict(s.to_dict() | {"junk": 1}) == s
    assert s.summary() == "FLAC"
    assert Settings(video_quality="720").summary() == "720p MP4"


def test_running_job_comes_back_paused():
    job = Job.from_dict({"url": "https://x", "status": "Downloading", "settings": {}})
    assert job.status == "Paused"


def test_command_line_is_copyable():
    cmd = command_line(Settings(output_dir="C:/My Videos"), "https://youtu.be/x")
    assert cmd.startswith("yt-dlp ") and cmd.endswith("https://youtu.be/x")
    assert "'C:/My Videos'" in cmd


def test_cookies_file_wins_over_browser():
    o = opts(cookies="firefox", cookies_file="C:/cookies.txt")
    assert o["cookiefile"] == "C:/cookies.txt"
    assert not o.get("cookiesfrombrowser")


def test_discard_partials_deletes_only_unfinished_files(tmp_path):
    part = tmp_path / "Video.mp4.part"
    leftovers = [part, tmp_path / "Video.mp4.part-Frag1.part", tmp_path / "Video.mp4.ytdl"]
    finished = [tmp_path / "Video.mp4", tmp_path / "Other.mp4"]
    for f in leftovers + finished:
        f.write_text("x")
    job = Job("https://x", Settings(), partials={str(part), str(tmp_path / "Other.mp4")})
    assert job.discard_partials() == 3
    assert [f.exists() for f in leftovers] == [False] * 3
    assert all(f.exists() for f in finished)
    assert not job.partials


def test_partials_survive_a_restart():
    job = Job("https://x", Settings(), partials={"C:/a.mp4.part"})
    assert Job.from_dict(job.to_dict()).partials == {"C:/a.mp4.part"}
