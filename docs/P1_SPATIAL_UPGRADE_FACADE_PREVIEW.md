# P1 SpatialContext and upgrade-host facades (pre-v1 review)

**Owner scope choice (2026-10-10):** spatial data validation/projection is part of the initial 1.0 basic API target; the upgrade-host probe remains **experimental/importable but not stable in 1.0**. This Draft adds two **small identity-alias subfacades** on top of the
[canonical runtime/persistence preview](P1_CANONICAL_RUNTIME_FACADE_PREVIEW.md).
Neither is an approved stable v1 API, a live host adapter, or an authorization
to publish/write the production Life ref. All inputs are synthetic in tests.

## `snowfall_life.spatial` — validated spatial **data**

- `build_spatial_context`
- `validate_spatial_context`
- `project_spatial_runtime_decision_facts`
- `project_spatial_runtime_target_inputs`

A SpatialContext is a sealed mapping (not a new `SpatialAdapter` protocol).
The context owns approved complete route profiles. Callers are **not allowed**
to supply competing `route_profiles` during projection, even an empty route
array; nor may they mutate the original context by computing a different hash.
Duplicate or mismatched ID/character authority, invalid hash and invalid
numeric types must fail closed. No geocoding, API, map discovery, reverse
route inference, external credentials, real character locations or host
framework is exported.

## `snowfall_life.upgrade` — host-injected **read-only probe**

- `UpgradeEnvironmentAdapter` (existing four-method `Protocol`)
- `CheckoutPolicyMaterial` (typed host-supplied approved material)
- `CompatibilityProbeResult` (typed read-only evidence)
- `run_target_compatibility_probe` (engine-side validation)

The four callbacks are `load_checkout_policy_material`,
`materialize_pinned_engine_checkout`, `cleanup_materialized_checkout`,
`probe_target_compatibility`. Exact public protocol method signatures
are retained as aliases to existing implementation, **not re-specified**.
The host supplies pinned local checkouts, approved policy and the read-only
target probe. The engine verifies expected commit identities, checks checkout
cleanliness and ensures Life source files are unchanged. An incomplete host
adapter or invalid target SHA fails **before** any file/checkout access.

The probe can call host checkout/materialization functions and clean owned
temporary directories, so it must be used only with an authorized host and
quiescent sources. It **never** performs a Life-ref CAS publish, remote update,
automatic migration, or force rollback. It is not a generic W1→W2 converter,
and selecting W1 for initial 1.0 does **not** approve an upgrade API.

## Compatibility tests

- Exact named export whitelist + identity/signature equivalence.
- Valid empty context and route projection without caller mutation; reject
  mismatched hash/character and competing route input.
- Reject absent four-method adapter and invalid target SHA without callback
  invocation or source writes.
- Ensure wheel and sdist include/import subfacades **outside checkout** with
  the current 37 schemas and without private launchers.
- Existing synthetic upgrade transactions and spatial tests continue to
  exercise full failure, history and Git authority boundaries.

## Separate stable-v1 gates

Choose whether both subfacades, neither, or a narrower slice is actually
worthy of public 1.x stability. Review exact types/errors, Python-version
window, deprecated `engine.life` path and CLI error/output contract.
Run an **actual 1.0 release candidate** through unchanged W1 goldens, provider
and all long-horizon gates **before** release. Existing `0.2.0.dev0`
previews are not stable-v1 proof. Main, runtime schema, W1 serializer,
migration registry and live saves remain unchanged.
