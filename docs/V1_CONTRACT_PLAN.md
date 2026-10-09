# Public v1 Contract Inventory and Stabilization Plan

Status: **review proposal**, 2026-10-08. This document is **not** a stable API declaration, a schema-migration implementation, or a change to the already-frozen functional boundary.

Authority: [V1_BOUNDARY.md](V1_BOUNDARY.md) (frozen responsibility boundary); [Issue #3](https://github.com/is4mu/snowfall-life-engine/issues/3) (remaining readiness work). The `v0.1.0` release is the semantic reference baseline, not a promise that all importable `engine.life.*` modules are public API.

For pending 1.0 approval decisions, CI evidence and Draft PR merge dependencies, see [P1 v1 Readiness Decision Matrix](P1_V1_READINESS_DECISION_MATRIX.md). It records review options, not accepted API promises.

## Detailed contract reviews

- [Minimal Python/CLI facade proposal](V1_PUBLIC_API_PROPOSAL.md)
- [Persistence schema and migration compatibility matrix](V1_PERSISTENCE_COMPATIBILITY.md)
- [Runtime fact, spatial, and upgrade adapter contracts](V1_ADAPTER_CONTRACTS.md)

These remain proposals; the v0.1.0 implementation is not automatically a stable v1 API.

## 1. Four identities must remain separate

| Identity | Current observed value | v1 rule / disposition |
| --- | --- | --- |
| Git release tag | `v0.1.0`, release commit `a274470dcc1b6a2f22c1ae74df19d94d66a21f34` | An OSS publication identifier; it does **not** imply stable 1.0 API. |
| Persisted engine contract | `ENGINE_VERSION = "0.1.0-foundation"` and `SUPPORTED_ENGINE_VERSIONS` in `engine/life/__init__.py` | Separately versioned serialized-state compatibility gate. Do not rename existing persisted values by changing a constant alone. |
| JSON document schema | Per-document `schema_version` values, plus `SUPPORTED_SCHEMA_VERSION = 1` in the foundation package | Schema versions are **not** package SemVer. Each persisted family needs its own compatibility/migration matrix. |
| Python/CLI distribution identity | `engine.life`, `python -m engine.life[.cli]`; no canonical `snowfall_life` distribution yet | `snowfall_life` / `snowfall-life` is the already-selected pre-1.0 target. Specify any compatibility alias and end date before 1.0. |

**Release sequencing:** inventory existing state readers/writers -> specify migrations and consumer compatibility -> implement/contract-test canonical package/CLI -> then decide 1.0 identity/version bump. Do not change stored `engine_version`, canonical IDs or hashes as a cosmetic cleanup.

## 2. Surface classifications

- **Stable (v1 only):** explicitly documented facade symbols, serialized formats, codes and CLI contracts shipped with a `1.0.0` release and guarded by compatibility tests. **No `v0.1.0` implementation import is being promoted to stable by this document.**
- **Experimental (current 0.x):** importable implementations and CLI commands below, available for early synthetic consumers but subject to pre-1.0 redesign and migration.
- **Internal:** modules, private helpers, orchestration wiring, algorithm details, test builders, and implementation layout not explicitly exposed by a documented public facade. Importability alone is not a compatibility guarantee.

Proposed *v1 contract ownership* and source anchors (exact facade names and signatures require a separate focused review):

| ID | Planned v1 contract | Existing source/test anchor | Explicit open decision |
| --- | --- | --- | --- |
| C01 | Deterministic advance, explicit timestamps/seed and restart equivalence | `clock.py`, `runtime_orchestrator.py`, `tests/soak/test_restart_equivalence_72h.py` | Choose one minimal public advance/session facade and result type, separate from orchestrator internals. |
| C02 | Append-only ActualEvents, correction/recovery, checkpoint/history integrity | `checkpoint.py`, `history.py`, `runtime_persistence.py`, `runtime_bundle.py` | Name the persisted artifact families, directory layout and approved migration entrypoints. |
| C03 | Canonical serialization, IDs, hashes and stable error-code meaning | `canonical.py`, `ids.py`, `errors.py` (`ErrorCode`, `LifeEngineError`) | Specify which exported functions and error codes become facade contracts. Diagnostic message text need not be stable. |
| C04 | Versioned schema validation | `schema.py` with 37 registered public JSON schemas | Distinguish persistent, input, output and operator schemas; publish a per-family migration matrix. |
| C05 | Call-local runtime fact injection | `runtime_fact_provider.py`: `RuntimeFactProvider`, `RuntimeTargetRequest`, `RuntimeFactRequestContext`, `RuntimeDecisionProviderResult` | Publish protocol signatures, ownership, purity/no-I/O rules and synthetic conformance tests. |
| C06 | Spatial/route authority and projection | `spatial_context.py`: normalize/build/validate; `runtime_spatial.py`: route projection | Specify one supported input facade, hash/identity rules, and no reverse-route or external map inference. |
| C07 | Local upgrade integrity and injected host adapter | `operator_upgrade.py`: `UpgradeEnvironmentAdapter`, `CheckoutPolicyMaterial`, `CompatibilityProbeResult` | Contract-test method signatures, exact SHA pin, clean checkout, immutable input and fail-closed probes; keep production wiring private. |
| C08 | Public CLI and machine-facing output/errors | `cli.py`, `__main__.py` | Choose canonical command invocation, structured output, exit codes and any compatibility alias. |
| C09 | Minimal Python entry facade | `engine/life/__init__.py` currently exports version/errors/canonical helpers | Define a small explicit `snowfall_life` export table; do **not** freeze all ~66 importable module paths. |
| C10 | Local operator transaction/CAS and logical capture records | `operator_git_transaction.py`, `runtime_git_transaction.py`, `camera_roll.py`, capture schemas | Freeze data/integrity semantics, not private remote-publication or media-rendering behavior. |

### Current CLI inventory (experimental)

The current `python -m engine.life.cli` parser defines exactly these commands: `validate`, `canonical-hash`, `bootstrap-propose`, `advance`, `verify-workspace`, `self-check`, and `init-sandbox`.

The implementation currently returns 0 on successful command execution and 2 on a caught `LifeEngineError`, writing its code to stderr; `argparse` handles argument failures separately. Unhandled errors and human-readable prose are **not yet** a versioned output/exit contract. Stabilization should prefer machine-readable results, a documented stderr/error-code rule, and a synthetic end-to-end example. Do not claim `snowfall-life` is already installed or production-ready.

### Public adapter invariants to test

- Fact-provider methods (`decision_inputs`, `materialization_facts`, `wakeup_facts`, `capture_source_moments`) receive frozen/explicit context and have no live network, wall-clock or private authority dependency.
- Spatial context is approved input; routes are not invented by reverse inference or supplied from conflicting caller authority. Projection preserves full validated route-set semantics.
- Upgrade adapter implements `load_checkout_policy_material`, `materialize_pinned_engine_checkout`, `cleanup_materialized_checkout` and `probe_target_compatibility`. Engine independently validates SHA identity, policy hashes, compatibility evidence and source/target immutability.

These are contract *candidates*, not new implementation requirements beyond the frozen functional scope.

## 3. Proposed persistence compatibility policy for 1.x (not yet implemented)

The current `versioning.py` has a migration registry but no registered migrations. `load_checkpoint()` calls `migrate_state()`; unsupported `engine_version` fails closed. A version-number bump without a tested migration would break existing stored checkpoints.

Proposed v1 rules, subject to approval in Issue #3:

1. **Readability:** each 1.x reader must either accept a persisted 1.x artifact it previously wrote or provide an explicit deterministic, lossless migration path. If migration is impossible, fail with a documented compatibility error; never silently coerce or discard history.
2. **Safe migration:** operate on a copy/staging location, validate the target schema and semantic history, preserve the source bytes until a successful verified commit, and record origin/target format/version. No implicit in-place rewrite during mere inspection.
3. **History invariants:** preserve ActualEvent ordering, identities, finalized meaning, correction/recovery lineage, canonical hash verification and restart equivalence. If a format change necessarily changes hashes, use an explicit, reviewed re-hash/relink procedure instead of quietly reserializing history.
4. **Version gates:** separate engine semantic version, per-artifact schema version, behavior-policy version and exact engine commit SHA. On an unsupported future/unknown format, fail closed (`UNSUPPORTED_VERSION` or a documented public compatibility code).
5. **Reader/writer matrix:** list each persisted family (at minimum current-state, actual-event/timeline, runtime world/schedule state, captures, operator history) with current schema ID/version, owning loader/writer, oldest supported version, migration path and golden fixtures.
6. **Regression evidence:** run old->new migration plus uninterrupted vs restart equivalence and cross-version hash/invariant tests. Keep the `v0.1.0` baseline fixtures immutable as provenance; **do not** claim automatic compatibility from v0.x to 1.x before the gateway is implemented.

Proposed SemVer interpretation after 1.0: PATCH repairs within documented semantics; MINOR adds backward-compatible public surface/schema with a reader/migration guarantee; MAJOR may remove deprecated contracts after an announced transition. Policy tuning must be explicit and must not silently redefine deterministic invariants.

## 4. CLI, Python, and dependency boundary gates

- Freeze the **documented facade** only, including signatures, exceptions and result representations; internal `engine.life.*` imports are not automatically stable.
- Implement/test `snowfall_life` and `snowfall-life` **before** 1.0; record whether `engine.life` remains as an alias, its warning policy and removal window. Test both if an alias is promised.
- Resolve package metadata, wheel/sdist inclusion of all registered schemas, offline installation and a clean virtual-environment self-check.
- v0.1.0 CI is Python **3.12 only**. The v1 supported interpreter window remains a decision, not an implied 3.9+ promise.
- Keep core tests synthetic, deterministic and network-free. Application adapters own private character data, image/LLM pipelines, live publication and credentials.
- Preserve the accepted Run #126 coverage baseline (statement 80.84%; branch 64.78%; combined 75.93%), investigate regressions, and adopt any numeric ratchet only after meaningful denominator review.

## 5. Definition of Done for P1 boundary stabilization

- [x] Published v0.1.0 baseline and provenance verified.
- [x] Functional ownership, exclusions and canonical future package/CLI name frozen in `V1_BOUNDARY.md` (merged PR #4).
- [ ] Review this **proposed** contract inventory; explicitly approve the minimal facade and stability classifications.
- [ ] Approve persistence compatibility policy, reader/writer schema matrix and migration/version policy.
- [ ] Approve CLI command/output/error contract and `engine.life` alias/deprecation policy.
- [ ] Document and contract-test fact, spatial and upgrade adapter protocols.
- [ ] Implement canonical packaging, synthetic consumer example and supported Python window.
- [ ] Add end-to-end schema/migration/CLI/facade contract tests without application-private data.
- [ ] Review coverage ratchet and pass publication, import/schema closure, Fast, Full and 6/6 determinism validation on a 1.0 release candidate.
- [ ] Obtain explicit human approval **before** declaring any API stable, merging a stabilization PR, tagging or publishing 1.0.

P1 does **not** add new character features or change existing simulator behavior. Character Creator and the Snowfall application remain out of scope until this boundary and the readiness handoff are deliberately accepted.
