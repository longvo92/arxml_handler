import json

import pytest
from conftest import access, component, comspec, document, element

import file_transaction
import patch_engine
from arxml_parser import Dataset, PatcherError, semantic
from diff_engine import scan
from patch_engine import apply_patch, save_report


def multi_file_plan(inputs):
    master, source, _ = inputs(
        source_elements=element(),
        master_component=component(),
        source_component=component(
            rcom=comspec(), pcom=comspec("P"), accesses=access() + access("DATA-SEND-POINTS", "P")
        ),
    )
    return master, source, scan(master, source)


def test_tc14_tc15_tc16_tc20_complete_cross_file_patch(inputs):
    master, source, plan = multi_file_plan(inputs)
    untouched = master / "nested" / "unrelated.arxml"
    untouched.parent.mkdir()
    untouched.write_bytes(document("", "Unrelated").replace("\n", "\r\n").encode())
    plan = scan(master, source)
    original = Dataset.load(master)
    source_before = Dataset.load(source).manifest()
    assert [op.record.Category for op in plan.operations] == [
        "DataElement",
        "ComSpec",
        "ComSpec",
        "RunnableAccess",
        "RunnableAccess",
    ]
    result = apply_patch(plan)
    assert result.master_folder == master
    assert set(result.changed_files) == {
        original.objects["/Pkg/If"].file,
        original.objects["/Pkg/Swc"].file,
    }
    for file, doc in original.documents.items():
        if file in result.changed_files:
            assert (result.backup_folder / "originals" / (str(file) + ".bak")).read_bytes() == (
                doc.original
            )
        else:
            assert (master / file).read_bytes() == doc.original
    assert Dataset.load(source).manifest() == source_before
    repeated = scan(master, source)
    assert repeated.valid and repeated.summary["ADD"] == 0
    assert repeated.summary["EXISTS"] == 5
    report = json.loads((result.backup_folder / "patch_report.json").read_text())
    assert report["post_patch_validation"]["second_scan_add"] == 0
    assert report["outcome"] == "patched" and report["write_mode"] == "in_place"
    assert not report["xsd_validated"]
    assert (
        json.loads((result.backup_folder / "transaction.json").read_text())["state"] == "committed"
    )
    assert not list(master.rglob("*.bak"))
    with pytest.raises(PatcherError, match="changed after scan"):
        apply_patch(plan)


def test_tc17_invalid_input_never_writes(inputs):
    master, source, unused = inputs(source_elements=element())
    before = Dataset.load(master).manifest()
    (source / "broken.arxml").write_text("<broken>")
    plan = scan(master, source)
    with pytest.raises(PatcherError, match="Preflight blocked"):
        apply_patch(plan)
    assert Dataset.load(master).manifest() == before
    report_path = unused.parent / "error-report"
    save_report(plan, report_path, "blocked")
    assert not list(report_path.glob("*.arxml"))
    assert json.loads((report_path / "patch_report.json").read_text())["errors"]


def test_tc18_xsd_failure_never_writes_or_creates_backup(inputs, monkeypatch):
    master, source, _ = inputs(source_elements=element())
    before = Dataset.load(master).manifest()

    def fail(*args):
        raise PatcherError("Injected XSD failure")

    monkeypatch.setattr(patch_engine, "validate_xsd", fail)
    with pytest.raises(PatcherError, match="Injected"):
        apply_patch(scan(master, source), "schema.xsd")
    assert Dataset.load(master).manifest() == before
    assert not (master.parent / ".arxml-patcher-backups").exists()


def test_backup_or_report_write_failure_never_changes_master(inputs, monkeypatch):
    master, source, _ = inputs(source_elements=element())
    before = Dataset.load(master).manifest()

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(patch_engine, "_write_reports", fail)
    with pytest.raises(PatcherError, match="Disk full"):
        apply_patch(scan(master, source))
    assert Dataset.load(master).manifest() == before


def test_second_master_write_failure_rolls_back_first(inputs, monkeypatch):
    master, source, plan = multi_file_plan(inputs)
    before = Dataset.load(master).manifest()
    replace = file_transaction.replace_file
    writes = []

    def fail_second(path, data):
        if path.suffix == ".arxml":
            writes.append(path)
            if len(writes) == 2:
                raise OSError("Injected second-file failure")
        replace(path, data)

    monkeypatch.setattr(file_transaction, "replace_file", fail_second)
    with pytest.raises(PatcherError, match="rolled back"):
        apply_patch(plan)
    assert len(writes) == 3
    assert Dataset.load(master).manifest() == before
    journals = list((master.parent / ".arxml-patcher-backups").glob("*/transaction.json"))
    assert json.loads(journals[0].read_text())["state"] == "rolled_back"
    assert not list(master.rglob(".arxml-write-*"))
    monkeypatch.setattr(file_transaction, "replace_file", replace)
    apply_patch(scan(master, source))
    assert scan(master, source).summary["ADD"] == 0


