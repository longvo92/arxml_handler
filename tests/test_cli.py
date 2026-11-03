from conftest import element

from diff_engine import scan
from main import main


def test_scan_has_no_output_side_effects(inputs):
    master, source, output = inputs(source_elements=element())
    assert main(["--master", str(master), "--source", str(source)]) == 0
    assert not output.exists()


def test_cli_apply_and_report(inputs):
    master, source, output = inputs(source_elements=element())
    report = output.parent / "report-only"
    assert (
        main(
            [
                "--master",
                str(master),
                "--source",
                str(source),
                "--apply",
                "--report",
                str(report),
            ]
        )
        == 0
    )
    assert scan(master, source).summary["ADD"] == 0
    assert not output.exists()
    assert (report / "patch_report.json").exists()


def test_blocked_cli_returns_two_and_exports_only_reports(inputs):
    master, source, output = inputs(source_elements=element(dtype="/Pkg/Missing"))
    report = output.parent / "blocked-report"
    assert (
        main(
            [
                "--master",
                str(master),
                "--source",
                str(source),
                "--apply",
                "--report",
                str(report),
            ]
        )
        == 2
    )
    assert not output.exists()
    assert (report / "patch_report.txt").exists()
