# Downers

Download video and audio from YouTube and other sites. Windows only.

Uses [yt-dlp](https://github.com/yt-dlp/yt-dlp).

## Install

1. Download `Downers.zip` from the [latest release](https://github.com/phillram/downers/releases/latest).
2. Unzip it into its own folder.
3. Run `Downers.exe`.

Keep the four files together. Downers needs all of them.

## Download something

1. Copy a link to a video, playlist or channel.
2. Press Ctrl+V in Downers.
3. Pick **Video** or **Audio only**, and the quality.

Files go to `Downloads\Downers`. A playlist gets its own folder.

Hover over any button for help.

## Buttons

- **Pause / Pause all**: stop the selected downloads, or all of them.
- **Resume / Resume all**: carry on where they stopped. Also retries failed ones.
- **Download again**: download the selected rows again from scratch.
- **Remove**: take the selected rows off the list. Downloaded files stay.
- **Clear done**: take finished rows off the list.
- **Options**: login, proxy, subtitles, speed limit.
- **Log**: details when something goes wrong.

Right-click a row for more. Double-click a row to open its folder.

## Checkboxes

- **Cover art**: put the thumbnail inside the file.
- **Tags & chapters**: save the title, channel, date and chapters in the file.
- **Cut sponsors**: remove sponsor segments.
- **Whole playlist**: when a video link is part of a playlist, get the whole playlist.

## If YouTube asks you to sign in

1. Open **Options**.
2. Set **Login from** to your browser. Firefox works best.
3. Close that browser.
4. Press **Resume**.

## If downloads stop working

1. Open **Options**.
2. Press **Update yt-dlp**.

Downers also checks for this once a week.

## Settings

Settings and the download list are saved in `%APPDATA%\Downers`.

## Build from source

Needs Python 3.10 or newer.

```
pip install -r requirements.txt
python tools/fetch_ffmpeg.py
python -m downers
```

To build the exe:

```
pip install pyinstaller
python tools/build_exe.py
```

To run the tests:

```
pip install pytest
python -m pytest
```
