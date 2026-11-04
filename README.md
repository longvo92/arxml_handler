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
3. Optionally select a matching XSD file with all local include/import files.
4. Click **Apply to Master**. It is enabled only when preflight passes and additions
   exist. The tool validates the complete result before modifying any Master file.
   The success message shows the automatic backup and report location. Scan again
   to refresh the results after Apply.

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

All matches use fully qualified AUTOSAR paths. The whole Source node is copied,
including UUID, DEST, init values and properties. Different UUIDs or other
technical attributes are conflicts, even when names match. Formatting-only
whitespace, XML comments, namespace prefixes and attribute ordering are ignored
when comparing; meaningful text and child order remain significant.

`ADD` means safe to copy; `EXISTS` means equivalent; `CONFLICT` means an existing
identity differs; `UNSUPPORTED` means outside the supported operation or a missing
dependency. Any error, conflict or unsupported record blocks the **entire** patch.
There is no selective conflict bypass.

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

## In-place updates, backups and validation

Only changed Master ARXML files are replaced. Other ARXML and generated artifacts
such as C, headers and A2L retain their original bytes. Modified files are serialized as XML
and can have minor serialization differences; existing XML content is verified
semantically unchanged after removing the permitted additions.

Patch order is data elements, ComSpecs, then runnable accesses. Validation checks
identity collisions, new references and DEST types, datatype/value dependencies,
port direction and interface membership, verified container order, unrelated
content preservation, reparsing, and a second scan with `ADD = 0`. All checks,
including optional XSD validation, finish in memory before writing Master.

An automatic backup is created at
`<Master parent>/.arxml-patcher-backups/<Master name>-<unique ID>/`.
It contains `originals/<relative ARXML path>.bak` for each changed file,
`patch_report.txt`, `patch_report.json`, and `transaction.json` with file hashes
and transaction state. Backups use `.bak` extensions and live outside Master,
so recursive ARXML scans do not index them as duplicate definitions.

Each file is flushed to a temporary sibling and replaced atomically. A failure
during the sequence triggers rollback of files already written. If a file was
changed externally or cannot be restored, backup files are retained and the
transaction is marked `recovery_required`; subsequent Apply attempts are blocked.
A lock also prevents two tool instances from patching the same Master at once.
The GUI waits for an active operation to finish before allowing normal close.

Replacing multiple files is not a single filesystem transaction: forced process
termination or power loss may interrupt it. After such an interruption, inspect
the backup's journal and compare the current files against the stored original
and patched hashes. Restore the listed `.bak` files to their corresponding Master
paths if rolling back, then mark the journal `rolled_back`. Remove a leftover
`<Master name>.lock` only after ensuring no patch process is still running and
recovery is complete. Scan again before applying further changes. Never overwrite
independent edits made after the interrupted patch without reviewing them.

**Schema support is deliberately limited to namespace
`http://autosar.org/schema/r4.0` with `AUTOSAR_00047.xsd` in `xsi:schemaLocation`.**
Other/unspecified revisions, foreign element namespaces, DTD/entities, ambiguous
non-package definitions, duplicate UUIDs and linked input directories are rejected.
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

# Update Master in place with backup and optional full XSD validation.
python main.py --master 'C:\data\Master' --source 'C:\data\Source' `
  --apply --xsd 'C:\schemas\AUTOSAR_00047.xsd'
```

Exit code `0`: scan or patch succeeded. Exit code `2`: blocked scan, failed
validation/write, invalid arguments, or no additions when `--apply` is requested.
The packaged windowed executable is intended for GUI use; use Python for console
output and command-line automation.

Migration from the earlier copy-output MVP: remove `--output` from commands.
`--master` is now the set modified by `--apply`. The Python API is now
`apply_patch(plan, xsd=None)` and returns a `PatchResult` with `master_folder`,
`backup_folder` and `changed_files`. Applications needing a separate patched copy
can copy Master themselves and select that copy as Master.

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
directions, all five access modes, blocked writes, backup/rollback, input changes, GUI
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
