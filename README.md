# Downers

Downers downloads video and audio from YouTube and anywhere else
[yt-dlp](https://github.com/yt-dlp/yt-dlp) supports. Paste a link to a video, a playlist or a
whole channel, pick the format, and it goes.

Windows only. Dark themed.

## Getting it running

Download `Downers.zip` from the
[latest release](https://github.com/phillram/downers/releases/latest) and unzip it. Keep the
four files together:

| File | What it's for |
| --- | --- |
| `Downers.exe` | The app |
| `ffmpeg.exe` | Merging video with audio, converting audio, embedding thumbnails |
| `ffprobe.exe` | Thumbnails in MKV files, cutting sponsors |
| `deno.exe` | Solving YouTube's JavaScript challenges, without which the best formats are missing |

From source, Python 3.10 or newer:

```
pip install -r requirements.txt
python tools/fetch_ffmpeg.py     # ffmpeg.exe and ffprobe.exe, once
python -m downers                # the window
python tools/build_exe.py        # puts Downers.exe and its helpers in this folder
```

## Using it

Paste a link and press Enter. **Paste** adds every link on the clipboard. Each link keeps the
settings it was added with, so a 1080p video and an MP3 can sit in the same queue. A link
already waiting in the same format isn't added twice.

| Setting | Choices | Default |
| --- | --- | --- |
| Video | Best, 2160p, 1440p, 1080p, 720p, 480p, 360p. The best up to that height | Best |
| Video file | MP4, MKV | MP4 |
| Audio only | Original (YouTube's own stream, not re-encoded), MP3, M4A, Opus, FLAC, WAV | Original |
| Audio quality | Best, 320k, 256k, 192k, 128k, 96k | Best |
| Embed thumbnail | Cover art inside the file. Not WAV | On |
| Tags & chapters | Title, uploader, date and chapter markers | On |
| Cut sponsors | Removes sponsor, self-promotion and "like and subscribe" segments, via [SponsorBlock](https://sponsor.ajay.app) | Off |
| Full playlist from video links | A `watch?v=…&list=…` link gets the whole playlist, not just that video | Off |

Files go to `Downloads\Downers` unless you choose another folder. A playlist gets its own
folder with numbered files. A channel gets a folder per tab, such as `Name - Videos` and
`Name - Shorts`; link to `youtube.com/@name/videos` for the videos alone.

**Pause** and **Resume** act on the selected rows; **Pause all** and **Resume all** on the
whole queue. Resuming also retries failed links. A paused download keeps its partial file and continues from
the same byte. Finished playlist items are skipped. Closing Downers mid-download pauses it,
and the queue is back next time. Only one Downers runs at a time; opening it again brings the
open one forward.

Right-click a row to open its folder, copy its link, or copy the equivalent yt-dlp command.
Double-click opens the folder. **Remove** (or Delete) takes a row off the queue. Finished files
stay; for an unfinished download it asks, then deletes the partial files.

### Options

| Option | What it does |
| --- | --- |
| Subtitles | Languages to embed in videos, such as `en` or `en,es` |
| Items | Part of a playlist or channel, such as `1-20` or `1,5,8-10` |
| Login from | Use a browser's YouTube login. Close that browser first |
| Cookies file | Use an exported `cookies.txt` instead |
| Proxy | Such as `socks5://127.0.0.1:1080` or `http://host:port` |
| Speed limit | Such as `2M` for 2 MB/s |
| At once | How many links download at the same time, 1 to 4 |
| Prefer H.264/AAC | For old TVs, phones and editors. YouTube rarely offers H.264 above 1080p |
| Remember finished videos | Skip anything downloaded before. Rerun a channel to fetch only new uploads |
| Update yt-dlp | Fetch the latest yt-dlp and restart |

Options apply to links added after the change. **Log** shows yt-dlp's output, including why
anything failed.

## Do I need a YouTube login?

Not for public videos. A login is needed for age-restricted, members-only and private videos,
and when YouTube answers with "Sign in to confirm you're not a bot". That happens most on VPNs
and shared connections.

For a login, pick your browser under **Login from**. Firefox works best. Chrome and Edge lock
their cookies on Windows, so for those, export a `cookies.txt` with a browser extension and
choose it under **Cookies file**. A cookies file is your logged-in session: keep it private.

## When downloads stop working

YouTube changes something every few weeks and yt-dlp catches up within days. Press
**Options → Update yt-dlp**. Downers fetches the new version from PyPI, through your proxy if
one is set, and offers to restart. It works the same in the exe; no rebuild needed.

## Where things are kept

Everything Downers remembers lives in `%APPDATA%\Downers`: settings, window size and position,
the queue, the list of finished videos, and any yt-dlp fetched by Update. The updated yt-dlp is
only used while it's newer than the built-in one. Delete its `yt-dlp` folder to go back.

## Development

```
pip install pytest
python -m pytest
```

The window tests open real windows, so they need a desktop session. Tests that download for
real are skipped unless `DOWNERS_NETWORK_TESTS=1` is set.

```
downers/
├── downers/
│   ├── __main__.py     entry point
│   ├── app.py          the tkinter window
│   ├── engine.py       settings to yt-dlp options, and running one download
│   ├── updater.py      fetching a newer yt-dlp and loading it ahead of the bundled one
│   └── paths.py        where files live, from source or from the exe
├── tests/
├── tools/
│   ├── build_exe.py    builds Downers.exe and puts its helpers beside it
│   ├── fetch_ffmpeg.py downloads and checks ffmpeg and ffprobe
│   └── make_icon.py    redraws assets/downers.ico
└── assets/
```

`engine.py` has no tkinter in it. Settings become a yt-dlp command line, which yt-dlp's own
parser turns into options, so post-processing is wired exactly as yt-dlp intends.

Pushing a `v*` tag builds the exe on GitHub Actions and publishes `Downers.zip` as a release.
