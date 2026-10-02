"""The window remembers where it was. Needs a desktop session."""

import pytest

from downers import app as A


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    for name in ("SETTINGS_FILE", "QUEUE_FILE", "ARCHIVE_FILE"):
        monkeypatch.setattr(A, name, tmp_path / getattr(A, name).name)
    monkeypatch.setattr(A, "DATA_DIR", tmp_path)
    return tmp_path


def test_on_screen():
    assert A.on_screen("800x500+100+100")
    assert not A.on_screen("800x500+99999+100")
    assert not A.on_screen("800x500+100+-99999")
    assert not A.on_screen("")
    assert not A.on_screen("garbage")


def test_window_size_and_position_are_restored(data_dir):
    w = A.App()
    w.geometry("1000x600+150+120")
    w.update()
    w._on_close()

    w = A.App()
    w.update()
    try:
        assert w.geometry() == "1000x600+150+120"
    finally:
        w.destroy()


def test_maximized_comes_back_maximized_with_the_old_size_behind_it(data_dir):
    w = A.App()
    w.geometry("900x550+200+150")
    w.update()
    w.state("zoomed")
    w.update()
    w._on_close()

    w = A.App()
    w.update()
    try:
        assert w.state() == "zoomed"
        w.state("normal")
        w.update()
        assert w.geometry() == "900x550+200+150"
    finally:
        w.destroy()
