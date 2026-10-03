"""The window: layout, queue handling, remembering. Needs a desktop session."""

import tkinter

import pytest

from downers import app as A


def new_app():
    """A.App(), retried if Tk fails to start. Making and destroying many Tk windows
    in one process occasionally fails to read Tk's own files ("Can't find a usable
    tk.tcl"); Downers makes one window, so only the tests meet this."""
    for attempt in range(3):
        try:
            return A.App()
        except tkinter.TclError as e:
            if "usable tk.tcl" not in str(e) or attempt == 2:
                raise


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
    w = new_app()
    w.geometry("1000x600+150+120")
    w.update()
    w._on_close()

    w = new_app()
    w.update()
    try:
        assert w.geometry() == "1000x600+150+120"
    finally:
        w.destroy()


def test_maximized_comes_back_maximized_with_the_old_size_behind_it(data_dir):
    w = new_app()
    w.geometry("900x550+200+150")
    w.update()
    w.state("zoomed")
    w.update()
    w._on_close()

    w = new_app()
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
    w = new_app()
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
    assert abs(main_cx - pop_cx) < 20
    # Centered top to bottom too, unless that would run off a small screen, in which
    # case it's moved up just enough to fit
    h = pop.winfo_height()
    wanted_top = app.winfo_rooty() + app.winfo_height() // 2 - h // 2
    fitted_top = max(0, min(wanted_top, pop.winfo_vrootheight() - h - 40))
    assert abs(pop.winfo_rooty() - fitted_top) < 40


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
    w = new_app()
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


@pytest.fixture
def fake_runs(data_dir, monkeypatch):
    """Downloads that end however the test says, recording what each was asked."""
    runs, outcomes = [], []

    def run(job, emit, archive=None):
        runs.append({"fresh": job.fresh, "archive": archive})
        status, error = outcomes.pop(0) if outcomes else ("Done", "")
        job.errors, job.last_error = (1, error) if error else (0, "")
        emit(job, status=status)
    monkeypatch.setattr(A.engine, "run", run)
    w = new_app()
    yield w, runs, outcomes
    w.destroy()


def finish(w):
    for t in list(w.threads.values()):
        t.join(5)
    w._pump()


def test_a_dropped_connection_is_retried_then_gives_up(fake_runs):
    w, runs, outcomes = fake_runs
    outcomes += [("Error", "Unable to download webpage")] * 3
    w._add_from_text("https://youtu.be/a")
    job = next(iter(w.jobs.values()))
    for attempt in range(A.RETRIES):
        finish(w)
        assert job.status == "Waiting" and "Retrying in" in job.progress
        job.retry_at = 0          # don't wait the minute
        w._pump()
    finish(w)
    assert job.status == "Error" and len(runs) == A.RETRIES + 1


def test_a_login_wall_is_not_retried(fake_runs):
    w, runs, outcomes = fake_runs
    outcomes.append(("Error", "Sign in to confirm you're not a bot"))
    w._add_from_text("https://youtu.be/a")
    finish(w)
    assert next(iter(w.jobs.values())).status == "Error" and len(runs) == 1


def test_auto_retry_can_be_turned_off(fake_runs):
    w, runs, outcomes = fake_runs
    w.auto_retry.set(False)
    outcomes.append(("Error", "Unable to download webpage"))
    w._add_from_text("https://youtu.be/a")
    finish(w)
    assert next(iter(w.jobs.values())).status == "Error"


def test_download_again_runs_fresh_and_ignores_the_remembered_list(fake_runs):
    w, runs, _ = fake_runs
    w.v["archive"].set(True)
    w._add_from_text("https://youtu.be/a")
    finish(w)
    job = next(iter(w.jobs.values()))
    w.tree.selection_set(str(job.id))
    w._for_selected(w._again)
    finish(w)
    assert runs[0] == {"fresh": False, "archive": A.ARCHIVE_FILE}
    assert runs[1]["fresh"] is True and runs[1]["archive"] != A.ARCHIVE_FILE
    assert job.status == "Done" and not job.fresh


def test_closing_mid_download_carries_on_next_time(data_dir, monkeypatch):
    import threading
    release = threading.Event()
    monkeypatch.setattr(A.engine, "run", lambda job, emit, archive=None: (
        emit(job, status="Downloading"), release.wait(0.2)))
    w = new_app()
    w._add_from_text("https://youtu.be/a")
    w._pump()
    # Closing with downloads running asks first; say yes
    monkeypatch.setattr(A.messagebox, "askyesno", lambda *a, **k: True)
    w._on_close()
    saved = A.json.loads(A.QUEUE_FILE.read_text())
    assert [j["status"] for j in saved] == ["Queued"]


def test_weekly_update_check_offers_a_newer_yt_dlp(fake_runs, monkeypatch):
    w, _, _ = fake_runs
    asked = []
    monkeypatch.setattr(A.updater, "latest_version", lambda proxy="": "2999.1.1")
    monkeypatch.setattr(A.messagebox, "askyesno", lambda *a, **k: asked.append(a) or False)
    w.last_update_check = 0
    w._maybe_check_for_update()
    for _ in range(50):
        w._pump()
        if asked:
            break
        A.time.sleep(0.05)
    assert asked and "2999.1.1" in asked[0][1]
    assert w.last_update_check > 0
    asked.clear()
    w._maybe_check_for_update()  # within the week: no network, no question
    A.time.sleep(0.2)
    w._pump()
    assert not asked
