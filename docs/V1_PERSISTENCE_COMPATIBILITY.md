# v1 Persistence Compatibility Proposal and Artifact Inventory

Status: **pre-1.0 proposal** based on the released v0.1.0 code. This is not a migration implementation or a guarantee that existing v0.x artifacts already load under future 1.x.

## Separate version authorities

| Authority | Observed v0.1.0 value | Future obligation |
| --- | --- | --- |
| Git/public release | `v0.1.0`, commit `a274470dcc1b6a2f22c1ae74df19d94d66a21f34` | Distribution provenance; never substitute for persisted format version. |
| Checkpoint engine | `ENGINE_VERSION = "0.1.0-foundation"`; only current version in `SUPPORTED_ENGINE_VERSIONS` | Distinct semantic compatibility/migration gate. |
| JSON Schema document | Persisted document families below currently require `schema_version: 1` | Schema version is independent of engine/release SemVer. Operator action/request root schemas are variant `oneOf` documents. |
| Behavior policy | `behavior_policy_version` | Keep separate policy identity/hash and approval checks. |
| Exact implementation | `engine_commit_sha` (Git SHA) | Preserve pinning and integrity; do not treat as the package version. |

`versioning.py` currently has an empty migration registry. `load_checkpoint` calls `migrate_state`, which fails closed with `UNSUPPORTED_VERSION` when an old/new engine version lacks an explicit migration. **Renaming the engine-version constant alone would strand existing persisted checkpoints.**

## Actual runtime artifact matrix

| Artifact path relative to runtime root | Schema / current document version | Current reader/integrity binding |
| --- | --- | --- |
| `state/current.json` | `current_state` v1 | checkpoint loader, RuntimeBundle; state revision and finalized history head |
| `schedule/state.json` | `schedule_state` v1 | RuntimeBundle / schedule state |
| `relations/state.json` | `relation_state` v1 | RuntimeBundle with approved person IDs |
| `home/state.json` | `home_state` v1 | RuntimeBundle with approved entity IDs |
| `consumables/state.json` | `consumables_state` v1 | RuntimeBundle with approved consumable IDs |
| `wardrobe/state.json` | `wardrobe_state` v1 | RuntimeBundle with approved wardrobe item IDs |
| `finance/state.json` | `finance_state` v1 | RuntimeBundle / finance validation |
| `state/social-responses.json` | `social_response_state` v1 | full persistent-snapshot reader; character/as-of/revision binding |
| `timeline/YYYY-MM-DD.json` | `timeline_day` v1, nested `actual_event` v1 | append-only history and timeline verification |
| `camera-roll/records/YYYY-MM.json` | `camera_roll_shard` v1, nested `camera_roll_record` v1 | capture-to-event causality and shard integrity |
| `state/operator-history.json` and `operator/actions/<sha>.json` | `operator_history` v1; `operator_action` variants | operator ledger, hash and action-chain verification |
| `state/last-run.json` | `runtime_last_run` v1 | optional execution metadata bound to semantic tree |
| `state/last-operator-action.json` | `last_operator_action` v1 | optional operator metadata bound to ledger/tree |

The first **seven** paths are required by `runtime_bundle.REQUIRED_BUNDLE_RELPATHS`; a complete 5C persistent snapshot also requires `state/social-responses.json`. Missing optional timeline/camera/operator artifacts must still satisfy history/causality invariants. `runtime_persistence.SEMANTIC_FIXED_RELPATHS` excludes `state/last-run.json` and `state/last-operator-action.json` from the semantic tree while validating their bindings separately.

## Proposed 1.x policy — approval required

1. **New readers read old supported 1.x:** readers accept previously written supported 1.x documents or migrate through a documented, deterministic and lossless path. Old readers do **not** automatically promise acceptance of newer 1.x writers because existing schemas reject unknown keys. Downgrade is not guaranteed.
2. **Stage then verify:** migration works on a copy/candidate tree; the original bytes remain unchanged until a validated, integrity-proven transaction is committed. Read-only inspection never mutates the source.
3. **Fail closed:** unknown future/unsupported engine, schema, or policy versions must not be coerced or silently downgraded. Compatibility errors need a documented public code.
4. **History means facts:** keep finalized event order/identity, causal links, correction/recovery lineage, canonical serialization and restart equivalence. Hash-changing format migrations require an explicit reviewed re-key/relink procedure and immutable provenance.
5. **Family-specific matrix:** for every artifact row record: schema `$id`, earliest readable version, newest writable version, migration function, validation order, synthetic golden input/output, and negative unsupported-version coverage. No migration can be assumed to exist merely because `register_migration` is present.
6. **Release semantics:** additive schema changes still require proven newer-reader compatibility; a format-breaking removal is a major-version decision after a documented transition, not a silent minor/patch update.

## Required acceptance evidence

- Current stored state round-trips; unsupported `engine_version` leaves source bytes unchanged and raises `UNSUPPORTED_VERSION`.
- Every required JSON document passes the current registered schema and rejects structurally invalid/unknown-version input.
- Old->new migration and failure injection preserve source bytes, event hashes, operator ledger, camera roll, correction lineage, and semantic tree checks.
- 72h restart equivalence, 28d structural and 90d invariant tests remain valid with the proposed compatibility rules.
- Installed wheels and sdists contain the schema resources and can run all readers/validation away from the source checkout.

**Approval of this policy is not the same as implementing migration.** A 1.0 release must not proceed until the actual migration/readability matrix and tests are complete.
