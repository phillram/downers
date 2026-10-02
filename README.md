# Downers

A small, dark window for downloading video and audio with
[yt-dlp](https://github.com/yt-dlp/yt-dlp). Paste a link to a video, a playlist or a whole
channel, pick the format, and it downloads. You can pause and pick up where you left off.

## Two ways to run it

### The exe (no Python needed)

Run **`Downers.exe`** in this folder. It uses the two programs beside it, so keep the three
together:

| File | Purpose |
|---|---|
| `Downers.exe` | The app, with yt-dlp built in |
| `ffmpeg.exe` | Merging video and audio, converting audio, embedding thumbnails |
| `deno.exe` | Solving YouTube's JavaScript challenges, needed for full-quality formats |

To use it elsewhere, including on another Windows PC, copy those three files into one folder.
For a desktop shortcut, right-click `Downers.exe` → **Send to** → **Desktop**.

To build them, or rebuild after a code change:

```bash
pip install pyinstaller -r requirements.txt
python tools/build_exe.py
```

### From source

Needs Python 3.10 or newer.

```bash
pip install -r requirements.txt
```

That installs yt-dlp, plus copies of ffmpeg and Deno, so nothing has to be installed
system-wide. If `ffmpeg` or `deno` is already on your `PATH`, that copy is used instead.

Double-click **`Downers.pyw`**, or run `python -m downers`. `Downers.pyw` uses the Python named
on its first line (`C:\Coding\.venv\Scripts\pythonw.exe`); edit that line if yours is
elsewhere.

## Using it

1. Paste a link into the box and press **Enter** or **Add**. **Paste** adds whatever links are
   on the clipboard; several at once is fine.
2. Choose **Video** or **Audio only** *before* adding. Each link keeps the settings it was
   added with, so you can queue a 1080p video and an MP3 side by side.
3. Downloads start straight away, one at a time (change this in **Options**).

| Setting | Choices | Default |
|---|---|---|
| Video quality | Best, 2160p, 1440p, 1080p, 720p, 480p, 360p (the best up to that height) | Best |
| Video file | MP4, MKV | MP4 |
| Audio format | Original (YouTube's own stream, no re-encoding), MP3, M4A, Opus, FLAC, WAV | Original |
| Audio quality | Best, 320k, 256k, 192k, 128k, 96k | Best |
| Embed thumbnail | Cover art inside the file (video and audio; not WAV) | On |
| Tags & chapters | Title, uploader, date and description tags, plus chapter markers | On |
| Cut sponsors | Removes sponsor, self-promo and "like and subscribe" segments ([SponsorBlock](https://sponsor.ajay.app)) | Off |
| Full playlist from video links | A link like `watch?v=…&list=…` downloads the whole playlist, not just that video | Off |
| Save to | Any folder | `Downloads\Downers` |

In video mode, audio quality caps the soundtrack's bitrate. In audio mode it sets the
encoding bitrate. FLAC and WAV are lossless, so they have no quality setting.

### Playlists and channels

Playlist and channel links are saved into a folder named after the playlist, with each file
numbered:

```
Downloads\Downers\
  Some Video.mp4
  My Playlist\
    01 - First Song.mp3
    02 - Second Song.mp3
```

A channel link (`youtube.com/@name`) gets a folder for each tab it has, such as
`Name - Videos` and `Name - Shorts`. Use `youtube.com/@name/videos` to get just the videos.
Use that form with **Items** too: on a bare `@name` link, Items counts tabs, not videos.

### Pause and resume

- **Pause** and **Resume** act on the selected rows, or on everything if nothing is selected.
- A paused download keeps its partial file and carries on from the same point.
- In a playlist, finished items are skipped when you resume.
- Closing Downers mid-download asks first, then pauses everything. The queue comes back the
  next time you open it.

Right-click a row for more: **Open folder**, **Copy link**, and **Copy yt-dlp command** (the
equivalent command line, for running by hand). Double-click a row to open its folder. **Delete**
removes it from the queue (files already downloaded are kept).

### Options

| Option | What it does |
|---|---|
| Subtitles | Language codes to embed in videos, e.g. `en` or `en,es` |
| Items | Only some of a playlist or channel, e.g. `1-20` or `1,5,8-10` |
| Login from | Borrow a browser's YouTube login for age-restricted, members-only or private videos. Close that browser first. Firefox works best; Chrome and Edge often lock their cookies on Windows. |
| Proxy | Send traffic through a proxy, e.g. `socks5://127.0.0.1:1080` or `http://host:port` |
| Speed limit | e.g. `2M` for 2 MB/s |
| At once | How many links download at the same time (1–4) |
| Prefer H.264/AAC | Picks formats that old TVs, phones and editors can play. YouTube usually only offers H.264 up to 1080p. |
| Remember finished videos | Records what's been downloaded and skips it next time. Handy for re-running a channel to fetch only new uploads. |
| Update yt-dlp | YouTube changes often. If downloads start failing, press this; it fetches the latest yt-dlp (through your proxy, if one is set) and offers to restart. Works in the exe too, with no rebuild needed. |

Changes apply to links added afterward.

**Log** shows yt-dlp's full output, including the reason for any failure.

## Where things are kept

Settings, the saved queue and the "remember finished videos" list live in
`%APPDATA%\Downers\`. So does any newer yt-dlp fetched by **Update yt-dlp**, in a `yt-dlp`
folder. It's used only while it's newer than the built-in one, and deleting the folder goes
back to the built-in one.
