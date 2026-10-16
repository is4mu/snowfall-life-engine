# P1 canonical CLI input-error proposal and legacy import review

**Status: Draft implementation and review-only error mapping.**
The initial 1.0 *functional scope* is owner approved, and W1 is the
initial persisted writer. The exact public ABI/CLI error policy, supported
Python promise, legacy import support window, main merge and release are
**not yet approved**.

## Narrow canonical `snowfall-life` error improvement

Only the diagnostic JSON-reader commands `validate` and `canonical-hash`
gain a predictable **proposed** failure envelope in
`snowfall_life.cli.main`. The Foundation implementation parser and command
functions in `engine.life.cli` are **unchanged**. Existing seven
commands, their successful text/JSON stdout and `argparse` exit codes
remain as before. `init-sandbox`, `bootstrap-propose`, `advance`
and `verify-workspace` remain **synthetic**, not stable full-runtime
commands; their unexpected Python exception behavior is *not* normalized
by this proposal.

| Failure | `validate` | `canonical-hash` |
| --- | --- | --- |
| Malformed JSON syntax or invalid UTF-8 in input | stderr `ERROR SCHEMA_INVALID: invalid JSON or UTF-8 input`, return 2 | stderr `ERROR INVALID_STATE: invalid JSON or UTF-8 input`, return 2 |
| Unreadable/missing/directory input file (`OSError`) | stderr `ERROR INVALID_STATE: unable to read JSON input`, return 2 | same |
| Valid JSON that violates the selected schema | existing `LifeEngineError(SCHEMA_INVALID)` stderr path; return 2 | n/a |
| Valid input | existing `OK schema=...` stdout; return 0 | existing 64-char hex stdout; return 0 |
| Missing CLI flags/invalid CLI syntax | existing `argparse` usage to stderr and `SystemExit(2)` | same |

The new wrapper **does not** catch arbitrary `ValueError`,
`KeyboardInterrupt`, `SystemExit`, unanticipated failure inside
Provider/World runtime, or application-side Python API I/O errors.
No user-supplied path or raw invalid JSON is printed for these *caught*
input failures, and failures perform no write/repair. Existing
`LifeEngineError` codes and diagnostic strings emitted by the Foundation
CLI remain otherwise intact.

The machine-readable [error behavior candidate](../oss/p1-v1-cli-error-review.json)
and regression tests guard this narrow mapping. Actual installed ephemeral
`1.0.0rc1` wheel **and** sdist execute
`tools/p1_cli_error_probe.py` using only site-packages imports and
synthetic files. Both archives' results are compared to guard against
distribution-specific error differences. A successful probe is **not**
human approval to freeze the CLI wire format.

## Python API error semantics remain a separate decision

The canonical Python API currently re-exports tested engine methods.
`LifeEngineError.code` is appropriate for deterministic domain/schema
and version failures, while `detail` is diagnostic text only. Raw
`OSError`, `UnicodeError`, `json.JSONDecodeError` and other Python
exceptions can still arise on some Python read paths. We do **not** expand
the entire Python runtime with new exception translation without a
review of code and caller/host boundaries. The v1 release gate must either
explicitly document native I/O and parse errors as part of the contract
or introduce separately justified narrowed adapters with tests.

## Python support and compatibility recommendation

- Current `pyproject.toml` declares **Python >=3.12,<3.13**. The
  initial 1.0 recommendation is **Python 3.12.x only**, not an
  untested promise for 3.13/older versions. A wider window needs CI.
- The published v0.1.0 `engine.life` **root** still exposes exactly
  eight names: `ENGINE_VERSION`, `SUPPORTED_ENGINE_VERSIONS`,
  `SUPPORTED_SCHEMA_VERSION`, `ErrorCode`, `LifeEngineError`,
  `canonical_bytes`, `canonical_hash`, `canonical_json`.
  Those remain working and the shared five names alias
  `snowfall_life` objects by identity. The old `engine.life.cli`
  retains its old exception behavior.
- Recommend retaining these eight root imports through all first
  1.x patch/minor releases and reviewing a removal only at a future
  major version. No promise is made for arbitrary undocumented
  `engine.life.*` implementation submodules.
- This is **policy recommendation + evidence**, not an approved
  support/deprecation announcement.

Remaining v1 gates: review exact ABI (#26), approve this CLI/error and
legacy/Python support policy, test an exact final integration-head RC
including private goldens and full long-horizon coverage, then request
separate authorization for main merge and general release. No tag,
production Git ref update, saved-data rewrite, new W2 format, private
Snowfall application data or Character Creator work is performed.
