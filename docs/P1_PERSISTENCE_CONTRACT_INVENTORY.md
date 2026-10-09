# P1 Persistence Contract Inventory — v0.1.0 Evidence

Status: **pre-v1 inventory and test oracle** (not a frozen 1.x migration/readability contract). Parent: [Issue #9](https://github.com/is4mu/snowfall-life-engine/issues/9). Related design: [Draft PR #7](https://github.com/is4mu/snowfall-life-engine/pull/7).

This document and [`oss/persistence-contract-inventory.json`](../oss/persistence-contract-inventory.json) identify **all 16 currently versioned persistent-document families** found in the public engine's runtime state, history, capture and operator trees. The JSON inventory records the schema registry name, current version, associated sample path/embedded JSON pointer, current reader anchor, bundle requirement, semantic-tree membership, and explicit **unresolved** v1 read/write/migration fields. The schema registry contains 37 total schemas: most others are inputs, outputs or supporting contracts rather than the persistent families listed here.

## Proven baseline, not inferred v1 support

- The OSS release is `v0.1.0`; persisted checkpoint `ENGINE_VERSION` is still `0.1.0-foundation`, with no pre-registered state migration.
- The 16 documented stored-document families use `schema_version: 1`. The OperatorAction root schema is a `oneOf` containing four v1 variants; its root has no universal `properties.schema_version` entry.
- Bundle loading requires seven files: state/current, schedule, relations, home, consumables, wardrobe, and finance. Full 5C persistent snapshot loading additionally requires state/social-responses. Timeline/camera/operator records may be absent only if consistency invariants allow it.
- The semantic tree fixes nine exact JSON paths. Timeline, Camera Roll and operator action paths are dynamic; two optional execution-summary files (`state/last-run.json`, `state/last-operator-action.json`) are **not** semantic-tree content but are verified against the authoritative state when present.
- There is an existing **compatibility exception** in `operator_history.normalize_operator_history`: a missing `schema_version` is inserted as version 1 before schema validation. A blanket "missing schema version must reject" rule would be a behavior change. This exception must be explicitly reviewed, preserved or migrated before any v1 normalization rule is declared.
- A future `engine_version` cannot be read without an explicit registered migration; changing `ENGINE_VERSION` to match `1.0.0` without one would break old checkpoints.

## What remains to be approved before 1.0

| Gate | Decision/evidence required | Current status |
| --- | --- | --- |
| R1: v0.1.0 -> v1 policy | Decide whether the first stable 1.x reader promises to read v0.1.0 historical artifacts. A no-compatibility choice must be documented before release. | **Unresolved** |
| R2: per-family reader/writer | Fill `v1_oldest_readable_schema_version` and `v1_newest_writable_schema_version` for each of the 16 entries. Null means **no promise**, not version 1. | **Unresolved** |
| R3: upgrade algorithm | Explicitly versioned, deterministic staging from source copy; validate source and target, preserve source bytes on failure, commit only after full integrity proof. | **Not implemented** |
| R4: append-only invariants | Golden IDs/hash vectors; ActualEvent/camera/operator cross-links; correction lineage; read-only failure injection; restart equivalence. | **Release-candidate gate** |
| R5: v1 public migration entrypoint | Declare one documented API/CLI entry with errors, return type, source immutability and deprecation contract. | **Unresolved** |
| R6: installed-package closure | Wheel/sdist must expose all 37 schema resources with no hidden checkout/private app assumptions. | **Demonstrated by Draft PR #8**, not yet released |
| R7: supported Python | v0.1.0 CI verifies only Python 3.12. Any wider 1.x guarantee needs a matrix. | **Unresolved** |

## How the new contract tests work

`tests/contracts/persistence/test_persistence_contract_inventory.py` verifies the recorded schema IDs and each family's v1 schema pin (including four OperatorAction variants), rejects future versions at the JSON Schema field level, checks the actual runtime path allowlist and bundle/semantic membership, and covers the documented OperatorHistory missing-version exception.

These tests intentionally **do not** register migrations, change persistent schemas, modify runtime code, infer future compatibility or touch real character state.

## Explicit boundary

All data and tests are generic, synthetic and source-backed. No production authority, private canon, Character Creator, LLM/media behavior or remote GitHub Life-ref publisher enters this repository. This proposal remains a Draft until the compatibility guarantees are agreed, implemented and tested; no merge/release/API stability decision is implied.
