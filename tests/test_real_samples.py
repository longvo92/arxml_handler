"""Opt-in integration against user-owned codegen data and a locally supplied XSD."""

import os
import shutil
from pathlib import Path

import pytest
from lxml import etree as ET

from arxml_parser import Dataset, PatcherError, child, value
from diff_engine import scan
from patch_engine import apply_patch
from validator import validate_xsd


@pytest.fixture
def real_data(tmp_path):
    master, source = os.getenv("ARXML_TEST_MASTER"), os.getenv("ARXML_TEST_SOURCE")
    if not master or not source:
        pytest.skip("Set ARXML_TEST_MASTER and ARXML_TEST_SOURCE for real codegen integration")
    original_master, source = Path(master), Path(source)
    snapshot = Dataset.load(original_master)
    source_before = Dataset.load(source).manifest()
    safe_master = tmp_path / "master-under-test"
    for file, document in snapshot.documents.items():
        target = safe_master / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(document.original)
    yield safe_master, source
    assert Dataset.load(original_master).manifest() == snapshot.manifest()
    assert Dataset.load(source).manifest() == source_before


def test_real_matlab_codegen_patch_and_idempotency(real_data, tmp_path):
    master, source = real_data
    before = Dataset.load(master).manifest(), Dataset.load(source).manifest()
    plan = scan(master, source)
    assert plan.valid, plan.report()
    assert plan.operations, "The provided real sample must contain at least one missing S/R item"
    output = apply_patch(plan, os.getenv("AUTOSAR_XSD")).master_folder
    repeated = scan(output, source)
    assert repeated.valid and repeated.summary["ADD"] == 0
    assert Dataset.load(source).manifest() == before[1]
    changed = {Path(op.record.TargetFile) for op in plan.operations}
    for file, doc in plan.master.documents.items():
        if file not in changed:
            assert (output / file).read_bytes() == doc.original


def test_real_generated_definitions_all_three_features(real_data, tmp_path):
    master, source = real_data
    baseline = tmp_path / "controlled-master"
    shutil.copytree(master, baseline)
    dataset = Dataset.load(baseline)
    source_data = Dataset.load(source)
    src_interface = next(
        o
        for o in source_data.objects_of("SENDER-RECEIVER-INTERFACE")
        if child(o.node, "DATA-ELEMENTS") is not None
    )
    src_element = child(src_interface.node, "DATA-ELEMENTS")[0]
    element_path = src_interface.path + "/" + value(src_element, "SHORT-NAME")
    removed = dataset.objects[element_path]
    removed.node.getparent().remove(removed.node)
    src_port = next(
        o
        for o in source_data.objects_of("R-PORT-PROTOTYPE")
        if child(o.node, "REQUIRED-COM-SPECS") is not None
    )
    port = dataset.objects[src_port.path]
    container = child(port.node, "REQUIRED-COM-SPECS")
    container.remove(container[0])
    for file in {removed.file, port.file}:
        dataset.documents[file].tree.write(
            str(baseline / file), encoding="UTF-8", xml_declaration=True
        )
    plan = scan(baseline, source)
    assert plan.valid, plan.report()
    assert all(plan.summary[k] > 0 for k in ["DataElement", "ComSpec", "RunnableAccess"])
    output = apply_patch(plan, os.getenv("AUTOSAR_XSD")).master_folder
    assert scan(output, source).summary["ADD"] == 0


