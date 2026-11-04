"""Validate accepted additions in memory before updating Master in place."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
from pathlib import Path

from lxml import etree as ET

from arxml_parser import NS, Dataset, Document, PatcherError, child, semantic, tag
from diff_engine import Plan, compare
from file_transaction import PatchResult, commit_changes
from file_transaction import revert_patch as revert_patch
from schema_profile import ACCESS_CONTAINERS, ORDER
from validator import check_access, check_dependencies, check_order, validate_xsd


def report_text(report: dict) -> str:
    lines = [
        "AUTOSAR S/R Incremental Patcher 0.1.0",
        f"Outcome: {report['outcome']}",
        f"Master: {report['master_folder']}",
        f"Source: {report['source_folder']}",
        f"Preflight valid: {report['preflight_valid']}",
        f"XSD validated: {report['xsd_validated']}",
        report["validation_note"],
        "Summary: " + ", ".join(f"{k}={v}" for k, v in report["summary"].items()),
        "",
    ]
    lines.extend("ERROR: " + e for e in report["errors"])
    for row in report["records"]:
        if row["Action"] != "EXISTS":
            lines.append(
                f"[{row['Action']}] {row['Category']} {row['ObjectPath']}: {row['Reason']}"
            )
    return "\n".join(lines) + "\n"


def _write_reports(folder: Path, report: dict) -> None:
    (folder / "patch_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (folder / "patch_report.txt").write_text(report_text(report), encoding="utf-8")


def _report_path(report: dict, folder: str | Path) -> Path:
    if not str(folder).strip():
        raise PatcherError("Select a report folder")
    output = Path(folder).expanduser().absolute()
    if output.is_symlink():
        raise PatcherError("Report folder must not be a symbolic link")
    output = output.resolve()
    for input_folder in [Path(report["master_folder"]), Path(report["source_folder"])]:
        if output.is_relative_to(input_folder) or input_folder.is_relative_to(output):
            raise PatcherError("Report folder must be separate from both inputs (no nesting)")
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        raise PatcherError("Report folder already contains data; select a new or empty folder")
    return output


def _publish(stage: Path, output: Path) -> None:
    if output.exists():
        # rmdir only succeeds for an empty directory, including if another writer raced us.
        output.rmdir()
    # On Windows rename refuses an existing destination; never use replace/overwrite.
    if output.exists():
        raise PatcherError("Output appeared during export; it was not overwritten")
    os.rename(stage, output)


def save_report(plan: Plan | PatchResult, folder: str | Path, outcome="scanned", error="") -> Path:
    """Explicit report-only export also works for blocked scans; contains no ARXML."""
    if isinstance(plan, PatchResult):
        report = copy.deepcopy(plan.report)
        report["revert_available"] = plan.can_revert
        if error:
            report["errors"].append(error)
    else:
        report = plan.report(outcome, error)
    output = _report_path(report, folder)
    output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".arxml-report-", dir=output.parent))
    try:
        _write_reports(stage, report)
        _publish(stage, output)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return output


def _append(parent: ET._Element, node: ET._Element, index: int | None = None) -> None:
    """Match existing indentation without reformatting the Master tree."""
    elems = [n for n in parent if tag(n)]
    closing = elems[-1].tail if elems else parent.text
    sibling = elems[-2].tail if len(elems) > 1 else parent.text
    if not sibling or sibling.strip():
        depth = len(list(parent.iterancestors())) + 1
        sibling = "\n" + "    " * depth
    if not closing or closing.strip():
        closing = "\n" + "    " * len(list(parent.iterancestors()))
    if index is None or index >= len(parent):
        if len(parent):
            parent[-1].tail = sibling
        else:
            parent.text = sibling
        node.tail = closing
        parent.append(node)
    else:
        if index == 0:
            parent.text = sibling
        else:
            parent[index - 1].tail = sibling
        node.tail = sibling
        parent.insert(index, node)


def _container(parent: ET._Element, name: str) -> tuple[ET._Element, bool]:
    existing = child(parent, name)
    if existing is not None:
        return existing, False
    error = check_order(parent)
    if error:
        raise PatcherError(error)
    order = ORDER[tag(parent)]
    index = next(
        (i for i, n in enumerate(parent) if tag(n) and order.index(tag(n)) > order.index(name)),
        len(parent),
    )
    new = ET.Element(f"{{{NS}}}{name}")
    _append(parent, new, index)
    return new, True


def _serialize(document: Document) -> bytes:
    raw = ET.tostring(
        document.tree,
        encoding=document.tree.docinfo.encoding or "UTF-8",
        xml_declaration=document.original.lstrip().startswith(b"<?xml"),
        pretty_print=False,
    )
    if document.original.endswith(b"\n") and not raw.endswith(b"\n"):
        raw += b"\n"
    if b"\r\n" in document.original:
        raw = raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return raw


def apply_patch(plan: Plan, xsd: str | Path | None = None) -> PatchResult:
    if not plan.valid:
        raise PatcherError("Preflight blocked: resolve all errors, conflicts and unsupported items")
    if not plan.operations:
        raise PatcherError("No ADD changes to apply")
    if plan.master.folder.is_relative_to(plan.source.folder) or plan.source.folder.is_relative_to(
        plan.master.folder
    ):
        raise PatcherError("Master and Source must be separate folders with no nesting")
    plan.master.assert_unchanged()
    plan.source.assert_unchanged()
    # Build fresh trees from the captured bytes; scan input objects remain untouched.
    patched = Dataset(plan.master.folder)
    for file, doc in plan.master.documents.items():
        patched._parse(file, doc.original)
    patched.reindex()
    changed = set()
    inserted: list[tuple[ET._Element, ET._Element]] = []
    created: list[tuple[ET._Element, ET._Element]] = []
    phase = {"DataElement": 0, "ComSpec": 1, "RunnableAccess": 2}
    operations = sorted(plan.operations, key=lambda op: phase[op.record.Category])
    for op in operations:
        target = patched.objects[op.parent_path]
        container, was_created = _container(target.node, op.container)
        if was_created:
            created.append((target.node, container))
        node = copy.deepcopy(op.source)
        _append(container, node)
        inserted.append((container, node))
        changed.add(target.file)
    patched.reindex()
    if patched.errors:
        raise PatcherError("Post-patch indexing failed: " + "; ".join(patched.errors))
    for op, (_, node) in zip(operations, inserted, strict=True):
        if semantic(node) != semantic(op.source):
            raise PatcherError(f"Copied definition changed: {op.record.ObjectPath}")
        _, error, _ = check_dependencies(node, patched.objects, plan.source)
        if error:
            raise PatcherError(f"Post-patch reference validation failed: {error}")
        if op.record.Category == "RunnableAccess":
            error = check_access(
                node, op.parent_path, ACCESS_CONTAINERS[op.container], patched.objects
            )
            if error:
                raise PatcherError(error)
        error = check_order(patched.objects[op.parent_path].node)
        if error:
            raise PatcherError(error)
    repeat = compare(patched, plan.source)
    if not repeat.valid or repeat.summary["ADD"]:
        raise PatcherError("Post-patch idempotency/duplicate check failed")
    # Prove all original XML content is unchanged when the permitted additions are removed.
    proof = Dataset(plan.master.folder)
    proof.documents = {
        file: Document(file, doc.original, copy.deepcopy(doc.tree))
        for file, doc in patched.documents.items()
    }
    proof.reindex()
    for op in reversed(operations):
        target = proof.objects[op.parent_path]
        container = child(target.node, op.container)
        container.remove(container[-1])
    for parent, container in reversed(created):
        path = patched.paths[parent]
        proof_parent = proof.objects[path].node
        proof_parent.remove(child(proof_parent, tag(container)))
    for file, doc in plan.master.documents.items():
        if semantic(proof.documents[file].tree.getroot()) != semantic(doc.tree.getroot()):
            raise PatcherError(f"Unrelated XML content changed in {file}")
    # Serialize and parse once more before changing any Master file.
    serialized = Dataset(plan.master.folder)
    for file, doc in patched.documents.items():
        serialized._parse(file, _serialize(doc) if file in changed else doc.original)
    serialized.reindex()
    if serialized.errors:
        raise PatcherError("Output XML validation failed: " + "; ".join(serialized.errors))
    for file, doc in serialized.documents.items():
        if semantic(doc.tree.getroot()) != semantic(patched.documents[file].tree.getroot()):
            raise PatcherError(f"XML serialization changed meaning in {file}")
    if xsd:
        validate_xsd(serialized, xsd)
    report = plan.report("validated", xsd=bool(xsd))
    report["changed_files"] = sorted(p.as_posix() for p in changed)
    report["post_patch_validation"] = {
        "references": True,
        "structure": True,
        "unrelated_content_preserved": True,
        "second_scan_add": repeat.summary["ADD"],
    }
    return commit_changes(plan, serialized, changed, report)
