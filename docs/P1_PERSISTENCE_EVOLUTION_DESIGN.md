# P1 Persistence Evolution Design — Future-Resilient Save Data

**Status: DESIGN TARGET / REVIEW PROPOSAL, 2026-10-09.** The owner requests (a) continuation from valid v0.1.0 saved histories, (b) an **early durable approach** to upgrading the save format over future releases (not necessarily an immediate reformat), and (c) continued narrow legacy OperatorHistory read tolerance. **No new format, schema, writer version, migration or public facade is implemented or authorized by this document.**

## Why this matters

Character history grows for months or years. We need to be able to update engine code **without changing the meaning of yesterday's events or losing a save file**. A file format cannot guarantee all conceivable future upgrades forever; the reliable solution is a **versioned compatibility protocol**, immutable historical evidence, and tested old-reader/new-reader transitions. A package version and a saved-data format version answer different questions.

## Existing anchors that must survive the transition

| Identity | Existing format | v1 evolution requirement |
| --- | --- | --- |
| Python distribution | `v0.1.0` released; `snowfall_life` / `snowfall-life` are unreleased pre-v1 candidates | Version libraries with SemVer **independently** of state compatibility. |
| Serialized checkpoint engine identity | `engine_version: "0.1.0-foundation"` | An explicit historical format/semantic epoch; do **not** rename it to 1.0 just because the distribution changes. |
| Stored schema families | Sixteen schema-v1 families (including nested ActualEvent, Capture and operator variants) | Each family retains its own explicit `schema_version`, typed schema IDs, read/write compatibility range and deterministic migration edges. |
| Behavior authority | `behavior_policy_version` and approved policy hashes | Behavior-policy upgrades must not silently reinterpret old facts; validate separately from format. |
| Exact implementation | `engine_commit_sha` | Source/pin provenance, not package SemVer or schema version. |
| Historical identity | ActualEvent IDs/hashes, correction chain, Camera Roll links, operator ledger, revision and semantic-tree hash | Append-only facts and causal links are immutable unless a specifically approved and audited relink migration proves consistency. |

## Proposed contract boundaries (not committed APIs)

1. **Decoder is separate from writer.** A 1.x reader recognizes an **explicit allowlist** of historically supported formats and selects the matching decoder, never guesses from missing fields (except the already-existing documented OperatorHistory v1 legacy case). Unrecognized future/invalid formats fail closed. *Reading alone never changes bytes.*
2. **Normalize to a verified in-memory representation.** Old v0.1.0 data is read and validated in its original format; optional data-shape adapters may construct a **detached view** for the new engine. Never pretend a converted view is the persisted original or re-sign canonical historical hashes without authority.
3. **Write exactly one declared format per operation.** The writer must declare its target family versions and persisted engine semantic epoch; add or change fields only with explicitly reviewed schemas and compatibility tests. Do not use a package tag as the persisted engine version.
4. **Migration edges are explicit and pure.** Use a registry keyed by **concrete old->new family/engine-version tuples**, with named versions and deterministic functions. Do not implicitly invoke the currently unrestricted mutable `versioning.register_migration` registry as a stable public migration API. Unsupported paths are errors, not silent best-effort coercion.
5. **Hash and ordering invariants are independent constraints.** The original serialized blobs/hashes, event ordering, correction supersession, capture links and operator history must be preserved or undergo an explicit reviewed, complete re-key/relink with frozen old/new proofs. Metadata files have separate integrity bindings and cannot be assumed safe merely because the semantic-tree hash stayed unchanged.
6. **Deploy using expansion before contraction.** Ship a reader that can accept the old proven format **before** activating a new writer. Initially keep reading old formats during the agreed support window; only later, with explicit policy and migration evidence, consider dropping deprecated formats in a major compatibility transition. A new minor release must not strand records written by a supported earlier minor.
7. **Isolate transactions.** Future format-changing writes are staged outside the authoritative source, verified against both source and target versions, then committed locally as an unreferenced candidate. A separate authorized host may CAS-publish it; on failure or concurrency conflict, the original ref and bytes remain authoritative. No forced history rewrite or silent rollback. Details: [Migration Transaction RFC](P1_MIGRATION_TRANSACTION_RFC.md).

