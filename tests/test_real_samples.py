"""Opt-in integration against user-owned codegen data and a locally supplied XSD."""

import os
import shutil
from pathlib import Path

import pytest
from lxml import etree as ET

from arxml_parser import Dataset, child, value
from diff_engine import scan
from patch_engine import apply_patch
from validator import validate_xsd


@pytest.fixture
def real_data():
    master, source = os.getenv("ARXML_TEST_MASTER"), os.getenv("ARXML_TEST_SOURCE")
    if not master or not source:
        pytest.skip("Set ARXML_TEST_MASTER and ARXML_TEST_SOURCE for real codegen integration")
    return Path(master), Path(source)


def test_real_matlab_codegen_patch_and_idempotency(real_data, tmp_path):
    master, source = real_data
    before = Dataset.load(master).manifest(), Dataset.load(source).manifest()
    plan = scan(master, source)
    assert plan.valid, plan.report()
    assert plan.operations, "The provided real sample must contain at least one missing S/R item"
    output = apply_patch(plan, tmp_path / "patched", os.getenv("AUTOSAR_XSD"))
    repeated = scan(output, source)
    assert repeated.valid and repeated.summary["ADD"] == 0
    assert (Dataset.load(master).manifest(), Dataset.load(source).manifest()) == before
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
    output = apply_patch(plan, tmp_path / "patched", os.getenv("AUTOSAR_XSD"))
    assert scan(output, source).summary["ADD"] == 0


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
    apply_patch(scan(master, source), output, xsd)
    validate_xsd(Dataset.load(output), xsd)
