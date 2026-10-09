# P1 W1/W2 comparison and reviewed-API / actual-v1 RC gates

Status: selection material, 2026-10-09. **No first-v1 writer or stable API is
selected.** Refs #9, #16. The owner-approved order is W1 write/reload → synthetic
W2 migration/recovery → comparison → API/RC preparation. Main remains the
released baseline; this is a stacked Draft evidence layer.

## Executable comparison

| Review dimension | W1 actual current-format writer | W2 synthetic transaction laboratory |
| --- | --- | --- |
| Concrete operation | Existing engine correction writer appends action 3 and revision 3; creates a verified unreferenced commit and exports/reloads its actual blobs. Installed provider consumer separately covers simulation writes. | Transforms the 15-file/16-family synthetic archive into ordered document records in a detached lab container, serializes it, independently decodes/validates exact original bytes, creates a local unreferenced candidate and simulates ack recovery. |
| Historical evidence | Raw event chain, closed timeline/camera shard and prior action blobs stay fixed; new correction changes the effective overlay only. | All raw documents and metadata decode byte-for-byte; original semantic/history/operator/effective hashes are identical. |
| Versions and paths | Existing epoch `0.1.0-foundation`, all family versions 1; strict runtime paths preserved. | `TEST_ONLY_W2_CONTAINER_V2` is an experimental transport identifier outside runtime paths. It is **not** an engine epoch, schema version, registered migration or approved envelope. Current engine deliberately cannot load it as a save. |
| Candidate/restart | One frozen parent, pinned Git commit/tree and every raw digest, fresh-interpreter reload, repeated source replay. | Pinned source/target digests, exact-parent/tree/blob verification and deterministic proof recreated after restart. All stage/object files are owned temporaries. |
| Failure behavior | Future/corrupt committed input or invalid correction rejects; source worktree/index/HEAD/refs stay unchanged. | Copy/transform/object I/O failures, six interruption/invalid-target points, unknown shape/path/duplicate/missing/reordered/rehash inputs and stale source all reject; owned temporary paths are removed. |
| Lost ack/CAS | Existing writer creates no publication. | Pure **simulated** host observations: candidate → ALREADY_PUBLISHED; base → NOT_PUBLISHED; unrelated → CONFLICT. No ref is read/updated, no retry or rollback is authorized. Self-resealed altered receipts reject after recomputation. |
| Determinism/distributions | Six source-process environments plus installed wheel/sdist × six environments compare pinned writer report. | Six fresh-process environments compare transformation and restarted recovery proof. Lab tools are excluded from distributions. |
| Remaining format work | Guard every future semantic change under the same persisted epoch; approve writer/support window and facade. | Define an actual target schema/epoch and dual reader, any required historical rekey/relink and metadata resealing; then run new target-schema vectors. A transport experiment does **not** prove a production schema migration. |

**Provisional recommendation remains W1.** The demonstrated W1 operation adds
history without format churn, and there is still no approved incompatible data
requirement that earns W2's extra reader/migration burden. W2 laboratory evidence
supports transaction and recovery design, but does not erase the missing
production-format gates. Revisit W2 when a concrete required data-shape change
cannot be expressed under the reviewed existing schemas. Never silently add
fields under existing format identities.

## W2 laboratory scope and reproducibility

`tools.w2_synthetic_lab` accepts only a source whose complete preflight report
matches the pinned public v0.1.0 synthetic archive. Semantically identical but
byte-different input is rejected. It never accepts arbitrary real saves.

The lab writes its alternate representation, decoded tree, isolated indexes and
bare Git object database only inside one owned temporary directory. Both base
and candidate commits have no containing refs; bare HEAD remains unborn. The
candidate object is verified before temporary cleanup; the retained JSON report
is deterministic evidence, **not a durable publishable Git object**. Failures
and restarts rerun from the unchanged synthetic source; there is no resume
journal or automatic publication. Fingerprints detect ordinary source races,
not filesystem locks or an atomic snapshot. Host CAS remains unimplemented.

