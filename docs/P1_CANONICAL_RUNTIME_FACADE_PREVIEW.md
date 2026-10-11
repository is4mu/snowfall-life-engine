# P1 canonical RuntimeBundle facade — pre-1.0 preview

**Status:** owner-approved **basic feature scope**, but reversible Draft implementation / exact API whitelist/signatures **still review-only**. See [v1 scope decision](P1_V1_BASIC_API_SCOPE.md). Neither `snowfall_life.runtime`
nor `snowfall_life.persistence` is yet a **stable 1.x Python API**. The first
v1 writer **format W1 is already owner-selected**; *which Python symbols/signatures
become stable* is a different decision. Parent: Issue #3; persistence: #9/#16.

## Small exact namespace candidates

| Namespace | Explicit preview exports | Purpose |
| --- | --- | --- |
| `snowfall_life` | `ErrorCode`, `LifeEngineError`, `canonical_bytes`, `canonical_hash`, `canonical_json` | Existing small root; no runtime internals re-exported here |
| `snowfall_life.schema` | `load_schema_document`, `validate_instance` | Existing 37 included schema resources |
| `snowfall_life.runtime` | `RuntimeReferenceSets`, `RuntimeBundle`, `RuntimeTargetRequest`, `RuntimeFactRequestContext`, `RuntimeFactProvider`, `RuntimeDecisionProviderResult`, `RuntimeTargetAdvanceResult`, `RuntimeDecisionFacts`, `RuntimeDecisionFrame`, `RuntimeDecisionTrigger`, `RuntimeMaterializationContext`, `RuntimeActivityMaterializationFacts`, `RuntimeWakeupProjectionFacts`, `SocialResponseState`, `parse_runtime_target_request`, `advance_runtime_to_target_with_provider` | Inject host-approved, call-local deterministic facts; expose **annotation types used by all four callbacks** without forcing downstream use of engine.life; advance an **in-memory** runtime bundle |
| `snowfall_life.persistence` | `RuntimePersistentSnapshot`, `RuntimePersistenceResult`, `load_runtime_persistent_snapshot`, `build_runtime_candidate_tree_with_provider_factory` | **Read/verify** existing W1 snapshots; **stage** a deterministic provider-backed runtime candidate in a disjoint caller-owned directory |

The new modules are **direct identity aliases** of the existing implementation
entrypoints. There is no shadow simulator, no alternate format normalization,
no new exception translation and no changed runtime semantics. Names not in
`__all__` are implementation details. Direct use of unlisted `engine.life.*`
functions in synthetic bootstrap examples does **not** make them supported APIs.

## Exact call shapes (preview; API design review still required)

```python
load_runtime_persistent_snapshot(
    root: Path | str, *, reference_sets: RuntimeReferenceSets,
) -> RuntimePersistentSnapshot

advance_runtime_to_target_with_provider(
    *, bundle: RuntimeBundle, reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    social_response_state: SocialResponseState | Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider: RuntimeFactProvider,
) -> RuntimeTargetAdvanceResult

build_runtime_candidate_tree_with_provider_factory(
    *, baseline_root: Path | str, candidate_root: Path | str,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    target_request: RuntimeTargetRequest | Mapping[str, Any],
    fact_provider_factory: Callable[[RuntimePersistentSnapshot], RuntimeFactProvider],
    executing_engine_commit_sha: str, life_base_sha: str,
) -> RuntimePersistenceResult
```

The preview also exposes the callback's argument/result annotation types under the **same canonical runtime namespace**, so a downstream typed provider need not import `engine.life.runtime_decision`, `engine.life.activity_lifecycle`, `engine.life.activity_materialization`, or `engine.life.social_runtime` just to describe its signatures. These objects are identity aliases, not new copies.

`RuntimeFactProvider` has exactly four keyword-only callbacks:
`decision_inputs`, `materialization_facts`, `wakeup_facts`,
`capture_source_moments`. The host supplies all approved identity sets,
behavior policy and request inputs. The engine validates provider results,
chronology and persisted integrity; source application data must not be fetched
implicitly by the engine.

