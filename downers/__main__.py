"""python -m downers"""

from downers import updater

updater.activate()  # before anything imports yt_dlp

from downers.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
