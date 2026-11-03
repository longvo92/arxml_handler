"""Validated in-place writes with durable backups and rollback on ordinary failures."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from arxml_parser import Dataset, PatcherError


@dataclass(frozen=True)
class PatchResult:
    master_folder: Path
    backup_folder: Path
    changed_files: tuple[Path, ...]


def replace_file(path: Path, data: bytes) -> None:
    """Flush a temporary sibling before replacing a single file atomically."""
    fd, name = tempfile.mkstemp(prefix=".arxml-write-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            temporary.chmod(stat.S_IMODE(path.stat().st_mode))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _json(path: Path, data: dict) -> None:
    replace_file(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def commit_changes(plan, validated: Dataset, changed: set[Path], report: dict, write_reports):
    master, source = plan.master.folder, plan.source.folder
    if master.is_relative_to(source) or source.is_relative_to(master):
        raise PatcherError("Master and Source must be separate folders with no nesting")
    root = master.parent / ".arxml-patcher-backups"
    if root.is_symlink() or root.resolve() != root.absolute():
        raise PatcherError("Backup storage must not be a linked directory")
    root.mkdir(exist_ok=True)
    lock = root / (master.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise PatcherError(
            f"Master is locked by another/interrupted patch. Inspect {lock}"
        ) from exc
    os.close(fd)
    backup = None
    journal = None
    written = []
    try:
        for previous in root.glob(master.name + "-*/transaction.json"):
            try:
                state = json.loads(previous.read_text(encoding="utf-8"))
                if Path(state["master_folder"]) != master:
                    continue
                if state["state"] not in {"committed", "rolled_back"}:
                    raise PatcherError(f"Unfinished patch needs recovery: {previous.parent}")
            except (OSError, ValueError, KeyError) as exc:
                raise PatcherError(f"Cannot verify previous transaction: {previous}") from exc
        plan.master.assert_unchanged()
        plan.source.assert_unchanged()
        backup = Path(tempfile.mkdtemp(prefix=master.name + "-", dir=root))
        for file in sorted(changed):
            original = backup / "originals" / (str(file) + ".bak")
            original.parent.mkdir(parents=True, exist_ok=True)
            replace_file(original, plan.master.documents[file].original)
        journal = {
            "master_folder": str(master),
            "state": "prepared",
            "changed_files": sorted(p.as_posix() for p in changed),
            "original_sha256": plan.master.manifest(),
            "patched_sha256": validated.manifest(),
        }
        report.update(
            master_folder=str(master),
            backup_folder=str(backup),
            write_mode="in_place",
            outcome="prepared",
        )
        _json(backup / "transaction.json", journal)
        write_reports(backup, report)
        # All backups and validations must finish before any Master file is replaced.
        plan.master.assert_unchanged()
        plan.source.assert_unchanged()
        journal["state"] = "applying"
        _json(backup / "transaction.json", journal)
        for file in sorted(changed):
            destination = master / file
            if destination.read_bytes() != plan.master.documents[file].original:
                raise PatcherError(f"Master changed during patch: {file}")
            written.append(file)  # Include replacements interrupted immediately after rename.
            replace_file(destination, validated.documents[file].original)
        actual = Dataset.load(master)
        if actual.errors or actual.manifest() != validated.manifest():
            raise PatcherError("Master changed or could not be verified after writing")
        plan.source.assert_unchanged()
        report["outcome"] = "patched"
        write_reports(backup, report)
        journal["state"] = "committed"
        _json(backup / "transaction.json", journal)
        return PatchResult(master, backup, tuple(sorted(changed)))
    except BaseException as exc:
        recovery_errors = []
        for file in reversed(written):
            try:
                destination = master / file
                current = destination.read_bytes()
                original = plan.master.documents[file].original
                if current == original:
                    continue
                if current != validated.documents[file].original:
                    raise PatcherError("File changed externally; refusing to overwrite it")
                replace_file(destination, original)
            except BaseException as restore_error:
                recovery_errors.append(f"{file}: {restore_error}")
        if backup is not None and journal is not None:
            journal["state"] = "recovery_required" if recovery_errors else "rolled_back"
            journal["error"] = str(exc)
            journal["recovery_errors"] = recovery_errors
            report["outcome"] = journal["state"]
            report["errors"].append(str(exc))
            try:
                _json(backup / "transaction.json", journal)
                write_reports(backup, report)
            except BaseException as log_error:
                recovery_errors.append(f"Could not finalize recovery journal: {log_error}")
        detail = (
            "Recovery required: " + "; ".join(recovery_errors)
            if recovery_errors
            else "Any files written by this patch were rolled back."
        )
        raise PatcherError(f"In-place patch failed: {exc}. {detail} Backup: {backup}") from exc
    finally:
        lock.unlink(missing_ok=True)
