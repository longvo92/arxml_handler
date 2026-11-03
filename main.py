"""GUI entry point and scriptable scan/apply interface."""

from __future__ import annotations

import argparse
import sys

from arxml_parser import PatcherError
from diff_engine import scan
from patch_engine import apply_patch, save_report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from gui import launch

        launch()
        return 0
    parser = argparse.ArgumentParser(description="AUTOSAR S/R Incremental Patcher (00047)")
    parser.add_argument("--master", required=True, help="Master ARXML folder (read-only)")
    parser.add_argument("--source", required=True, help="Source ARXML folder (read-only)")
    parser.add_argument("--apply", action="store_true", help="Apply the validated plan")
    parser.add_argument("--output", help="New or empty output folder; required with --apply")
    parser.add_argument("--report", help="Optional separate new/empty report-only folder")
    parser.add_argument("--xsd", help="Optional AUTOSAR XSD with local include/import dependencies")
    args = parser.parse_args(argv)
    if args.apply and not args.output:
        parser.error("--apply requires --output")
    if args.xsd and not args.apply:
        parser.error("--xsd validates the output and requires --apply")
    plan = scan(args.master, args.source)
    failure = ""
    outcome = "scanned" if plan.valid else "blocked"
    try:
        print(", ".join(f"{k}={v}" for k, v in plan.summary.items()))
        for error in plan.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        for row in plan.records:
            if row.Action != "EXISTS":
                print(f"[{row.Action}] {row.Category} {row.ObjectPath}: {row.Reason}")
        if args.apply:
            path = apply_patch(plan, args.output, args.xsd)
            outcome = "patched"
            print(f"Validated output: {path}")
    except (PatcherError, OSError) as exc:
        failure = str(exc)
        outcome = "failed"
        print(f"ERROR: {failure}", file=sys.stderr)
    if args.report:
        try:
            print(f"Reports: {save_report(plan, args.report, outcome, failure)}")
        except (PatcherError, OSError) as exc:
            failure = str(exc)
            print(f"ERROR saving report: {exc}", file=sys.stderr)
    return 0 if plan.valid and not failure else 2


if __name__ == "__main__":
    raise SystemExit(main())
