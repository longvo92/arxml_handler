# XML mapping and verified profile

Phase 0 inspection used the supplied MATLAB R2024a generated `MasterCode` (12
ARXML files) and `codegen` (5 ARXML files), namespace
`http://autosar.org/schema/r4.0`, schema `AUTOSAR_00047.xsd`.
No generated C, MATLAB Engine or model edits are used.

| Item | XML location / identity | Master file ownership |
| --- | --- | --- |
| Data element | `AR-PACKAGE/ELEMENTS/SENDER-RECEIVER-INTERFACE/DATA-ELEMENTS/VARIABLE-DATA-PROTOTYPE` | Existing interface's file |
| Port ComSpec | `APPLICATION-SW-COMPONENT-TYPE/PORTS/{R,P}-PORT-PROTOTYPE/{REQUIRED,PROVIDED}-COM-SPECS/*` | Existing port's file |
| Access | `INTERNAL-BEHAVIORS/SWC-INTERNAL-BEHAVIOR/RUNNABLES/RUNNABLE-ENTITY/<access container>/VARIABLE-ACCESS` | Existing runnable's file |

Paths concatenate ancestor `SHORT-NAME`s, including nested packages, component,
behavior and runnable. XML container names are not AUTOSAR path segments.
Packages may be split across files; duplicate non-package definitions are rejected,
even if identical. File names and short names alone never determine a match.

Real examples include `/Interfaces/If_Command/DE_CommandValue` and
`/Components/ActuatorAdapter/R_Command`. Accesses use
`ACCESSED-VARIABLE/AUTOSAR-VARIABLE-IREF/PORT-PROTOTYPE-REF` plus
`TARGET-DATA-PROTOTYPE-REF`. The data element's parent interface must agree with
the selected port's interface, and the port must belong to the runnable's component.

Implicit read/write roles are `DATA-READ-ACCESSS` and `DATA-WRITE-ACCESSS`.
Explicit roles are `DATA-RECEIVE-POINT-BY-ARGUMENTS`,
`DATA-RECEIVE-POINT-BY-VALUES`, and `DATA-SEND-POINTS`. The literal spelling
`ACCESSS` is intentional. The samples demonstrate implicit read/write and explicit
receive-by-argument/send. Receive-by-value and queued ComSpecs use synthetic
fixtures and the 00047 XSD, rather than a claim of MATLAB sample coverage.

The insertion order in `schema_profile.py` was expanded from the corresponding
`complexType` and inherited `group` sequences in the locally installed MathWorks
`schemas/00047/autosar.xsd`. Notably runnable read, receive-by-argument,
receive-by-value, send and write containers precede `SERVER-CALL-POINTS` and
`SYMBOL`; required/provided ComSpecs precede their interface TREF; interface data
elements precede invalidation policies. An unknown parent child blocks insertion.
Other schema revisions are explicitly unsupported until their profiles are verified.

Source/Master equivalence ignores UUID attributes recursively, including reachable
dependency definitions. DEST, other attributes, child order and meaningful text
remain significant. XML indentation, namespace prefix choice, comments and attribute
ordering do not affect equivalence. Existing Master UUIDs are preserved.
No implicit/explicit conversion is performed.

Scan prepares independent copies of accepted additions. Copied UUIDs that collide
with Master or earlier additions, and empty UUID attributes, receive UUIDv4 values.
Generated values avoid every UUID found in both input sets and the plan. UUIDs absent
from Source remain absent. DCE UUID case/prefix variants share one collision key;
opaque identifier namespaces retain literal identity. Apply inserts exactly these
prepared nodes and verifies their complete attributes. Preservation and serialization
proofs continue to compare UUIDs strictly. JSON `uuid_changes` entries describe planned
reassignments, including source XML paths for nested nodes. Source duplicate UUIDs
are tolerated during scan and stale-input checking; Master duplicates block patching
except split declarations of the same AR-PACKAGE. Input files remain unchanged by Scan.

References are resolved across the Master set plus accepted new data elements.
Dependency definitions such as datatypes and constants must already exist; if
also supplied by Source, their definitions must agree with Master. Missing or
incompatible dependencies block Apply. Unrelated existing references are not
claimed to be fully integration-validated. Optional user-provided XSD validation
checks all resulting documents; structural/reference checks alone are not full
AUTOSAR schema or RTE/BSW integration validation.

Apply validates the patched set in memory, then replaces only changed Master files.
Original bytes, modes and modification timestamps are retained in RAM for session
Revert. Revert verifies all changed files before restoring original bytes and times.
Ordinary failures roll back the current operation, and incomplete recovery retains
its result in memory for retry. Reports are exported explicitly. Source stays
read-only. See the README for session lifetime, interruption handling and API migration.
