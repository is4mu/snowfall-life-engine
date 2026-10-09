# P1 RFC — v1 persisted-data readability and initial writer policy

**Status: OWNER DIRECTION RECORDED (R1 and R3); WRITER FORMAT STILL OPEN, 2026-10-09.** The selected goals below are requirements for implementation and verification, **not** a shipped compatibility promise, a chosen new engine/schema version, or authorization to merge. Owns [Issue #9](https://github.com/is4mu/snowfall-life-engine/issues/9); follows the frozen responsibilities in [V1_BOUNDARY.md](V1_BOUNDARY.md).

## Owner direction recorded on 2026-10-09

1. **R1 = A (selected as a v1 requirement):** normal, valid v0.1.0 life save data **must remain readable by the new 1.x reader**. This includes checked semantic/history/capture/operator bindings; invalid, unknown or future formats still fail closed. A newer reader is not allowed to silently rewrite old saved files.
2. **R2 = future-resilient storage design early (direction selected; W1/W2 NOT selected):** design an extensible **versioning + compatibility + explicit migration mechanism during P1**, before the stable v1 contract is finalized, so subsequent engine releases do not casually strand character histories. There is **no requirement to immediately rewrite** today's saved data; choosing whether the first 1.0 writer emits today's layout (W1) or a new layout (W2) awaits separate technical review and demonstration. See [Persistence Evolution Design](P1_PERSISTENCE_EVOLUTION_DESIGN.md).
3. **R3 = retain precise legacy behavior (selected):** the existing OperatorHistory missing-`schema_version` case remains readable **under its current integrity rules**, without automatic modification. New writers must emit explicit versions. A malformed hash chain is still rejected.

**Approval boundary:** the owner has approved the **direction** of compatibility and preservation, not a new format identifier, target bytes, API signatures, migration implementation, automatic publish step, or a v1 release. The 16-family machine-readable manifest therefore continues to leave future guarantees/versions as `null` until passing tests and explicit v1 contract review.

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

**Selected direction: A.** The first stable 1.x reader must accept approved valid v0.1.0-format snapshots, proven with pinned golden vectors. **This remains an acceptance requirement, not an implemented stable-reader guarantee.** The exact compatible format inventory and legacy import facade still require full review.

## Decision R2 — What does the *first* 1.0 writer emit? (**still open**)

| Choice | Proposed behavior | Compatibility/risk |
| --- | --- | --- |
| **W1 (recommended, contingent on R1=A)** | At initial 1.0, preserve **the existing serialized family schema versions (all 16: v1)** and the existing checkpoint `engine_version` value for the same semantic format. Do not auto-upgrade during load. A package release labeled 1.0.0 is a **separate identity** from this persisted-format label. | No gratuitous reserialization or version bump; reviewers must explicitly accept the legacy-looking persisted engine label and prevent silent semantic drift within that label. |
| W2 | Introduce a **new** stored `engine_version` and/or schema version at the first 1.0 write. Define a two-format reader or approved migration plan. | More legible numbering but triggers new compatibility vectors and integrity work before 1.0. Renaming a constant is not a migration. |

The owner requests a **future-resilient evolution scheme early**, not an immediate compulsory reformat. Therefore **W1/W2 remain open**; no writer-format version may be locked or bumped yet. The first stable **package** may be called `1.0.0` without making `ENGINE_VERSION = "1.0.0"`. A W1 transition would need explicit semantic-version guarding to prevent behavioral drift under identical format IDs; W2 needs a proven dual-reader and deterministic migration. See [Persistence Evolution Design](P1_PERSISTENCE_EVOLUTION_DESIGN.md).

## Decision R3 — Legacy OperatorHistory normalization

**Selected direction:** keep the precise existing exception for approved legacy input (missing `schema_version` accepted as v1 **in memory**) while rejecting incompatible altered whole-tree hashes. New writers must emit an explicit version. An eventual deliberate normalization/migration is separate from read-only legacy acceptance; the loader must never silently rewrite the source.

## Compatibility table — proposal status only

| Family group | Stored schema at v0.1.0 | Proposed initial writer (W1 only) | Stable 1.x read implementation |
| --- | --- | --- | --- |
| State/Current, Schedule, Relation, Home, Consumables, Wardrobe, Finance | `1` each | `1` each | **Direction chosen; not yet verified/shipped** |
| SocialResponseState | `1` | `1` | **Direction chosen; not yet verified/shipped** |
| TimelineDay and nested ActualEvent | `1` each | `1` each | **Direction chosen; not yet verified/shipped** |
| CameraRollShard and nested CameraRollRecord | `1` each | `1` each | **Direction chosen; not yet verified/shipped** |
| OperatorHistory and OperatorAction variants | `1` each | `1` each | **Direction chosen; not yet verified/shipped** |
| RuntimeLastRun and LastOperatorAction metadata | `1` each | `1` each | **Direction chosen; not yet verified/shipped** |

The machine-readable [16-family inventory](../oss/persistence-contract-inventory.json) deliberately retains **null** v1 read/write/migration fields until an explicit owner decision, demonstrated compatibility suite, and separately reviewed implementation. This table is **not** permission to fill them yet.

## Invariants that gate either A/W1 or B/W2

1. A new 1.x reader must load the **unaltered** pinned 0.1.0 golden tree and validate every document, ActualEvent, capture link, correction supersession, operator ledger and execution-metadata binding. A reader **never** mutates a source merely by opening/verifying it.
2. The preflight tool remains stricter than some legacy readers: unknown files and symlinks fail closed, not silently imported. Distinguish supported runtime input from complete storage-root policy.
3. Repeated loads and staged copies must preserve raw bytes and canonical/semantic hashes, with explicit version rejection; failures must not repair an invalid source.
4. Any later **format-changing migration** must prove staged source/target validation, deterministic outputs, correct old/new schema gates and full copy-to-candidate history integrity. Hash-changing rewrites require explicit reviewed re-key/relink rules, not hidden repairs.
5. Covered process environments: three `PYTHONHASHSEED` values (0/1/8675309) × two timezones (UTC/Asia/Tokyo); Fast, Full, 72h/28d/90d, installed wheel/sdist + Provider consumer.
6. Approve an **actual** minimum readable format, writer-format upper bound, deprecation policy, error/CLI protocol and packaging name **before** declaring 1.0 stable. Unsupported future versions fail closed.

## Decision status and next gates

- **R1 accepted:** require validated v0.1.0 readability under the first stable 1.x reader. Install/run future 1.x wheel + sdist against immutable v0.1.0 goldens before declaring success.
- **R3 accepted:** retain exact legacy OperatorHistory read semantics, without mutation or weakening integrity.
- **R2 unresolved:** choose W1 or W2 **after** reviewing the concrete durable format/evolution design and its test results. The owner's priority is reliable future upgrades, not cosmetic version renumbering.
- **Still requires approval:** stable public import/CLI facade, exact source/target version table, migration implementation/commit policy, first writer format, merge and release.

The [16-family manifest](../oss/persistence-contract-inventory.json) remains intentionally unpromoted until a working 1.x compatibility contract and implementation are verified. See [Persistence Evolution Design](P1_PERSISTENCE_EVOLUTION_DESIGN.md) and [Migration Transaction RFC](P1_MIGRATION_TRANSACTION_RFC.md).