Run `python -m tools.w2_synthetic_lab --source
 tests/fixtures/golden/persistence-v0.1.0/runtime` (one command, no newline).
Tests: `python -m pytest -q tests/contracts/persistence/test_w2_synthetic_lab.py`.
Pinned report: `tests/fixtures/golden/w2-lab.experimental.json`.

## Current facade inventory and exact review inputs

The existing distribution is `0.2.0.dev0`, **not an actual 1.0 RC**. Current
implemented canonical namespaces are:

| Namespace / operation | Implemented today | Review decision still needed |
| --- | --- | --- |
| `snowfall_life` | `ErrorCode`, `LifeEngineError`, `canonical_bytes`, `canonical_hash`, `canonical_json` | Small root whitelist; hashes' intended canonicalization and error contract. |
| `snowfall_life.schema` | `validate_instance`, `load_schema_document` | Supported schema names/versions and return/error promises. |
| `snowfall-life` / `python -m snowfall_life` | Existing foundation CLI delegation, self-check and sandbox commands | Per-command stdout/stderr, exit status and error protocol; full-runtime vs sandbox responsibility. |
| Runtime/persistence/operator namespaces | Implementation entrypoints below are importable; canonical wrappers are not yet implemented | Review names, input ownership, provider callback protocol, detached/immutable return types, cleanup and authority boundary before exporting wrappers. |
| Migration/publication | Checkout-only preflight and synthetic lab; no canonical migration facade, no W2 production decoder or publication host | Do not expose an unrestricted registry or promote test tools into a stable API. |

Current implementation signatures used as review inputs:

```python
load_runtime_persistent_snapshot(root: Path | str, *,
    reference_sets: RuntimeReferenceSets) -> RuntimePersistentSnapshot

build_runtime_candidate_tree_with_provider_factory(*,
    baseline_root: Path | str, candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets, behavior_policy: Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider_factory: Callable[[RuntimePersistentSnapshot], RuntimeFactProvider],
    executing_engine_commit_sha: str, life_base_sha: str) -> RuntimePersistenceResult

build_operator_correction_git_transaction(*, runtime_repo: Path | str,
    request: Mapping[str, Any], reference_sets: RuntimeReferenceSets
) -> OperatorGitTransactionResult
```

These signatures are **implementation evidence, not stable exports**. The W1
installed probe currently uses `engine.life` entrypoints; it cannot substitute
for a test through the eventually reviewed canonical persistence facade.

## Actual-v1 release-candidate checklist (pending approval)

1. Owner reviews W1/W2 initial writer, persisted epoch/family bounds, reader
   support/deprecation policy and canonical Python/CLI whitelist.
2. Implement the selected reversible facade proposal on a dedicated Draft,
   preserving runtime behavior; contract-test ownership, errors, provider
   callback/type protocol, unsupported version rejection and no implicit writes.
3. Prepare an **actual approved 1.0 release-candidate artifact** with package
   identity distinct from persisted epoch. Record exact source SHA and artifact
   hashes; never relabel a pre-v1 result as an actual-v1 test.
4. Install that wheel and sdist outside checkout on supported Python 3.12;
   rerun unchanged v0.1.0 and W1 pinned reports through the reviewed facade,
   all 37 bundled schemas, provider simulation and six environment replays.
5. Rerun Fast/Full, 72h restart, 28d structural and 90d invariants on the exact RC
   head. If W2 is selected, add actual old/new schemas, dual-reader/migration
   vectors and separately approved host-CAS durability/recovery checks.
6. Review compatibility, API and RC evidence; stop before merge, tag, release,
   production-ref update or real-save rewrite until explicit owner approval.

The current pre-v1 CI prepares steps 4–5; it **does not complete** step 3. P1 is
still open. P0 and Character Creator are outside this work.
