# v1 Public API and CLI Proposal

Status: **review proposal**, not a stable v1 declaration. This elaborates [V1_BOUNDARY.md](V1_BOUNDARY.md) and [V1_CONTRACT_PLAN.md](V1_CONTRACT_PLAN.md).

## Decisions grounded in the current implementation

- The public release `v0.1.0` currently imports as `engine.life`. The planned canonical Python package and CLI remain `snowfall_life` / `snowfall-life` **before** 1.0.
- `engine.life.__init__` currently exports `ENGINE_VERSION`, `SUPPORTED_ENGINE_VERSIONS`, `SUPPORTED_SCHEMA_VERSION`, `ErrorCode`, `LifeEngineError`, `canonical_bytes`, `canonical_hash`, `canonical_json`. Importability of other modules does **not** imply compatibility.
- `clock.advance` drives the *foundation sandbox workspace*; `runtime_orchestrator.advance_runtime_to_target_with_provider` drives an in-memory RuntimeBundle; `runtime_persistence.build_runtime_candidate_tree_with_provider_factory` stages a persistent candidate. These are **different** operations; do not accidentally freeze the foundation CLI as the full simulation interface.
- The stacked pre-v1 candidate now has `pyproject.toml` (`0.2.0.dev0`) and installs schemas under `engine/life/_schemas`; isolated wheel/sdist checks verify all 37 schemas. Canonical root/schema/CLI wrappers **and now the proposed runtime/persistence aliases** are implemented experimentally; spatial/operator/upgrade remain review inputs. See [W1/W2 comparison and actual-v1 RC gates](P1_W1_W2_COMPARISON_AND_RC_GATES.md).

## Initial persistence writer selection — approved design only

The owner has selected **W1** as the first stable 1.0 writer format.
The persisted `engine_version` remains `0.1.0-foundation`, with existing
schema-version-1 files for all 16 persisted families. The distribution may
independently be versioned `1.0.0`. Valid v0.1.0 saves remain a required
reader input and must never be silently rewritten. W2 is deferred unless a
later concrete incompatibility warrants a separately approved migration.

See [the initial writer decision](P1_W1_INITIAL_WRITER_DECISION.md).
This does **not** finalize canonical Python/CLI exports, errors, support
windows or stable 1.0 behavior; those gates remain open.

## Candidate v1 facade (names under review)

| Intended documented namespace | Proposed exports / role | Implementation anchors |
| --- | --- | --- |
| `snowfall_life` | `ErrorCode`, `LifeEngineError`, `canonical_bytes`, `canonical_hash`, `canonical_json`; decide whether `persisted_hash` is a root symbol | `errors.py`, `canonical.py` |
| `snowfall_life.schema` | `validate_instance`, `load_schema_document`; documented schema-name table | `schema.py`, 37 registry entries |
| `snowfall_life.runtime` | **Preview implemented:** `RuntimeReferenceSets`, `RuntimeBundle`, `RuntimeTargetRequest`, `RuntimeFactRequestContext`, `RuntimeFactProvider`, `RuntimeDecisionProviderResult`, `RuntimeTargetAdvanceResult`, `RuntimeDecisionFacts`, `RuntimeDecisionFrame`, `RuntimeDecisionTrigger`, `RuntimeMaterializationContext`, `RuntimeActivityMaterializationFacts`, `RuntimeWakeupProjectionFacts`, `SocialResponseState`, `parse_runtime_target_request`, `advance_runtime_to_target_with_provider` | Exact aliases to runtime/provider/context/decision/materialization/social classes; makes all four Provider callback annotations importable via canonical namespace |
| `snowfall_life.persistence` | **Preview implemented:** `RuntimePersistentSnapshot`, `RuntimePersistenceResult`, `load_runtime_persistent_snapshot`, `build_runtime_candidate_tree_with_provider_factory` | Exact aliases to `runtime_persistence.py`; reader verifies source, builder stages disjoint candidate only |
| `snowfall_life.spatial` | Build/validate sealed SpatialContext; explicitly project approved routes | `spatial_context.py`, `runtime_spatial.py` |
| `snowfall_life.upgrade` | Injected `UpgradeEnvironmentAdapter`, `CheckoutPolicyMaterial`, `CompatibilityProbeResult` and verified local upgrade candidate operation | `operator_upgrade.py` |

**Recommendation:** keep root exports small and separate runtime, persistence, spatial, and optional operator facades. See [the runtime/persistence preview contract](P1_CANONICAL_RUNTIME_FACADE_PREVIEW.md) for currently implemented (not stabilized) symbols, errors and authority limits. Define a reviewed whitelist of signatures/return types before any 1.0 declaration. Do **not** stabilize all ~66 implementation modules, private workflow wiring, or the unrestricted mutable `versioning.register_migration` registry.

## CLI inventory and stabilization rules

Current invocation: `python -m engine.life.cli` (also `python -m engine.life`). Commands: `validate`, `canonical-hash`, `bootstrap-propose`, `advance`, `verify-workspace`, `self-check`, `init-sandbox`.

The current CLI returns 0 on success, 2 for caught `LifeEngineError` (and `argparse` uses exit 2 for argument errors). stdout is **not uniformly JSON**: `advance` prints JSON, while hash/validation/self-check commands print text. Neither diagnostic prose nor undocumented exceptional exits are stable wire contracts today.

Before v1: document each canonical `snowfall-life` command's required flags, stdout/stderr, error-code meaning and exit status. Keep `bootstrap-propose`/`init-sandbox` synthetic-only. Decide whether the old `engine.life` invocation has a tested alias and a deprecation/removal window. Do not call the foundation `advance` command a full provider-driven life runtime.

## Packaging acceptance gate

Build wheel and sdist; install each into clean Python 3.12 environments; import exactly the documented facade whitelist; validate/load **all 37** bundled schemas independently of the repository checkout; run `snowfall-life self-check`; confirm no private application modules/data are packaged. The initial supported Python floor is **proposed** as 3.12 because only that interpreter is presently proven by public CI; a wider supported window requires an explicit test matrix and approval.

All of these are pre-1.0 tasks. The stacked candidate implements experimental packaging/root/schema/CLI wrappers; this proposal does not change stored `engine_version` or establish a stable API.
