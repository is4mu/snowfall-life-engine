# P1 RFC — Safe migration transaction and publication/recovery boundary

**Status: REVIEW PROPOSAL, not an implemented migration, publication adapter, rollback command or stable API.** v0.1.0 read-compatibility and the narrow OperatorHistory legacy-read direction are accepted requirements; exact target engine/schema versions, initial writer format and implementation remain unset; see [Persistence Decision RFC](P1_V1_PERSISTENCE_DECISION_RFC.md), [Issue #9](https://github.com/is4mu/snowfall-life-engine/issues/9) and the [PR #13 preflight evidence](P1_PERSISTENCE_PREFLIGHT_EVIDENCE.md).

See also the [Persistence Evolution Design](P1_PERSISTENCE_EVOLUTION_DESIGN.md) for a future-proof format-version strategy. **Read compatibility is not the same as an automatic format rewrite:** when the reader can understand the old saved data, merely loading it must not enter the staged-migration state machine.

## Ground truth: existing authority boundaries

- `tools.persistence_preflight.inspect_persistence` validates an explicit **current->current** snapshot and returns evidence. It does **no** migration, stage, write, lock or ref update; its source fingerprints require external quiescence and are **not** a concurrency lock.
- `runtime_persistence.load_runtime_persistent_snapshot` verifies current-state/domain/timeline/camera/operator/metadata coherence. `build_runtime_candidate_tree*` operates on a **separate caller-provided candidate root**.
- `runtime_git_transaction.py` and `operator_git_transaction.py` use frozen local Git commit authority, exact blobs, object/commit integrity and fail-closed CAS/immutability validation, but return **unreferenced** candidates; their Git subprocess layer **forbids `update-ref`**. Existing operator *engine/policy upgrades* do not, by themselves, implement a new persisted-schema migration.
- The **consuming application** owns any live ref publication with expected-old-SHA CAS. Neither the OSS engine nor this RFC owns production authority, credentials, remote publishing or branch rewrite.

## Proposed explicit transaction state machine

| Phase | Authority and permitted side effect | Evidence and hard failure |
| --- | --- | --- |
| 0. `PLAN` | Declare *approved* source+target engine/schema tuples, exact engine SHA, behavior-policy ID, migration function identity, dry-run request ID, reference sets. **No writes**. | Reject absent approval/unsupported target, policy or source identity with documented errors. Current-only preflight cannot approve a new target. |
| 1. `FREEZE` | Quiesce an explicitly owned source; for Git storage, capture exact `HEAD`/tree/ref authority and immutable input blob map; for directory-only staging, require an external exclusive lock or immutable snapshot. | Reject dirty/nonlinear/incomplete Git input, symlinks/unknown authoritative paths, concurrent source change or mismatched expected base SHA. A byte fingerprint alone is not a lock. |
| 2. `READ` | Run schema+history/capture/operator correction preflight **read-only**, collect sealed source proof. | Fail closed on unknown versions, broken hashes, malformed files, linked correction errors; preserve original bytes even for corrupt input. |
| 3. `STAGE` | Materialize a temporary **owned directory outside** the authoritative source/worktree; run one approved deterministic migration `source_format -> target_format` on the **copy only**. For R1=A/W1, there may be **no migration step**. | Never auto-rewrite source, never let a provider touch external authority; cleanup only owned temporary paths. |
| 4. `VERIFY_TARGET` | Validate every target file with **target-version** schemas/reader, full event/capture/operator semantics, metadata bindings and deterministic digest vectors; compare source proof to ensure approved preservation rules. | Reject unproved relink/re-hash, dropped records, unapproved new paths, metadata mismatch, nondeterministic output or any immutable-input mutation. |
| 5. `BUILD_LOCAL_CANDIDATE` | Where needed, generate and independently verify an unreferenced local Git candidate commit with **one frozen parent**. This is **not publication**. | Exact expected blob set, candidate tree/commit bytes, parent and affected paths; before exiting, recheck original authority and source fingerprint. On failure leave HEAD/refs unchanged. |
| 6. `PUBLISH` (host-only) | A separately approved host, with credentials kept outside OSS, may publish the verified candidate using **compare-and-swap against the frozen base**; never force-push/rewrite or overwrite an unexpected current ref. | Atomic success is exact old SHA -> candidate SHA; CAS race rejects. A successful dry-run cannot authorize a later publish without repeating authority checks. |
| 7. `VERIFY_ACK` / `RECOVER` | Re-read the authoritative ref/commit after the publisher returns or times out. If it equals candidate SHA, record **already published** exactly once; if equals base SHA, it remains **not published** and may be retried after revalidation; otherwise **conflict**, do not rewrite. | Ambiguous network/ack results must never apply a second migration blindly. Preserve immutable evidence for retry/recovery. |
| 8. `ROLLBACK` policy | **Before publication:** discard unreferenced candidate/owned stage and keep source/ref untouched. **After publication:** do **not** rewind append-only facts or force-reset a public Life ref; any compensating forward action requires separate operator approval and historical consistency proof. | Document exact success/failed/unknown states. No automatic destructive rollback or rewriting corrected ActualEvents. |

No `update-ref` implementation is proposed in this OSS engine. Host publication and post-publication recovery remain separate review gates.

## Candidate evidence (future, illustrative names, **not** a schema/API)

An eventual immutable migration proof should identify:
`source_engine_version`, `target_engine_version`, per-family `source_schema_versions`/`target_schema_versions`, `behavior_policy_version`, `executing_engine_commit_sha`, `expected_life_base_sha`, `source_file_sha256`, `source_semantic_tree_hash`, `target_file_sha256`, `target_semantic_tree_hash`, `candidate_tree_sha`, `candidate_commit_sha`, `migration_id`, `files_changed` and a verified event/correction/capture-preservation certificate. Names here are **conceptual fields**, not an approved persistent document or a new exported Python class.

The proof must bind to the exact bytes executed. Because metadata files are non-semantic yet integrity-bound, compare **both** raw-file SHA sets and semantic-tree hashes; a matching semantic hash is not sufficient to authorize a different byte tree.

## Failure-injection acceptance matrix for an eventual implementation

| Injection point | Expected behavior |
| --- | --- |
| Unknown source/target schema, absent migration, future engine version | **Reject before stage**, do not invoke unapproved mutable migration registry, source bytes unchanged. |
| Corrupt operator hash, correction supersession, capture/event reference | **Reject read**, never fabricate a repair or silently truncate facts. |
| Source files or Git HEAD/ref change while reading/staging/probing | **Reject stale base**, source/ref bytes unchanged by engine; do not make a candidate authoritative. |
| Fault while creating staged copy, migration function raises, target validation fails | Only owned temporary artifacts may be cleaned; input and refs remain unchanged. |
| Identity/canonical hash mismatch, unapproved re-hash, altered closed day | **Reject target**, no publish. |
| Incorrect candidate blob/tree/parent, mutable index, dirty repo, SHA pin mismatch | **Reject local candidate**; verify refs, index and worktree remain unchanged. |
| Host CAS race after candidate generation | Report conflict, never force a ref. |
| Host publishes but response times out or acknowledgement is lost | Read authoritative ref before retry; equality to candidate means **exactly once** success, not re-publish. |
| Host ref diverges to unrelated commit | Report conflict; no automatic reset or rollback. |
| Process restarts at any pre-publication stage | Recompute source proof and resume idempotently only if exact authority matches; no secret or mutable state is assumed. |

Every deterministic phase must be exercised with the 0/1/8675309 × UTC/Asia-Tokyo matrix, fresh-process replay, pinned synthetic 16-family goldens and long-horizon tests. A test that copies the same version as itself does **not** establish old->new migration correctness. Full end-to-end **new target** vectors must be authored and reviewed only **after** target versions and compatibility policy are approved.

## Deliberate exclusions and implementation order

1. Apply owner direction: R1 accepts v0.1.0 readable history, R3 retains the narrow legacy exception; independently approve the *still undecided* W1/W2 first-writer format after evaluating the evolution design.
2. Define per-family target schema/version and migration identity **only if** the chosen writer requires a format change. Preserve old goldens as frozen origin evidence.
3. Implement pure target transformation and separately review the staged candidate creator, with failpoint tests; **not** as implicit `load_checkpoint()` mutation.
4. Add a minimal, documented and installed `snowfall_life` migration facade only after error, type and input/output review; the existing experimental `tools.persistence_preflight` is not that facade.
5. If live migration publication is needed, define separate host CAS and no-force recovery acceptance outside the OSS public runtime; obtain explicit publication/rollback approval.

This RFC does not add a v1 migration, stored-engine-version bump, ref update, release, Character Creator or private Snowfall application code.
