"""GUI entry point and scriptable scan/apply interface."""

from __future__ import annotations

import argparse
import sys

from arxml_parser import PatcherError
from diff_engine import scan
from file_transaction import TransactionError
from patch_engine import apply_patch, save_report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from gui import launch

        launch()
        return 0
    parser = argparse.ArgumentParser(description="AUTOSAR S/R Incremental Patcher (00047)")
    parser.add_argument("--master", required=True, help="Existing Master ARXML folder to patch")
    parser.add_argument("--source", required=True, help="Source ARXML folder (read-only)")
    parser.add_argument(
        "--apply", action="store_true", help="Patch Master in place (session-only undo)"
    )
    parser.add_argument("--report", help="Optional separate new/empty report-only folder")
    parser.add_argument("--xsd", help="Optional AUTOSAR XSD with local include/import dependencies")
    args = parser.parse_args(argv)
    if args.xsd and not args.apply:
        parser.error("--xsd validates the patched Master and requires --apply")
    plan = scan(args.master, args.source)
    failure = ""
    outcome = "scanned" if plan.valid else "blocked"
    report_target = plan
    try:
        print(", ".join(f"{k}={v}" for k, v in plan.summary.items()))
        for warning in plan.warnings:
            print(f"WARNING: {warning}")
        for error in plan.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        for row in plan.records:
            if row.Action != "EXISTS":
                print(f"[{row.Action}] {row.Category} {row.ObjectPath}: {row.Reason}")
        if args.apply:
            print("Undo data is held in memory and expires when this command exits.")
            result = apply_patch(plan, args.xsd)
            report_target = result
            outcome = "patched"
            print(f"Master updated: {result.master_folder}")
    except (PatcherError, OSError) as exc:
        if isinstance(exc, TransactionError):
            report_target = exc.result
            if exc.result.state == "recovery_required":
                print(
                    "Recovery is incomplete. This command loses its in-memory recovery data "
                    "when it exits. Use the GUI or Python API for session recovery.",
                    file=sys.stderr,
                )
        failure = str(exc)
        outcome = "failed"
        print(f"ERROR: {failure}", file=sys.stderr)
    if args.report:
        try:
            print(f"Reports: {save_report(report_target, args.report, outcome, failure)}")
        except (PatcherError, OSError) as exc:
            failure = str(exc)
            print(f"ERROR saving report: {exc}", file=sys.stderr)
    return 0 if plan.valid and not failure else 2


if __name__ == "__main__":
    raise SystemExit(main())
