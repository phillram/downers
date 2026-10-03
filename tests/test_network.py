"""Real downloads. Skipped unless DOWNERS_NETWORK_TESTS=1, as they need the internet."""

import os
import threading
import time

import pytest

from downers import engine

pytestmark = pytest.mark.skipif(not os.environ.get("DOWNERS_NETWORK_TESTS"),
                                reason="set DOWNERS_NETWORK_TESTS=1 to run")

HLS = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"  # a public test stream


def test_pausing_a_stream_in_pieces_can_be_resumed(tmp_path):
    """Interrupting a fragmented download left its file open, so resume failed."""
    job = engine.Job(HLS, engine.Settings(output_dir=str(tmp_path), rate_limit="1M",
                                          thumbnail=False))
    state = {}

    def emit(_, **changes):
        state.update({k: v for k, v in changes.items() if k == "status"})

    def pause_soon():
        while state.get("status") != "Downloading":
            time.sleep(0.1)
        time.sleep(3)
        job.stop.set()

    threading.Thread(target=pause_soon, daemon=True).start()
    engine.run(job, emit)
    assert state["status"] == "Paused" and job.partials

    job.stop.clear()
    job.settings.rate_limit = ""
    engine.run(job, emit)
    assert state["status"] == "Done"
    assert [f.name for f in tmp_path.iterdir()] == ["x36xhzz.mp4"]


def test_rerunning_a_playlist_skips_finished_items(tmp_path):
    """Audio in its original format came down again on every resume, because the
    file on disk isn't the name yt-dlp checks. The per-link archive fixes that."""
    settings = engine.Settings(output_dir=str(tmp_path / "out"), mode="audio", items="1-2",
                               thumbnail=False)
    archive = tmp_path / "resume.txt"
    for attempt in (1, 2):
        logs = []
        job = engine.Job("https://www.youtube.com/@NASA/shorts", settings)
        engine.run(job, lambda _, **c: logs.append(c.get("log", "")), archive)
        downloads = [line for line in logs if line.startswith("[download] Destination")]
        assert len(downloads) == (2 if attempt == 1 else 0)