@pytest.mark.parametrize("component_name", ["ActuatorAdapter", "Controller", "SensorAdapter"])
def test_real_single_model_codegen_overwrites_old_files(real_data, tmp_path, component_name):
    master, generated = real_data
    master_data, generated_data = Dataset.load(master), Dataset.load(generated)
    assert not master_data.errors and not generated_data.errors
    path = f"/Components/{component_name}"
    if path not in generated_data.objects:
        pytest.skip(f"Generated sample does not include {component_name}")
    old_component = master_data.objects[path]
    new_component = generated_data.objects[path]
    model_prefix = new_component.file.name.removesuffix("_component.arxml") + "_"
    source = tmp_path / "architecture-with-overwritten-model"
    # Begin with the old architecture, then replace the selected model's files in place.
    for file, document in master_data.documents.items():
        target = source / file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(document.original)
    initial = scan(master, source)
    assert initial.valid and initial.summary["ADD"] == 0
    replacements = set()
    for file, document in generated_data.documents.items():
        if file.name.startswith(model_prefix):
            matches = [p for p in master_data.documents if p.name == file.name]
            assert len(matches) == 1
            relative = matches[0]
            (source / relative).write_bytes(document.original)
            replacements.add(relative)
    assert old_component.file in replacements
    for file, document in master_data.documents.items():
        if file not in replacements:
            assert (source / file).read_bytes() == document.original
    plan = scan(master, source)
    assert plan.valid, plan.report()
    output = master
    if plan.operations:
        # A second overwrite after Scan must invalidate the old plan without exporting.
        component_file = source / old_component.file
        newer_bytes = component_file.read_bytes()
        component_file.write_bytes(master_data.documents[old_component.file].original)
        with pytest.raises(PatcherError, match="changed after scan"):
            apply_patch(plan)
        assert Dataset.load(master).manifest() == master_data.manifest()
        component_file.write_bytes(newer_bytes)
        fresh = scan(master, source)
        apply_patch(fresh, os.getenv("AUTOSAR_XSD"))
        changed = {Path(op.record.TargetFile) for op in fresh.operations}
        assert changed <= replacements
        for file, document in master_data.documents.items():
            if file not in changed:
                assert (output / file).read_bytes() == document.original
        repeated = scan(output, source)
        assert repeated.valid and repeated.summary["ADD"] == 0
    else:
        assert (
            Dataset.load(master).manifest() == master_data.manifest()
        )  # Unchanged generated models need no patch.
    assert Dataset.load(generated).manifest() == generated_data.manifest()


def test_profile_order_matches_installed_xsd():
    xsd = os.getenv("AUTOSAR_XSD")
    if not xsd:
        pytest.skip("Set AUTOSAR_XSD to verify the built-in ordering profile")
    from schema_profile import ORDER

    tree = ET.parse(xsd)
    ns = {"x": "http://www.w3.org/2001/XMLSchema"}

    def expand(node):
        result = []
        for sub in node:
            if not isinstance(sub.tag, str) or sub.tag.endswith("}annotation"):
                continue
            if sub.tag.endswith("}element"):
                result.append(sub.get("name") or sub.get("ref"))
            elif sub.tag.endswith("}group") and sub.get("ref"):
                group = tree.xpath(
                    "/x:schema/x:group[@name=$name]",
                    namespaces=ns,
                    name=sub.get("ref").split(":")[-1],
                )[0]
                result.extend(expand(group))
            else:
                result.extend(expand(sub))
        return result

    for name, order in ORDER.items():
        kind = tree.xpath("/x:schema/x:complexType[@name=$name]", namespaces=ns, name=name)[0]
        assert expand(kind) == order


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize(
    "mode,direction",
    [
        ("DATA-READ-ACCESSS", "R"),
        ("DATA-WRITE-ACCESSS", "P"),
        ("DATA-RECEIVE-POINT-BY-ARGUMENTS", "R"),
        ("DATA-RECEIVE-POINT-BY-VALUES", "R"),
        ("DATA-SEND-POINTS", "P"),
    ],
)
def test_synthetic_access_and_comspec_against_real_xsd(inputs, queued, mode, direction):
    xsd = os.getenv("AUTOSAR_XSD")
    if not xsd:
        pytest.skip("Set AUTOSAR_XSD to validate synthetic queued/explicit fixtures")
    from conftest import access, component, comspec, element

    # Required nonqueued fields follow the verified 00047 sequence.
    if queued:
        content = "<USES-END-TO-END-PROTECTION>false</USES-END-TO-END-PROTECTION>" + (
            "<QUEUE-LENGTH>2</QUEUE-LENGTH>" if direction == "R" else ""
        )
    else:
        content = "<USES-END-TO-END-PROTECTION>false</USES-END-TO-END-PROTECTION>"
        if direction == "R":
            content += (
                "<ALIVE-TIMEOUT>0</ALIVE-TIMEOUT><ENABLE-UPDATE>false</ENABLE-UPDATE>"
                "<HANDLE-NEVER-RECEIVED>false</HANDLE-NEVER-RECEIVED>"
            )
    spec = comspec(direction, queued=queued, content=content)
    master, source, output = inputs(
        source_elements=element(),
        master_component=component(),
        source_component=component(
            accesses=access(mode, direction), **{direction.lower() + "com": spec}
        ),
    )
    apply_patch(scan(master, source), xsd)
    validate_xsd(Dataset.load(master), xsd)