`RuntimeTargetRequest` requires exactly `target_time`, `event_budget`
and `character_source_sha`. Extra/missing fields, negative/boolean budgets
and invalid timestamps reject with `LifeEngineError(ErrorCode.INVALID_STATE)`.
The request context represents only immutable-by-contract call-local finalized
history deltas and returns detached copies, not a second history store.
Dataclasses are **shallow frozen**; nested mapping objects are not advertised
as universally deep-immutable. The provider must treat received state as
read-only and must not access external mutable authority during deterministic
callbacks.

## Persistence and side-effect boundary

The **loader** reads and verifies a complete W1 persisted tree, including
cross-document domain binding, ActualEvent history, logical capture links,
operator ledger, correction references and execution metadata when present.
It **does not write or migrate** the source. In a host application, use a
quiescent immutable source snapshot; fingerprints are **not locks**.

The **candidate builder** accepts a baseline and **separate** candidate root.
It validates the baseline before calling a provider factory, invokes the
factory **once**, writes only the disjoint candidate root, and fully reloads
and verifies the candidate on success. If the host returns malformed facts
or the source/target identity is inconsistent, it fails closed. It does
**not** commit to Git, update refs, publish, call LLM/media functions or
authorize production upgrades. Candidate staging is **not an atomic host
publish/rollback operation**. Do not equate a successful staged tree with a
verified Git CAS transaction.

Errors are the **existing** `LifeEngineError` carrying an `ErrorCode`,
e.g. `UNSUPPORTED_VERSION` for unknown engine versions or `INVALID_STATE`
for malformed runtime facts. Diagnostic `detail` messages are **not** yet
stable wire-format outputs.

## Compatibility / acceptance evidence

- Pre-v1 wheel and sdist install outside checkout (Python 3.12) and export
  the same objects and **unaltered signatures** as existing implementation.
  There are no new runtime dependencies or package-version bumps.
- The [synthetic provider consumer](../examples/provider_runtime/README.md)
  uses canonical runtime/persistence imports for real provider execution,
  stages **two identical** candidates from the same frozen baseline and
  verifies source bytes, output hashes and all four callbacks.
- The [v0.1.0 legacy reader probe](../examples/legacy_v010_reader/README.md)
  uses the preview canonical loader on immutable historic goldens. W1 real
  correction-writer/restart verification remains a separate contract.
- Contract tests ensure malformed input, future versions, in-place/nested
  candidates and provider failures **cannot silently rewrite** source data.
- CI must continue to run all 37 packaged schema imports, W1 frozen goldens,
  Provider, publication boundaries, Fast, Full + 72h/28d/90d, and the
  hashseed/timezone determinism matrix.

## Explicit next review gates

1. Approve exact export whitelist/signatures and which typed results should
   be public. Spatial data and narrow upgrade-host probe now have a **separate
   experimental preview** ([review](P1_SPATIAL_UPGRADE_FACADE_PREVIEW.md));
   operator correction/persistent Git candidate exports remain unapproved.
   **Do not expose** the mutable migration registry.
2. Finalize `engine.life` deprecation/alias window and Python support matrix.
3. Review [current CLI behavior and error gaps](P1_CLI_RC_REVIEW.md) and select the public stdout/stderr/error/exit semantics;
   current `snowfall-life advance` is the **Foundation sandbox**, *not*
   the provider-backed persistent runtime.
4. Build and install an **actual 1.0 RC** against unchanged v0.1.0 and W1
   goldens using only the **approved** canonical paths; run the exact-head
   Fast/Full/determinism/soak/coverage gates. Current package
   `0.2.0.dev0` is **not** that RC.
5. Explicit go/no-go for merging PRs, exposing stable API, tagging and
   releasing is separate. No `main` merge, production refs or real saves
   are changed by this preview.
