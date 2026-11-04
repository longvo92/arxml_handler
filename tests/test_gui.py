import sys
import time
import tkinter as tk

import pytest
from conftest import element

import gui
from diff_engine import scan


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
    existing = element("Old")
    master, source, output = inputs(master_elements=existing, source_elements=existing + element())
    application, messages = app
    assert str(application.apply_button["state"]) == "disabled"
    for name, path in [("Master", master), ("Source", source)]:
        application.paths[name].set(str(path))
    application.scan_button.invoke()
    settle(application)
    assert application.plan.valid
    assert str(application.apply_button["state"]) == "normal"
    (row,) = application.tree.get_children()
    assert application.tree.set(row, "Status") == "ADD"
    assert "Exists: 1" in application.summary.get()
    application.tree.selection_set(row)
    application.show_detail()
    assert "Dependencies: /Pkg/Value" in application.detail.get("1.0", "end")
    assert not output.exists()
    application.apply_button.invoke()
    settle(application)
    assert "Output" not in application.paths
    assert scan(master, source).summary["ADD"] == 0
    assert str(application.apply_button["state"]) == "disabled"
    assert list((master.parent / ".arxml-patcher-backups").glob("*/patch_report.json"))
    assert messages[-1][0] == "Patch complete"
    application.scan_button.invoke()
    settle(application)
    assert not application.tree.get_children()
    assert "Exists: 2" in application.summary.get()
    application.paths["Source"].set(str(source / "changed"))
    assert str(application.apply_button["state"]) == "disabled"


def test_gui_blocks_invalid_preflight_and_can_save_report(inputs, app, monkeypatch):
    master, source, output = inputs(
        source_elements=element("Blocked", dtype="/Pkg/Missing") + element()
    )
    application, _ = app
    application.paths["Master"].set(str(master))
    application.paths["Source"].set(str(source))
    application.start_scan()
    settle(application)
    assert not application.plan.valid
    assert str(application.apply_button["state"]) == "disabled"
    rows = application.tree.get_children()
    assert [application.tree.set(row, "Status") for row in rows] == ["UNSUPPORTED", "ADD"]
    assert "Missing dependency in Master" in application.tree.set(rows[0], "Reason")
    report = output.parent / "report"
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kw: str(report))
    application.report_button.invoke()
    settle(application)
    assert (report / "patch_report.json").exists()
    assert not output.exists()


def test_gui_prevents_normal_close_during_write(app):
    application, _ = app
    application.busy = True
    application.close()
    assert application.root.winfo_exists()
    assert "wait" in application.status.get()
    application.busy = False
