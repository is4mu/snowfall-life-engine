# v1 Adapter Contract Proposal

Status: **review-only implementation inventory**, not a stable 1.0 API. App-specific facts/authority and production workflows must stay outside this repository. See [V1_BOUNDARY.md](V1_BOUNDARY.md).

## Runtime fact-provider boundary

Current source: `engine/life/runtime_fact_provider.py`. `RuntimeFactProvider` is a typing `Protocol` with four methods:

- `decision_inputs(*, bundle, social_response_state, trigger, behavior_policy, reference_sets, target_request, request_context) -> RuntimeDecisionProviderResult`
- `materialization_facts(*, bundle, social_response_state, frame, context, decision_facts, behavior_policy, reference_sets, target_request, request_context) -> RuntimeActivityMaterializationFacts | Mapping`
- `wakeup_facts(*, bundle, active_activity, as_of, behavior_policy, reference_sets, target_request, request_context) -> RuntimeWakeupProjectionFacts | Mapping`
- `capture_source_moments(*, bundle, active_activity, from_time, target_time, character_source_sha, target_request) -> Sequence[Mapping]`

`RuntimeTargetRequest` requires exactly `target_time`, `event_budget`, and `character_source_sha`. Parser rules: canonical timestamp, true nonnegative integer budget (not bool), nonempty source string, no missing/extra keys. The parser does **not** currently require the source string to match 40-hex; do not invent such a guarantee.

`RuntimeFactRequestContext` carries only ActualEvents finalized since the provider's frozen persistent base, preserving event order. Its context hash and returned copies must not permit mutation of the call-local history. During a simulation invocation provider outputs must depend only on approved explicit inputs, not network/Git/wall-clock or alternative private state. An application can load its own private inputs before invoking the engine.

**Contract tests required:** type/missing/extra-key rejection, canonical time, context hash/delta isolation, determinism for equivalent requests, no live-network dependencies, no private source imports.

## Spatial boundary — validated data, not a `Protocol`

Source: `spatial_context.py`, `runtime_spatial.py`. `build_spatial_context` creates a sealed mapping with a semantic `context_hash`; `validate_spatial_context` checks identity, version, provenance, unique IDs, route authority, strict integers and hash without mutating caller data. `project_spatial_runtime_decision_facts` and `project_spatial_runtime_target_inputs` project the **complete approved route set**; caller-supplied competing `route_profiles` are rejected. No reverse-route inference, transit/geocoding API, graph reachability or private production geography belongs to this public engine.

**Contract tests required:** deterministic normalization, no mutation, malformed hash or IDs, bool-as-int, duplicated/ambiguous routes, caller route override rejection, full validated route preservation. Do not invent an unsupported `SpatialAdapter` class.

## Upgrade host boundary

Source: `operator_upgrade.py`; `UpgradeEnvironmentAdapter` is a typing `Protocol` with four methods:

1. `load_checkout_policy_material(checkout: Path) -> CheckoutPolicyMaterial`
2. `materialize_pinned_engine_checkout(*, engine_source_repo: Path, engine_commit_sha: str, work_root: Path) -> Path`
3. `cleanup_materialized_checkout(*, engine_source_repo: Path, checkout: Path) -> None`
4. `probe_target_compatibility(*, target_checkout: Path, target_engine_commit_sha: str, life_files: Mapping[str, bytes]) -> CompatibilityProbeResult`

The engine independently proves exact commit pin, checkout cleanliness, policy identity/hash, target compatibility evidence and source/target Life immutability; an incomplete adapter fails closed. Private host-side policy loading and execution may exist outside this repository; production authority, launchers, credentials and remote publication may not be imported as a dependency.

**Contract tests required:** absent methods, wrong output types, wrong target SHA/policy hash, dirty checkout, incompatible probe result, any Life tree mutation during read-only probe, safe cleanup on success/failure. Keep existing synthetic operator integration coverage.

Only explicitly selected future `snowfall_life.runtime`, `snowfall_life.spatial` and `snowfall_life.upgrade` facade imports could become stable. Current `engine.life` implementation paths remain experimental.
