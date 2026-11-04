import json
import os
import sys
import time
import tkinter as tk

import pytest
from conftest import component, comspec, element

import file_transaction
import gui
from arxml_parser import Dataset
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
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args, **kwargs: messages.append(args))
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


def test_gui_scan_apply_and_stale_input(inputs, app, monkeypatch):
    existing = element("Old")
    master, source, output = inputs(master_elements=existing, source_elements=existing + element())
    application, messages = app
    target = master / "interfaces.arxml"
    os.utime(target, ns=(1_650_000_000_123_456_700, 1_650_000_000_123_456_700))
    before = (target.read_bytes(), target.stat().st_mtime_ns)
    assert str(application.apply_button["state"]) == "disabled"
    assert str(application.revert_button["state"]) == "disabled"
    for name, path in [("Master", master), ("Source", source)]:
        application.paths[name].set(str(path))
    application.scan_button.invoke()
    settle(application)
    assert application.plan.valid
    assert not messages
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
    assert not (master.parent / ".arxml-patcher-backups").exists()
    assert str(application.revert_button["state"]) == "normal"
    assert str(application.report_button["state"]) == "normal"
    assert messages[-1][0] == "Patch complete"
    application.scan_button.invoke()
    settle(application)
    assert not application.tree.get_children()
    assert "Exists: 2" in application.summary.get()
    assert messages[-1] == (
        "Scan complete",
        "Preflight checks passed.\n"
        "No supported S/R additions are needed.\n\n"
        "2 equivalent item(s) found in Master.",
    )
    assert str(application.apply_button["state"]) == "disabled"
    application.paths["Source"].set(str(source / "changed"))
    assert str(application.apply_button["state"]) == "disabled"
    assert str(application.revert_button["state"]) == "normal"
    application.paths["Master"].set(str(master.parent / "other"))
    assert str(application.revert_button["state"]) == "disabled"
    application.paths["Master"].set(str(master))
    assert str(application.revert_button["state"]) == "normal"
    application.revert_button.invoke()
    settle(application)
    assert (target.read_bytes(), target.stat().st_mtime_ns) == before
    assert str(application.revert_button["state"]) == "disabled"
    assert messages[-1][0] == "Revert complete"
    report = master.parent / "revert-report"
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kw: str(report))
    application.report_button.invoke()
    settle(application)
    data = json.loads((report / "patch_report.json").read_text(encoding="utf-8"))
    assert data["outcome"] == "reverted"
    assert not data["revert_available"]
    assert data["post_revert_validation"]["original_mtime"]


@pytest.mark.parametrize("addition", ["", element()])
def test_gui_blocks_invalid_preflight_and_can_save_report(inputs, app, monkeypatch, addition):
    master, source, output = inputs(
        source_elements=element("Blocked", dtype="/Pkg/Missing") + addition
    )
    application, messages = app
    application.paths["Master"].set(str(master))
    application.paths["Source"].set(str(source))
    application.start_scan()
    settle(application)
    assert not application.plan.valid
    assert not messages
    assert str(application.apply_button["state"]) == "disabled"
    rows = application.tree.get_children()
    expected = ["UNSUPPORTED", "ADD"] if addition else ["UNSUPPORTED"]
    assert [application.tree.set(row, "Status") for row in rows] == expected
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


def test_gui_uuid_reassignment_preview_matches_apply(inputs, app):
    identifier = "11111111-1111-4111-8111-111111111111"
    old = element("Old").replace(
        "<VARIABLE-DATA-PROTOTYPE>", f'<VARIABLE-DATA-PROTOTYPE UUID="{identifier}">'
    )
    new = element().replace(
        "<VARIABLE-DATA-PROTOTYPE>", f'<VARIABLE-DATA-PROTOTYPE UUID="{identifier}">'
    )
    master, source, _ = inputs(master_elements=old, source_elements=old + new)
    application, _ = app
    application.paths["Master"].set(str(master))
    application.paths["Source"].set(str(source))
    application.scan_button.invoke()
    settle(application)
    assert application.plan.valid
    (change,) = application.plan.uuid_changes
    (row,) = application.tree.get_children()
    assert "reassigned 1 UUID(s)" in application.tree.set(row, "Reason")
    assert str(application.apply_button["state"]) == "normal"
    application.apply_button.invoke()
    settle(application)
    patched = Dataset.load(master)
    assert patched.objects["/Pkg/If/Old"].node.get("UUID") == identifier
    assert patched.objects["/Pkg/If/New"].node.get("UUID") == change.NewUUID


def test_gui_retains_partial_apply_for_recovery(inputs, app, monkeypatch):
    master, source, _ = inputs(
        source_elements=element(),
        master_component=component(),
        source_component=component(rcom=comspec()),
    )
    before = Dataset.load(master).manifest()
    application, messages = app
    application.paths["Master"].set(str(master))
    application.paths["Source"].set(str(source))
    application.scan_button.invoke()
    settle(application)
    replace = file_transaction.replace_file
    writes = 0

    def fail(path, data):
        nonlocal writes
        writes += 1
        if writes >= 2:
            raise OSError("File locked")
        replace(path, data)

    monkeypatch.setattr(file_transaction, "replace_file", fail)
    application.apply_button.invoke()
    settle(application)
    assert application.undo_result.state == "recovery_required"
    assert str(application.revert_button["state"]) == "normal"
    assert str(application.apply_button["state"]) == "disabled"
    assert messages[-1][0] == "Operation failed"
    application.close()
    assert "Recovery is incomplete" in application.status.get()
    monkeypatch.setattr(file_transaction, "replace_file", replace)
    application.revert_button.invoke()
    settle(application)
    assert Dataset.load(master).manifest() == before
    assert messages[-1][0] == "Revert complete"
