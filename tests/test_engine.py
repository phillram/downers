"""Settings -> yt-dlp options. No network."""

import yt_dlp

from downers.engine import Job, Settings, build_argv, command_line


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


def test_video_quality_caps_resolution_and_audio_bitrate():
    o = opts(video_quality="1080", audio_quality="128", compatible=True, container="mkv")
    assert o["format_sort"] == ["res:1080", "vcodec:h264", "acodec:m4a", "abr:128"]
    assert o["merge_output_format"] == "mkv"


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
