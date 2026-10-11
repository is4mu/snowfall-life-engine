# P1 minimum stable-v1 **ABI candidate** — final review proposal

**Status: REVIEW-ONLY.** Owner-approved *functional scope* (basic deterministic
RuntimeBundle, safe W1 read/stage and spatial verification) is recorded in
[Basic scope](P1_V1_BASIC_API_SCOPE.md). Owner-approved initial persisted
format is W1. **Neither choice approved the exact public Python function
signatures, CLI failure contract, supported Python window, release or merge.**

The source-backed [candidate machine contract](../oss/p1-v1-abi-review.json)
and `tools/p1_api_abi_probe.py` now verify an explicit list of function
argument names/kinds/default presence, all **four** keyword-only Provider
callbacks, and field names of key frozen public result types. The
signature SHA-256 printed by the probe is **compatibility-review evidence**,
not an irrevocable versioned ABI or a hash of historical saved facts.

## Recommended initial 1.x Python contract

**Only these five canonical modules** should be in the first stable API
scope if the exact contracts are approved:

| Module | Role | Proposed guarantee |
| --- | --- | --- |
| `snowfall_life` | Core typed failures and canonical hashing | `ErrorCode`, `LifeEngineError`, `canonical_json`, `canonical_bytes`, `canonical_hash` |
| `snowfall_life.schema` | Resource-backed validation of 37 schemas | `load_schema_document`, `validate_instance` |
| `snowfall_life.runtime` | Deterministic call-local Provider / result types | Provider protocol, request/results, callback annotation types and `advance_runtime_to_target_with_provider` |
| `snowfall_life.persistence` | W1 load/verify and separate candidate construction | `load_runtime_persistent_snapshot`, `build_runtime_candidate_tree_with_provider_factory` and typed results |
| `snowfall_life.spatial` | Sealed spatial data and approved route projection | `build_spatial_context`, `validate_spatial_context`, both `project_spatial_runtime_*` functions |

The explicit export list in `oss/p1-v1-basic-api-scope.json` remains the
candidate source of truth for named symbols. The new ABI review manifest
adds **13 callable signature records**, the four Provider callback
argument lists, and seven key frozen data-shape records. Never infer a
guarantee for any other currently importable implementation symbol.
These result types are shallow-frozen; nested mappings and application
authority need defensive ownership discipline, not a fictional deep-freeze
guarantee.

A reader may safely load verified historical v0.1.0/W1 snapshots without
writing to the old tree; a writer returns a **separate unreferenced
candidate** and cannot CAS-publish the application Life ref. Source SHA,
policy and approved identity sets remain explicit caller inputs.
The first 1.0 writer keeps `engine_version="0.1.0-foundation"` and
sixteen schema-version-1 families. Unsupported versions/integrity
violations fail closed. Upgrades, Git ref publication, operator recovery
and W2 migration are **not stable 1.0 core features**.

## Python error contract proposal — still needs agreement

Recommend treating `LifeEngineError.code` as the **machine-readable failure
identity** and `detail`/exception string as **diagnostic only** (not
parseable/supported text). Preserve existing distinct error values
including `SCHEMA_INVALID`, `INVALID_STATE`,
`UNSUPPORTED_VERSION`, `HISTORY_HASH_MISMATCH` and
`POLICY_HASH_MISMATCH` for relevant failures. Avoid promising all
internal `ErrorCode` values imply stable public operations.

**Gap:** missing/malformed JSON files, filesystem permissions and some
unexpected parsing exceptions can currently propagate as normal Python
exceptions rather than `LifeEngineError`. Before approval, explicitly
choose whether the stable Python contract maps these inputs to typed error
codes or documents which exceptions may propagate. Do **not** secretly
change v0.1.0 error semantics without separate testing.

## CLI candidate — diagnostics, not a production Life service

Propose only `snowfall-life self-check`, `validate`, and
`canonical-hash` as initial basic CLI contracts. Today they print
different text formats and use exit 0 for success; argparse errors and
caught `LifeEngineError` return/exit 2. The other four Foundation
commands remain **synthetic sandbox tools** and must not be marketed as
the full Provider runtime.

**Explicit unresolved issue:** malformed JSON and ordinary filesystem
errors are not consistently caught/formatted by the existing CLI. Recommend
implementing and contract-testing clear stderr/exit behavior **before**
promising a stable CLI error ABI; alternatively keep diagnostics
experimental until that improvement. Do not declare this resolved based
only on currently green CLI smoke tests.

## Python-version and legacy import recommendations

- **Python 3.12.x only for initial 1.0 support** is the narrowest justified
  first-release option. The current CI, isolated wheel/sdist and private
  RC use Python 3.12. Supporting 3.13+ requires new interpreter CI.
- Preserve the **published v0.1.0 `engine.life` root exports** as working
  compatibility imports through 1.x; announce their eventual removal in
  a separately reviewed major transition rather than silently breaking
  existing users in a minor/patch release. This is a **recommendation**
  requiring explicit sign-off. Importability of all internal submodules
  does **not** make them permanent stable APIs.
- Within the eventual stable v1 series, new minor versions should maintain
  the agreed exported call shapes and W1 historical-reader contract,
  with additive expansion/deprecation only under documented rules.
  Older binaries are not guaranteed to read unknown future persisted
  formats.

## Release-candidate conformance

The private `1.0.0rc1` GitHub workflow additionally runs the ABI
probe against **both installed wheel and sdist outside checkout** and
compares the complete deterministic JSON ABI report. It checks signature,
callback and frozen-data-shape conformity on actual versioned packages
alongside existing 12-matrix legacy/W1 replay, full Provider, 37 schemas,
Fast/Full/72h/28d/90d, publication and coverage tests.

**Passing is evidence, not a contract decision.** Remaining explicit
go/no-go choices:

1. Accept/revise the exact five module export lists, **13 proposed callable
   signatures**, four callback signatures and seven public data shapes.
2. Decide the Python error behavior for I/O/JSON failures and the canonical
   three-command CLI's error/exit contract.
3. Approve Python 3.12.x support and the proposed `engine.life` legacy
   root compatibility window.
4. Reconcile the stacked Drafts, retest a final exact integration commit,
   inspect branch coverage and regressions, **then separately authorize**
   a stable API declaration, main merge, tag and general release.

All upstream PRs and this proposal remain Draft. No production Life
ref, real character save, historical fixture, W1 writer epoch, main
branch or package `pyproject.toml` version changes in this review.
