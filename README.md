# AUTOSAR S/R Incremental Patcher

A small Windows desktop tool that copies missing generated S/R definitions into
the existing Master AUTOSAR architecture in place. Python 3.11+, `lxml`
and Tkinter; no MATLAB session, server, or AUTOSAR authoring tool is required.

## Run

Open `dist/AUTOSAR-SR-Patcher.exe` on Windows, or run from source:

```powershell
python -m pip install -r requirements.txt
python main.py
```

1. Select **Master ARXML Folder** (the existing set to update) and **Source ARXML
   Folder** (generated definitions). ARXML files are discovered recursively, regardless of names
   or extension case. Definitions and dependencies can reside in different files.
2. Click **Scan & Compare**. This creates no output. Select rows to inspect full
   paths, reasons and dependencies. Scan again after changing either input.
   When preflight passes with no supported additions, a **Scan complete** popup
   confirms the result and shows the number of equivalent items found in Master.
3. Optionally select a matching XSD file with all local include/import files.
4. Click **Apply to Master**. It is enabled only when preflight passes and additions
   exist. The tool validates the complete result before modifying any Master file.
   The success message confirms that session undo is available. Scan again to refresh
   the results after Apply.
5. Click **Revert Last Apply** to restore the previous files and their **Date modified**
   timestamps. This is available only while the same application session stays open.

**Save Report** exports `patch_report.txt` and `patch_report.json` separately,
including for blocked scans or failed applications. It does not export ARXML.
The GUI, CLI and text reports list additions and blockers. Equivalent items
(`EXISTS`) appear as a count only. Text reports show one line per item, and
JSON reports retain every record with its file paths and dependencies.
Source is read-only. Master and Source must be separate folders with no nesting.
Scan snapshots are checked again before writing, so changes made by code generation
or another process require a fresh scan. There is no Output Folder to select.

When regenerating one model, overwrite its old ARXML files in the selected Source
set and leave the other models in that set unchanged. Select a single coherent
set: for the sample layout, use `codegen/ClassicSample` for the assembled set or
the individual `*_SWC_autosar_rtw` folder when its dependencies exist in Master.
Selecting the aggregate `codegen` root when it also contains copies in both
locations is blocked as duplicate definitions. Overwriting files after Scan
requires scanning again before Apply.

## Supported changes

- Add a missing `VARIABLE-DATA-PROTOTYPE` to an existing S/R interface.
- Add a missing nonqueued/queued receiver/sender ComSpec on an existing R/P port.
- Add a missing `VARIABLE-ACCESS` on an existing runnable, preserving its mode:
  implicit read/write, explicit receive by argument/value, or explicit send.

All matches use fully qualified AUTOSAR paths. Source/Master comparisons ignore
UUID attributes throughout each compared subtree, including datatype and constant
dependencies. Existing Master objects retain their UUIDs. Other technical
attributes, DEST, init values and properties remain significant. Formatting-only
whitespace, XML comments, namespace prefixes and attribute ordering are ignored
when comparing; meaningful text and child order remain significant.

`ADD` means safe to copy; `EXISTS` means equivalent; `CONFLICT` means an existing
identity differs; `UNSUPPORTED` means outside the supported operation or a missing
dependency. Any error, conflict or unsupported record blocks the **entire** patch.
There is no selective conflict bypass.

New nodes retain their Source UUIDs when available and unique. UUID collisions
with Master or other planned additions, and empty UUID attributes on copied nodes,
are replaced with UUIDv4 values during Scan. Apply uses those planned values.
Missing UUID attributes stay optional. DCE UUID case and prefix variations are
recognized as the same identifier. Custom identifier namespaces are compared
literally. Source may contain duplicate UUIDs because only accepted additions
are copied and repaired; duplicate object paths still block the scan.
Master UUID collisions block the scan, except repeated declarations of the same
AR-PACKAGE across files. JSON reports include a `uuid_changes` list with the owning
operation, source and target files, source XML node path, old/new UUID and reason.
An empty list means no UUID reassignment was planned. ADD reasons include a count
of reassigned UUIDs. UUID-only differences produce EXISTS and require no write.

Datatypes, constants and their reachable reference dependencies must already exist
in Master. If Source also defines a dependency, it must agree with Master.
No interfaces, datatypes, constants, ports, components or runnables are created.
No existing definition is updated, renamed or deleted; timing, events, symbols,
connectors, implementation and generated C are left intact. Unrelated objects
are not synchronized. C/S and local-variable access are never added.

Runnable accesses require a port mapped to an existing Sender-Receiver interface;
NV-Data interfaces are outside scope even when accepted by the AUTOSAR XSD.
Unknown data access modes always block the patch, including when their access
node matches Master. Unchanged accesses in the known read/write local-variable
containers may coexist with the supported S/R changes.

## In-place updates, session Revert and validation

Only changed Master ARXML files are replaced. Other ARXML and generated artifacts
such as C, headers and A2L retain their original bytes. Modified files are serialized as XML
and can have minor serialization differences; existing XML content is verified
semantically unchanged after removing the permitted additions.

Patch order is data elements, ComSpecs, then runnable accesses. Validation checks
identity collisions, new references and DEST types, datatype/value dependencies,
port direction and interface membership, verified container order, unrelated
content preservation, reparsing, and a second scan with `ADD = 0`. All checks,
including optional XSD validation, finish in memory before writing Master.

Apply retains the original bytes, file modes and modification timestamps of changed
files in RAM. **Revert Last Apply** restores those exact bytes and **Date modified**
values using nanosecond timestamps, then verifies them. File creation timestamps
and filesystem change logs follow operating-system behavior.

