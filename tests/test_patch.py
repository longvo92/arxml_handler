import json

import pytest
from conftest import access, component, comspec, document, element

import patch_engine
from arxml_parser import Dataset, PatcherError, semantic
from diff_engine import scan
from patch_engine import apply_patch, save_report


def test_tc14_tc15_tc16_tc20_complete_cross_file_patch(inputs):
    master, source, output = inputs(
        source_elements=element(),
        master_component=component(),
        source_component=component(
            rcom=comspec(), pcom=comspec("P"), accesses=access() + access("DATA-SEND-POINTS", "P")
        ),
    )
    untouched = master / "nested" / "unrelated.arxml"
    untouched.parent.mkdir()
    untouched.write_bytes(document("", "Unrelated").replace("\n", "\r\n").encode())
    before = Dataset.load(master).manifest(), Dataset.load(source).manifest()
    plan = scan(master, source)
    assert plan.valid
    assert [op.record.Category for op in plan.operations] == [
        "DataElement",
        "ComSpec",
        "ComSpec",
        "RunnableAccess",
        "RunnableAccess",
    ]
    apply_patch(plan, output)
    assert (Dataset.load(master).manifest(), Dataset.load(source).manifest()) == before
    assert (output / "nested" / "unrelated.arxml").read_bytes() == untouched.read_bytes()
    assert (output / "types.arxml").read_bytes() == (master / "types.arxml").read_bytes()
    repeated = scan(output, source)
    assert repeated.valid and repeated.summary["ADD"] == 0
    assert repeated.summary["EXISTS"] == 5
    report = json.loads((output / "patch_report.json").read_text())
    assert report["post_patch_validation"]["second_scan_add"] == 0
    assert report["xsd_validated"] is False
    assert set(report["changed_files"]) == {"interfaces.arxml", "component.arxml"}
    # The scan itself remains reusable and no trees in its inputs were mutated.
    assert scan(master, source).summary == plan.summary


def test_tc17_invalid_input_never_writes(inputs):
    master, source, output = inputs(source_elements=element())
    (source / "broken.arxml").write_text("<broken>")
    plan = scan(master, source)
    with pytest.raises(PatcherError, match="Preflight blocked"):
        apply_patch(plan, output)
    assert not output.exists()
    report_path = output.parent / "error-report"
    save_report(plan, report_path, "blocked")
    assert not list(report_path.glob("*.arxml"))
    assert json.loads((report_path / "patch_report.json").read_text())["errors"]


def test_tc18_post_validation_failure_is_atomic(inputs, monkeypatch):
    master, source, output = inputs(source_elements=element())

    def fail(*args):
        raise PatcherError("Injected XSD failure")

    monkeypatch.setattr(patch_engine, "validate_xsd", fail)
    with pytest.raises(PatcherError, match="Injected"):
        apply_patch(scan(master, source), output, "schema.xsd")
    assert not output.exists()
    assert not list(output.parent.glob(".arxml-patch-*"))


def test_staged_write_failure_is_atomic(inputs, monkeypatch):
    master, source, output = inputs(source_elements=element())

    def fail(*args):
        raise OSError("Disk full")

    monkeypatch.setattr(patch_engine, "_write_reports", fail)
    with pytest.raises(PatcherError, match="Disk full"):
        apply_patch(scan(master, source), output)
    assert not output.exists()
    assert not list(output.parent.glob(".arxml-patch-*"))


def test_existing_output_is_never_overwritten(inputs):
    master, source, output = inputs(source_elements=element())
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(PatcherError, match="already contains"):
        apply_patch(scan(master, source), output)
    assert sentinel.read_text() == "keep"


def test_empty_output_directory_is_accepted(inputs):
    master, source, output = inputs(source_elements=element())
    output.mkdir()
    assert apply_patch(scan(master, source), output) == output


@pytest.mark.parametrize("where", ["master", "source", "parent", "inside"])
def test_output_cannot_overlap_inputs(inputs, where):
    master, source, _ = inputs(source_elements=element())
    output = {
        "master": master,
        "source": source,
        "parent": master.parent,
        "inside": master / "new",
    }[where]
    with pytest.raises(PatcherError, match="separate"):
        apply_patch(scan(master, source), output)


@pytest.mark.parametrize("change", ["edit", "new-file", "delete"])
def test_stale_scan_is_rejected(inputs, change):
    master, source, output = inputs(source_elements=element())
    plan = scan(master, source)
    if change == "edit":
        with (master / "types.arxml").open("a") as f:
            f.write("\n")
    elif change == "new-file":
        (source / "new.arxml").write_text(document("", "NewPkg"))
    else:
        (source / "types.arxml").unlink()
    with pytest.raises(PatcherError, match="changed after scan"):
        apply_patch(plan, output)
    assert not output.exists()


def test_conflict_blocks_other_valid_additions(inputs):
    master, source, output = inputs(
        master_elements=element("Old"),
        source_elements=element("Old", "/Pkg/Different") + element(),
    )
    plan = scan(master, source)
    assert plan.summary["ADD"] == plan.summary["CONFLICT"] == 1
    with pytest.raises(PatcherError):
        apply_patch(plan, output)
    assert not output.exists()


def test_uuid_and_properties_are_copied_exactly(inputs):
    new = element(
        extra="<SW-DATA-DEF-PROPS><SW-DATA-DEF-PROPS-VARIANTS>"
        "<SW-DATA-DEF-PROPS-CONDITIONAL><SW-CALIBRATION-ACCESS>READ-ONLY"
        "</SW-CALIBRATION-ACCESS></SW-DATA-DEF-PROPS-CONDITIONAL>"
        "</SW-DATA-DEF-PROPS-VARIANTS></SW-DATA-DEF-PROPS>"
    ).replace(
        "<VARIABLE-DATA-PROTOTYPE>",
        '<VARIABLE-DATA-PROTOTYPE UUID="11111111-1111-1111-1111-111111111111">',
    )
    master, source, output = inputs(source_elements=new)
    apply_patch(scan(master, source), output)
    assert semantic(Dataset.load(source).objects["/Pkg/If/New"].node) == semantic(
        Dataset.load(output).objects["/Pkg/If/New"].node
    )
