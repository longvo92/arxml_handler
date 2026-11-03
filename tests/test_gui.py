import sys
import time
import tkinter as tk

import pytest
from conftest import element

import gui


@pytest.fixture(scope="module")
def tk_root():
    # Tk applications use one interpreter; avoid repeated Tcl teardown during the suite.
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        if sys.platform == "win32":
            raise
        pytest.skip(f"Tk display unavailable: {exc}")
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def app(monkeypatch, tk_root):
    messages = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args: messages.append(args))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args: messages.append(args))
    root = tk_root
    application = gui.Application(root)
    yield application, messages
    for callback in root.tk.call("after", "info"):
        root.after_cancel(callback)
    application.destroy()


def settle(app):
    deadline = time.monotonic() + 10
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    assert not app.busy


def test_gui_scan_apply_and_stale_input(inputs, app):
    master, source, output = inputs(source_elements=element())
    application, messages = app
    assert str(application.apply_button["state"]) == "disabled"
    for name, path in [("Master", master), ("Source", source), ("Output", output)]:
        application.paths[name].set(str(path))
    application.scan_button.invoke()
    settle(application)
    assert application.plan.valid
    assert str(application.apply_button["state"]) == "normal"
    assert len(application.tree.get_children()) == 1
    assert not output.exists()
    application.apply_button.invoke()
    settle(application)
    assert (output / "patch_report.json").exists()
    assert messages[-1][0] == "Patch complete"
    application.paths["Source"].set(str(source / "changed"))
    assert str(application.apply_button["state"]) == "disabled"


def test_gui_blocks_invalid_preflight_and_can_save_report(inputs, app, monkeypatch):
    master, source, output = inputs(source_elements=element(dtype="/Pkg/Missing"))
    application, _ = app
    application.paths["Master"].set(str(master))
    application.paths["Source"].set(str(source))
    application.start_scan()
    settle(application)
    assert not application.plan.valid
    assert str(application.apply_button["state"]) == "disabled"
    report = output.parent / "report"
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kw: str(report))
    application.report_button.invoke()
    settle(application)
    assert (report / "patch_report.json").exists()
    assert not output.exists()
