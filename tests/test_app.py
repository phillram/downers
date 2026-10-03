"""The window remembers where it was. Needs a desktop session."""

import pytest

from downers import app as A


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    for name in ("SETTINGS_FILE", "QUEUE_FILE", "ARCHIVE_FILE", "RESUME_DIR"):
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


@pytest.fixture
def app(data_dir, monkeypatch):
    w = A.App()
    monkeypatch.setattr(w, "_schedule", lambda: None)  # queue only, no downloads
    yield w
    w.destroy()


def test_same_link_twice_is_queued_once(app):
    app._add_from_text("https://youtu.be/a https://youtu.be/a")
    app._add_from_text("https://youtu.be/a")
    assert len(app.jobs) == 1
    assert app.status.get() == "Already in the list"


def test_same_link_in_another_format_is_queued(app):
    app._add_from_text("https://youtu.be/a")
    app.v["mode"].set("audio")
    app._add_from_text("https://youtu.be/a")
    assert len(app.jobs) == 2


def test_resume_everything_leaves_finished_links_alone(app):
    app._add_from_text("https://youtu.be/a https://youtu.be/b")
    done, paused = app.jobs.values()
    done.status, paused.status = "Done", "Paused"
    app._for_all(app._resume)
    assert (done.status, paused.status) == ("Done", "Queued")


@pytest.mark.parametrize("show, attr", [("_show_options", "options_window"),
                                        ("_show_log", "log_window")])
def test_popups_open_centered_over_the_window(app, show, attr):
    app.geometry("900x500+300+200")
    app.update()
    getattr(app, show)()
    app.after(100, app.quit)
    app.mainloop()  # let the popup's deferred placement run
    pop = getattr(app, attr)
    pop.update()
    main_cx = app.winfo_rootx() + app.winfo_width() // 2
    pop_cx = pop.winfo_rootx() + pop.winfo_width() // 2
    main_cy = app.winfo_rooty() + app.winfo_height() // 2
    pop_cy = pop.winfo_rooty() + pop.winfo_height() // 2
    assert abs(main_cx - pop_cx) < 20 and abs(main_cy - pop_cy) < 40


def test_log_is_capped_in_memory_and_in_the_open_window(app):
    app._show_log()
    app.update()
    app._log_many([(f"line {i}", "info") for i in range(A.LOG_LIMIT * 2)])
    for i in range(500):
        app._log(f"more {i}")
    assert len(app.log_lines) == A.LOG_LIMIT
    shown = int(app.log_text.index("end-1c").split(".")[0]) - 1
    assert shown == A.LOG_LIMIT
    assert app.log_text.get("end-2l", "end-1c").strip() == "more 499"


def test_a_flood_of_updates_stays_small(app):
    app._add_from_text("https://youtu.be/a")
    job = next(iter(app.jobs.values()))
    for i in range(100_000):
        app._emit(job, status="Downloading", progress=f"{i}%")
        app._emit(job, log=f"line {i}")
    assert len(app.pending) == 1 and len(app.pending_log) == A.LOG_LIMIT
    app._pump()
    assert job.progress == "99999%" and not app.pending


def test_pause_all_and_resume_all(app):
    app._add_from_text("https://youtu.be/a https://youtu.be/b")
    a, b = app.jobs.values()
    app._for_all(app._pause)
    assert (a.status, b.status) == ("Paused", "Paused")
    app._for_all(app._resume)
    assert (a.status, b.status) == ("Queued", "Queued")


def test_pause_with_nothing_selected_does_nothing(app):
    app._add_from_text("https://youtu.be/a")
    job = next(iter(app.jobs.values()))
    app.tree.selection_set(())
    app._for_selected(app._pause)
    assert job.status == "Queued"
    assert app.status.get() == "Select a row first"


def test_login_and_proxy_set_later_apply_on_resume(data_dir, monkeypatch):
    """Each link keeps its format, but connection options are read as it starts,
    so fixing a "sign in" error in Options works on Resume."""
    started = []
    monkeypatch.setattr(A.engine, "run", lambda job, *a: started.append(job.settings))
    w = A.App()
    try:
        w._add_from_text("https://youtu.be/a")
        job = next(iter(w.jobs.values()))
        w.threads[job.id].join(5)
        w._pump()                            # it "finished"; now it failed
        job.status = "Error"
        w.v["cookies"].set("Firefox")
        w.v["proxy"].set("socks5://127.0.0.1:1080")
        w.v["mode"].set("audio")             # a format change must not touch it
        w._for_all(w._resume)
        w.threads[job.id].join(5)
        retried = started[-1]
        assert (retried.cookies, retried.proxy) == ("firefox", "socks5://127.0.0.1:1080")
        assert retried.mode == "video"
    finally:
        w.destroy()


def test_items_apply_to_the_next_link_only(app):
    app.v["items"].set("1-20")
    app._add_from_text("https://youtube.com/@a")
    app._add_from_text("https://youtube.com/@b")
    first, second = app.jobs.values()
    assert (first.settings.items, second.settings.items) == ("1-20", "")


def test_audio_quality_is_locked_in_video_mode(app):
    app.v["mode"].set("video")
    assert str(app.c_aq.cget("state")) == "disabled"
    app.v["mode"].set("audio")
    assert str(app.c_aq.cget("state")) == "readonly"


def test_ctrl_v_outside_a_text_box_adds_the_clipboard(app):
    app.clipboard_clear()
    app.clipboard_append("look https://youtu.be/a and https://youtu.be/b")
    app.tree.focus_set()
    app.event_generate("<Control-v>", when="now")
    app._ctrl_v(type("E", (), {"widget": app.tree})())
    assert len(app.jobs) == 2
