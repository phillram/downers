"""Put ffmpeg.exe and ffprobe.exe in the project folder.

    python tools/fetch_ffmpeg.py            # only if they're missing
    python tools/fetch_ffmpeg.py --force    # replace them with the current build

yt-dlp needs both: ffmpeg to merge, convert and embed, and ffprobe to embed
thumbnails in MKV, cut SponsorBlock segments and read back what it wrote.

The build is Gyan's "essentials" ffmpeg, from its GitHub releases: the smallest
standalone build that has everything Downers uses. The download is checked
against the SHA-256 GitHub publishes for it before anything is unpacked.
build_exe.py runs this; run it yourself once when working from source.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRAMS = ("ffmpeg.exe", "ffprobe.exe")

RELEASE = "https://api.github.com/repos/GyanD/codexffmpeg/releases/latest"


def fetch(url: str, dest: Path | None = None) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Downers build"})
    with urllib.request.urlopen(request, timeout=60) as response:
        if dest is None:
            return response.read()
        with dest.open("wb") as f:
            while chunk := response.read(1 << 20):
                f.write(chunk)
    return None


def main(argv: list[str]) -> int:
    if "--force" not in argv and all((ROOT / p).exists() for p in PROGRAMS):
        print("ffmpeg.exe and ffprobe.exe are already here.")
        return 0

    release = json.loads(fetch(RELEASE))
    asset = next(a for a in release["assets"] if a["name"].endswith("-essentials_build.zip"))
    expected = asset["digest"].removeprefix("sha256:").lower()
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "ffmpeg.zip"
        print(f"Downloading {asset['name']} ({asset['size'] / 1e6:.0f} MB)")
        fetch(asset["browser_download_url"], archive)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != expected:
            print(f"Checksum mismatch: got {digest}, expected {expected}. Nothing changed.")
            return 1
        with zipfile.ZipFile(archive) as z:
            for program in PROGRAMS:
                member = next(n for n in z.namelist() if n.endswith(f"/bin/{program}"))
                (ROOT / program).write_bytes(z.read(member))
                print(f"Wrote {program}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