The GUI retains one undo result for the latest successful Apply. Scanning again or
changing Source preserves it. Select the corresponding Master folder to enable
Revert. A later successful Apply replaces the previous undo result. Closing the app,
process termination or power loss discards the in-memory originals. The CLI discards
undo data when the command exits. Use the GUI or a live Python API session when
session Revert is needed. Multi-file writes can be interrupted by process termination
or power loss, and recovery then depends on independently retained originals.

Every changed file is flushed to a temporary sibling and replaced atomically. Apply
and Revert take the same exclusive Master lock. Before Revert writes anything, all
changed files must match their expected bytes, modification times and modes. External
edits, missing files or linked file paths block Revert. Other Master files and Source
are outside the Revert operation, so Source can be moved or removed after Apply.

An ordinary write failure triggers rollback to the state before that operation,
including modification timestamps. If rollback is incomplete, the result enters
`recovery_required` and retains the originals in memory. Further Apply operations on
that Master in the same process are blocked. Keep the GUI open and retry **Revert
Last Apply** after resolving the reported file access problem. The GUI blocks normal
close during active operations and incomplete recovery. The Python API exposes the
same recovery result through `TransactionError.result`.

Reports are saved explicitly through **Save Report** or CLI `--report`. Apply and
Revert keep their reports in memory until exported. JSON reports include the actual
operation outcome, undo storage mode, original modification times and validation
results. `revert_available` describes availability in the exporting process only.
After Revert, `post_revert_validation` records byte/timestamp restoration and
`apply_xsd_validated` records the earlier Apply's XSD result.

The temporary lock is `<Master parent>/.<Master name>.arxml-patcher.lock` and is removed
on normal completion. After a forced process exit, remove a leftover lock only after
ensuring the operation has stopped and reviewing the Master files. Existing backups
from older releases are retained. An unfinished legacy transaction or legacy lock
still blocks writes until its recovery is resolved.

**Schema support is deliberately limited to namespace
`http://autosar.org/schema/r4.0` with `AUTOSAR_00047.xsd` in `xsi:schemaLocation`.**
Other/unspecified revisions, foreign element namespaces, DTD/entities, ambiguous
non-package definitions, duplicate Master UUIDs and linked input directories are rejected.
AR-PACKAGE definitions may be distributed across files. See
[XML mapping and ordering evidence](docs/xml-mapping.md).

Without an XSD, checks are structural/reference checks, **not full AUTOSAR schema
validation**. With an XSD, every resulting document must pass it. Neither mode
claims RTE/BSW integration correctness or replaces downstream importer validation.
XSD files are not bundled; supply a licensed local schema if required. For a
MathWorks schema that imports `xml.xsd`, place both files in the same directory
or use an equivalent complete local schema set.

## Command line

No arguments opens the GUI. The source entry point also supports automation:

```powershell
# Scan only, optionally saving reports to a separate new/empty directory.
python main.py --master 'C:\data\Master' --source 'C:\data\Source' --report 'C:\data\ScanReport'

# Update Master in place with optional full XSD validation and session-only undo.
python main.py --master 'C:\data\Master' --source 'C:\data\Source' `
  --apply --xsd 'C:\schemas\AUTOSAR_00047.xsd'
```

Exit code `0`: scan or patch succeeded. Exit code `2`: blocked scan, failed
validation/write, invalid arguments, or no additions when `--apply` is requested.
The packaged windowed executable is intended for GUI use; use Python for console
output and command-line automation.

Migration from disk-backed backups: `PatchResult.backup_folder` has been removed.
`apply_patch(plan, xsd=None)` returns a session-only `PatchResult` with
`master_folder`, `source_folder`, `changed_files`, `report` and `can_revert`.
Keep that result alive and call `revert_patch(result)` in the same process.
`save_report(result, folder)` exports the actual Apply/Revert report, including XSD
validation status. `save_report(plan, folder)` continues to export scan reports.

```python
from diff_engine import scan
from patch_engine import apply_patch, revert_patch, save_report

result = apply_patch(scan(master_folder, source_folder), xsd=schema_path)
save_report(result, report_folder)
revert_patch(result)
```

For the older copy-output CLI, remove `--output` from commands. `--master` is the
set modified by `--apply`. Applications needing a separate patched copy can copy
Master themselves and select that copy as Master.

## Development and Windows packaging

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
.\build.bat
```

`build.bat` runs tests and lint before producing `dist/AUTOSAR-SR-Patcher.exe`
with PyInstaller. Tk tests need a working graphical desktop and skip if no
display is available. Standard automated fixtures cover TC01–TC20, both R/P
directions, all five access modes, blocked writes, session Revert, rollback, input changes, GUI
callbacks, CLI and report generation. Fixtures are generated in temporary
directories by `tests/conftest.py`; no user ARXML is committed.

Optional integration tests use user-owned data and schemas:

```powershell
$env:ARXML_TEST_MASTER = 'C:\data\MasterCode'
$env:ARXML_TEST_SOURCE = 'C:\data\codegen\ClassicSample'
$env:AUTOSAR_XSD = 'C:\schemas\AUTOSAR_00047.xsd'
python -m pytest -q
```

The supplied MATLAB R2024a sample was tested across 12 Master and 5 Source files:
the unmodified inputs need two runnable accesses, with no missing data elements
or ComSpecs. A temporary copy with a generated data element and ComSpec removed
exercises all three additions together. Both cases pass XSD 00047 validation and
idempotency; integration tests only modify temporary Master copies and preserve
the supplied real inputs. Queued ComSpecs and receive-by-value
are covered by synthetic fixtures checked against that XSD. No external
DaVinci/RTE importer validation has been performed.
