"""Draw Downers' icon: a down arrow onto a tray, in the app's accent blue.

Run this only to change the icon; the result is committed.

    python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "downers.ico"
SIZES = (256, 128, 64, 48, 32, 16)
N = 1024  # draw big, downscale smooth

BG = (43, 45, 49, 255)
ACCENT = (88, 101, 242, 255)
LIGHT = (230, 230, 230, 255)


def main() -> int:
    image = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((32, 32, N - 32, N - 32), radius=200, fill=BG)
    # Arrow: shaft and head
    d.rectangle((N * 0.42, N * 0.16, N * 0.58, N * 0.50), fill=ACCENT)
    d.polygon([(N * 0.24, N * 0.46), (N * 0.76, N * 0.46), (N * 0.50, N * 0.72)], fill=ACCENT)
    # Tray
    d.rounded_rectangle((N * 0.20, N * 0.76, N * 0.80, N * 0.84), radius=N * 0.04, fill=LIGHT)
    OUT.parent.mkdir(exist_ok=True)
    image.resize((256, 256), Image.LANCZOS).save(OUT, sizes=[(s, s) for s in SIZES])
    print(f"Wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
