# v1 Public API and CLI Proposal

Status: **review proposal**, not a stable v1 declaration. This elaborates [V1_BOUNDARY.md](V1_BOUNDARY.md) and [V1_CONTRACT_PLAN.md](V1_CONTRACT_PLAN.md).

## Decisions grounded in the current implementation

- The public release `v0.1.0` currently imports as `engine.life`. The planned canonical Python package and CLI remain `snowfall_life` / `snowfall-life` **before** 1.0.
- `engine.life.__init__` currently exports `ENGINE_VERSION`, `SUPPORTED_ENGINE_VERSIONS`, `SUPPORTED_SCHEMA_VERSION`, `ErrorCode`, `LifeEngineError`, `canonical_bytes`, `canonical_hash`, `canonical_json`. Importability of other modules does **not** imply compatibility.
- `clock.advance` drives the *foundation sandbox workspace*; `runtime_orchestrator.advance_runtime_to_target_with_provider` drives an in-memory RuntimeBundle; `runtime_persistence.build_runtime_candidate_tree_with_provider_factory` stages a persistent candidate. These are **different** operations; do not accidentally freeze the foundation CLI as the full simulation interface.
- The source tree has no `pyproject.toml`. `schema.py` currently looks for `schemas/life` using repository-relative `Path(__file__).resolve().parents[2]`, so wheel/sdist packaging needs a verified resource relocation or equivalent supported resource discovery.

## Candidate v1 facade (names under review)

| Intended documented namespace | Proposed exports / role | Implementation anchors |
| --- | --- | --- |
| `snowfall_life` | `ErrorCode`, `LifeEngineError`, `canonical_bytes`, `canonical_hash`, `canonical_json`; decide whether `persisted_hash` is a root symbol | `errors.py`, `canonical.py` |
| `snowfall_life.schema` | `validate_instance`, `load_schema_document`; documented schema-name table | `schema.py`, 37 registry entries |
| `snowfall_life.runtime` | `RuntimeReferenceSets`, `RuntimeBundle`, `RuntimeTargetRequest`, `RuntimeFactRequestContext`, `RuntimeFactProvider`, `RuntimeDecisionProviderResult`, `RuntimeTargetAdvanceResult`; provider-backed target advancement | `runtime_bundle.py`, `runtime_fact_provider.py`, `runtime_orchestrator.py` |
| `snowfall_life.persistence` | Read/verify a persisted runtime snapshot; stage a verified runtime candidate with a fact-provider factory, not an implicit in-place write | `runtime_persistence.py` |
| `snowfall_life.spatial` | Build/validate sealed SpatialContext; explicitly project approved routes | `spatial_context.py`, `runtime_spatial.py` |
| `snowfall_life.upgrade` | Injected `UpgradeEnvironmentAdapter`, `CheckoutPolicyMaterial`, `CompatibilityProbeResult` and verified local upgrade candidate operation | `operator_upgrade.py` |

**Recommendation:** keep root exports small and separate runtime, persistence, spatial, and optional operator facades. Define a reviewed whitelist of signatures/return types before any 1.0 declaration. Do **not** stabilize all ~66 implementation modules, private workflow wiring, or the unrestricted mutable `versioning.register_migration` registry.

## CLI inventory and stabilization rules

Current invocation: `python -m engine.life.cli` (also `python -m engine.life`). Commands: `validate`, `canonical-hash`, `bootstrap-propose`, `advance`, `verify-workspace`, `self-check`, `init-sandbox`.

The current CLI returns 0 on success, 2 for caught `LifeEngineError` (and `argparse` uses exit 2 for argument errors). stdout is **not uniformly JSON**: `advance` prints JSON, while hash/validation/self-check commands print text. Neither diagnostic prose nor undocumented exceptional exits are stable wire contracts today.

Before v1: document each canonical `snowfall-life` command's required flags, stdout/stderr, error-code meaning and exit status. Keep `bootstrap-propose`/`init-sandbox` synthetic-only. Decide whether the old `engine.life` invocation has a tested alias and a deprecation/removal window. Do not call the foundation `advance` command a full provider-driven life runtime.

## Packaging acceptance gate

Build wheel and sdist; install each into clean Python 3.12 environments; import exactly the documented facade whitelist; validate/load **all 37** bundled schemas independently of the repository checkout; run `snowfall-life self-check`; confirm no private application modules/data are packaged. The initial supported Python floor is **proposed** as 3.12 because only that interpreter is presently proven by public CI; a wider supported window requires an explicit test matrix and approval.

All of these are pre-1.0 tasks. This document does not implement a package rename, change stored `engine_version`, or establish a stable API.
