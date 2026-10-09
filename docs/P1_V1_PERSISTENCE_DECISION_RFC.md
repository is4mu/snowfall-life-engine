# P1 RFC — v1 persisted-data readability and initial writer policy

**Status: PROPOSED / APPROVAL REQUIRED, 2026-10-09.** This is a decision request, **not** an accepted compatibility promise, approved new engine/schema version, implementation, or authorization to merge. Owns [Issue #9](https://github.com/is4mu/snowfall-life-engine/issues/9); follows the frozen responsibilities in [V1_BOUNDARY.md](V1_BOUNDARY.md).

## Verified facts; do not extrapolate

- Released OSS provenance: `v0.1.0`, exact commit `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`. This is **not** the stored engine version.
- Current checkpoint engine gate: `ENGINE_VERSION = "0.1.0-foundation"`; `SUPPORTED_ENGINE_VERSIONS` currently contains only this value. The migration registry has **no registered migrations**.
- All **16 inventoried persisted document families** use schema version `1`; OperatorAction uses a four-variant `oneOf` rather than one root `schema_version` field.
- Frozen synthetic baseline: `tests/fixtures/golden/persistence-v0.1.0/`. [PR #13](https://github.com/is4mu/snowfall-life-engine/pull/13) pins per-file and canonical hashes, timeline/camera/operator links, two-step correction lineage, original bytes, and cross-process replay. Its target inspection is deliberately *current->current*, not v0.1.0->v1 migration.
- Existing `operator_history.normalize_operator_history` accepts **missing** `schema_version` by interpreting it as version 1 in memory; the total semantic tree or execution metadata can still reject a byte-altered historical snapshot. Do not generalize this exception to other families.
- The local Git transaction builders create **unreferenced** candidate commits and prohibit branch/ref updates; application-owned publication is a separate authority boundary.

## Decision R1 — What must the first stable 1.x reader accept?

| Choice | Proposed behavior | Compatibility/risk |
| --- | --- | --- |
| **A (recommended)** | For the **first** v1 release, read fully valid exact v0.1.0-formatted snapshots through a verified, explicitly documented **legacy read path**. Keep source files unchanged during reading. Reject malformed, unsupported and future-version inputs. | Preserves existing adopters and avoids rewriting closed history. Only promise supported historical formats **proven with goldens**; pin algorithm identity and input policy. |
| B | Require a declared, explicit old->new staged migration to a **different approved** target persisted engine/schema version before the v1 runtime can read old snapshots. | Requires design + implementation of target versions, re-hashing/link invariants, rollback and every per-family golden vector before shipping. No such migration exists today. |
| C | Do not support reading v0.1.0 persisted data in 1.0; start with v1-only formats. | Simplifies reader policy but breaks existing data consumers. This must be a prominently documented loss of compatibility, never a silent fallback. |

**Recommendation: A** as the 1.0 baseline. This would bind 1.x to a carefully enumerated historical format rather than all pre-1.0 data. **No selection is made by this RFC.**

## Decision R2 — What does the *first* 1.0 writer emit?

| Choice | Proposed behavior | Compatibility/risk |
| --- | --- | --- |
| **W1 (recommended, contingent on R1=A)** | At initial 1.0, preserve **the existing serialized family schema versions (all 16: v1)** and the existing checkpoint `engine_version` value for the same semantic format. Do not auto-upgrade during load. A package release labeled 1.0.0 is a **separate identity** from this persisted-format label. | No gratuitous reserialization or version bump; reviewers must explicitly accept the legacy-looking persisted engine label and prevent silent semantic drift within that label. |
| W2 | Introduce a **new** stored `engine_version` and/or schema version at the first 1.0 write. Define a two-format reader or approved migration plan. | More legible numbering but triggers new compatibility vectors and integrity work before 1.0. Renaming a constant is not a migration. |

The first stable **package** may be called `1.0.0` without making `ENGINE_VERSION = "1.0.0"`. If W1 is chosen, document the persisted-format epoch separately and define a future explicit migration to newer engine/schema identifiers. Changing simulation semantics under an unchanged persisted identifier is **not** automatically authorized.

## Decision R3 — Legacy OperatorHistory normalization

Recommend **keep the precise existing exception for approved legacy input** (missing `schema_version` accepted as v1 in memory), while rejecting incompatible altered whole-tree hashes. Do not emit missing-version data from a new writer. Alternative: explicit legacy migration that writes the field under a verified, separately authorized transition.

## Compatibility table — proposal status only

| Family group | Stored schema at v0.1.0 | Proposed initial writer (W1 only) | Read guarantee now |
| --- | --- | --- | --- |
| State/Current, Schedule, Relation, Home, Consumables, Wardrobe, Finance | `1` each | `1` each | **Not approved** |
| SocialResponseState | `1` | `1` | **Not approved** |
| TimelineDay and nested ActualEvent | `1` each | `1` each | **Not approved** |
| CameraRollShard and nested CameraRollRecord | `1` each | `1` each | **Not approved** |
| OperatorHistory and OperatorAction variants | `1` each | `1` each | **Not approved** |
| RuntimeLastRun and LastOperatorAction metadata | `1` each | `1` each | **Not approved** |

The machine-readable [16-family inventory](../oss/persistence-contract-inventory.json) deliberately retains **null** v1 read/write/migration fields until an explicit owner decision, demonstrated compatibility suite, and separately reviewed implementation. This table is **not** permission to fill them yet.

## Invariants that gate either A/W1 or B/W2

1. A new 1.x reader must load the **unaltered** pinned 0.1.0 golden tree and validate every document, ActualEvent, capture link, correction supersession, operator ledger and execution-metadata binding. A reader **never** mutates a source merely by opening/verifying it.
2. The preflight tool remains stricter than some legacy readers: unknown files and symlinks fail closed, not silently imported. Distinguish supported runtime input from complete storage-root policy.
3. Repeated loads and staged copies must preserve raw bytes and canonical/semantic hashes, with explicit version rejection; failures must not repair an invalid source.
4. Any later **format-changing migration** must prove staged source/target validation, deterministic outputs, correct old/new schema gates and full copy-to-candidate history integrity. Hash-changing rewrites require explicit reviewed re-key/relink rules, not hidden repairs.
5. Covered process environments: three `PYTHONHASHSEED` values (0/1/8675309) × two timezones (UTC/Asia/Tokyo); Fast, Full, 72h/28d/90d, installed wheel/sdist + Provider consumer.
6. Approve an **actual** minimum readable format, writer-format upper bound, deprecation policy, error/CLI protocol and packaging name **before** declaring 1.0 stable. Unsupported future versions fail closed.

## Decision request (explicit sign-off required)

1. **R1:** Support verified v0.1.0 format reads in the first 1.x release (**A**), or require migration (**B**), or drop support (**C**)?
2. **R2:** For the first 1.0 writer, keep existing serialized version values **W1**, or design **W2** and its migration before release?
3. **R3:** Retain the OperatorHistory missing-version **legacy-read-only** exception, or migrate/remove it?

**No default becomes binding without an affirmative choice.** If approved, record date, owner, provenance and test evidence in Issue #9; then update the machine-readable per-family matrix and implement the approved compatibility facade. See [Migration Transaction RFC](P1_MIGRATION_TRANSACTION_RFC.md) for later format transitions.