### Do we need a new envelope or manifest immediately?

**Not necessarily.** Today `schema_version` exists on the documents and `engine_version` in checkpoints. First define a **reader capability table** mapping each of the 16 families and the checkpoint semantic epoch to its allowed decoder/writer/migration edges. That table is a compatibility *policy*, not part of an old signed snapshot.

A future on-disk `format_manifest` / version envelope could make mixed-format archives easier to inspect, but is **only a candidate**: it must be checked against strict runtime path allowlists, schema closure, canonical hashes, metadata bindings and Git publication semantics. Merely adding a file to the runtime tree would itself be a format change. Do not add the manifest or promise an automatic upgrade without a separate design+target-reader implementation.

## Reader/writer capability matrix to build early in P1

For **each** of the existing 16 families (see [machine-readable inventory](../oss/persistence-contract-inventory.json)) and each supported checkpoint semantic epoch, track:

`family`, `schema_id`, `source_format`, `oldest_readable`, `accepted_versions`, `writer_version`, `explicit_migration_edges`, `decoder`, `writer`, `canonical_hash_algorithm`, `unknown_field_policy`, `historic_goldens`, `rejected_future_versions`, `support_window`.

Initially all **future 1.x** reader/writer values in the existing manifest remain null until a concrete tested implementation and owner-approved writer target exist. The owner has chosen the **v0.1.0 read-compatibility outcome**, not fabricated a new target version or signed off on 1.x implementation today.

## Concrete sequence (P1, before a stable 1.0 declaration)

| Stage | Deliverable | Must prove |
| --- | --- | --- |
| E1 — Pin old evidence (done in PR #13) | Immutable 16-family v0.1.0 synthetic goldens, correction/capture/operator links, full SHA/canonical hashes | Current format is readable and failed inspection cannot alter the source; cross-process 6/6. |
| E2 — Exercise **new packaged reader** against the **old pinned goldens** | A separate wheel/sdist installed test that reads v0.1.0 as-is, validates all 16 families and links, and makes no writes to source | Both distribution types and at least two process environments; pin output hashes instead of regenerating fixtures. |
| E3 — Define explicit versioned compatibility capability table | Reviewed policy for old reader/new reader/new writer, clear unsupported-version errors and old `engine_version` identity | No source/file edits as a side effect of inspection; unknown future version remains rejected. |
| E4 — Prototype format-evolution mechanics **only on synthetic data** | Optional in-memory adapter, new-format candidate & schema/migration prototype if a real format change is needed | Separate target schema, old/new golden vectors, failure injection, clean rollback before publication. |
| E5 — Owner reviews **W1 versus W2** | Choose initial stable writer format after analyzing whether an envelope/migration earns its risk | If W1, record reason+future trigger; if W2, target reader/migration must pass before 1.0. |
| E6 — Stable v1 release-candidate gate | Public `snowfall_life` facade and CLI, 37 schema resource closure, pinned backward-read suite, Full/soak, 6/6, coverage review | No unapproved private data, no implicit Git publication; actual 1.x candidate reads v0.1.0 intact. |

**Do not treat E2 passing on a pre-v1 package build as proof of a future, as-yet-unbuilt 1.0 release.** Retest the final 1.0 candidate using the same frozen goldens.

## Long-term support commitment to propose for 1.x

- Newly released compatible 1.x readers should accept all **explicitly supported earlier 1.x** formats and the approved v0.1.0 source baseline. Forward reading by older packages is **not** automatically promised.
- No silent in-place migrations, no automatic removal of original checkpoints/history, no partial event-chain rewrites and no history-dropping fallback.
- Never change the canonical meaning/hashing of already finalized events without an explicit reviewed semantic migration.
- Every newly introduced persisted schema/epoch gets negative tests for unknown/future versions and documented deprecation/support conditions.
- A future version may require an explicit migration; that is acceptable if it is deterministic, auditable, complete and safe. “Future-resilient” means **the historical record stays recoverable**, not that every future binary can read every unknown format.

## Release hard stop

No `main` merge, actual Life-ref publication, release tag, stable Python API designation, mass schema-version bump, new persisted format declaration, or migration target selection is authorized by this evolution proposal. Source Draft PRs remain separate; the 1.0 release and any irreversible decisions require explicit review.