def test_failed_rollback_preserves_backups_and_blocks_next_apply(inputs, monkeypatch):
    master, source, plan = multi_file_plan(inputs)
    replace = file_transaction.replace_file
    count = 0

    def fail(path, data):
        nonlocal count
        if path.suffix == ".arxml":
            count += 1
            if count >= 2:
                raise OSError("File locked")
        replace(path, data)

    monkeypatch.setattr(file_transaction, "replace_file", fail)
    with pytest.raises(PatcherError, match="Recovery required"):
        apply_patch(plan)
    journal = next((master.parent / ".arxml-patcher-backups").glob("*/transaction.json"))
    assert json.loads(journal.read_text())["state"] == "recovery_required"
    assert len(list(journal.parent.rglob("*.bak"))) == 2
    monkeypatch.setattr(file_transaction, "replace_file", replace)
    with pytest.raises(PatcherError, match="Unfinished patch"):
        apply_patch(scan(master, source))


def test_concurrent_patch_lock_blocks_writes(inputs):
    master, source, _ = inputs(source_elements=element())
    root = master.parent / ".arxml-patcher-backups"
    root.mkdir()
    lock = root / (master.name + ".lock")
    lock.write_text("another patch")
    before = Dataset.load(master).manifest()
    with pytest.raises(PatcherError, match="locked"):
        apply_patch(scan(master, source))
    assert Dataset.load(master).manifest() == before
    assert lock.read_text() == "another patch"


@pytest.mark.parametrize("failure", ["report", "journal", "after_replace"])
def test_late_failure_rolls_back_all_written_files(inputs, monkeypatch, failure):
    master, _, plan = multi_file_plan(inputs)
    before = Dataset.load(master).manifest()
    if failure == "report":
        writer = patch_engine._write_reports
        calls = 0

        def fail_report(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("Final report failure")
            return writer(*args)

        monkeypatch.setattr(patch_engine, "_write_reports", fail_report)
    elif failure == "journal":
        writer = file_transaction._json

        def fail_journal(path, data):
            if data["state"] == "committed":
                raise OSError("Final journal failure")
            return writer(path, data)

        monkeypatch.setattr(file_transaction, "_json", fail_journal)
    else:
        replace = file_transaction.replace_file
        failed = False

        def fail_after_replace(path, data):
            nonlocal failed
            replace(path, data)
            if path.suffix == ".arxml" and not failed:
                failed = True
                raise OSError("Interrupted after replacing a file")

        monkeypatch.setattr(file_transaction, "replace_file", fail_after_replace)
    with pytest.raises(PatcherError, match="rolled back"):
        apply_patch(plan)
    assert Dataset.load(master).manifest() == before


def test_rollback_does_not_overwrite_external_edits(inputs, monkeypatch):
    master, _, plan = multi_file_plan(inputs)
    replace = file_transaction.replace_file
    first = None
    external = b"external edit made by another process"

    def fail(path, data):
        nonlocal first
        if path.suffix == ".arxml":
            if first is None:
                first = path
            else:
                first.write_bytes(external)
                raise OSError("Second write failed")
        replace(path, data)

    monkeypatch.setattr(file_transaction, "replace_file", fail)
    with pytest.raises(PatcherError, match="Recovery required"):
        apply_patch(plan)
    assert first.read_bytes() == external
    journal = next((master.parent / ".arxml-patcher-backups").glob("*/transaction.json"))
    assert json.loads(journal.read_text())["state"] == "recovery_required"


@pytest.mark.parametrize("change", ["edit", "new-file", "delete"])
def test_stale_scan_is_rejected(inputs, change):
    master, source, _ = inputs(source_elements=element())
    plan = scan(master, source)
    if change == "edit":
        with (master / "types.arxml").open("a") as f:
            f.write("\n")
    elif change == "new-file":
        (source / "new.arxml").write_text(document("", "NewPkg"))
    else:
        (source / "types.arxml").unlink()
    before = Dataset.load(master).manifest()
    with pytest.raises(PatcherError, match="changed after scan"):
        apply_patch(plan)
    assert Dataset.load(master).manifest() == before


def test_conflict_blocks_other_valid_additions(inputs):
    master, source, _ = inputs(
        master_elements=element("Old"), source_elements=element("Old", "/Pkg/Different") + element()
    )
    before = Dataset.load(master).manifest()
    plan = scan(master, source)
    assert plan.summary["ADD"] == plan.summary["CONFLICT"] == 1
    with pytest.raises(PatcherError):
        apply_patch(plan)
    assert Dataset.load(master).manifest() == before


def test_overlapping_inputs_are_rejected(inputs):
    master, source, _ = inputs(source_elements=element())
    plan = scan(master, source)
    plan.source.folder = master.parent
    with pytest.raises(PatcherError, match="separate"):
        apply_patch(plan)


def test_uuid_and_properties_are_copied_exactly(inputs):
    new = element().replace(
        "<VARIABLE-DATA-PROTOTYPE>",
        '<VARIABLE-DATA-PROTOTYPE UUID="11111111-1111-1111-1111-111111111111">',
    )
    master, source, _ = inputs(source_elements=new)
    apply_patch(scan(master, source))
    assert semantic(Dataset.load(source).objects["/Pkg/If/New"].node) == semantic(
        Dataset.load(master).objects["/Pkg/If/New"].node
    )
