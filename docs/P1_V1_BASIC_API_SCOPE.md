# P1 v1 small-core API scope — owner direction recorded

**Owner approved the basic-functions-first principle on 2026-10-10.**
For the initial 1.0, focus on a **small public Python facade** that:
(1) consumes deterministic provider-backed Life runtime inputs;
(2) loads/verifies legacy W1 saved histories and stages **separate candidate**
W1 snapshots; and (3) validates sealed spatial data and projects approved
routes. Shipping a complicated host upgrade/recovery system is explicitly
**deferred**. The **initial persisted writer format W1** was approved
separately (see [W1 decision](P1_W1_INITIAL_WRITER_DECISION.md)).

**Scope acceptance is not exact ABI/signature acceptance, declaration of a
stable 1.0 API, final CLI wire contract, or authorization to merge/release.**

## Proposed exact minimal whitelist

The file [`oss/p1-v1-basic-api-scope.json`](../oss/p1-v1-basic-api-scope.json)
records a **candidate** symbol whitelist for five modules:
`snowfall_life`, `snowfall_life.schema`, `snowfall_life.runtime`,
`snowfall_life.persistence`, and `snowfall_life.spatial`.
Do **not** infer stability from other importable `engine.life.*` or
`snowfall_life.*` modules. These are deliberately identity aliases of
tested implementations; there is no fork of the simulator or persistence
authority. Runtime types used by the four method
`RuntimeFactProvider` protocol must be available through the candidate
canonical namespace so consumers need not import internal implementation
paths solely to annotate callbacks.

The candidate save/verify operation is **not a Git publication command**.
It cannot update main, the production Life ref, or other host refs; the
staging function must use a separate caller-owned destination and reject
overlapping roots. No automatic rewrite of old histories, no 1.0 schema
renumbering, no new on-disk envelope. Existing 16 schema-v1 families and
`engine_version="0.1.0-foundation"` are intentionally retained.

## Features available in preview but excluded from initial stability

- `snowfall_life.upgrade` is currently importable from the pre-v1 preview,
  but its host-injected target compatibility probe and four-method upgrade
  adapter **do not belong to the initial stable v1 whitelist**.
- Operator correction/recovery Git candidate logic and CAS publication remain
  internal/experimental host-level operations. The user has not authorized
  an irreversible public migration/commit protocol.
- W2 conversion is deferred until a specific future W1 limitation warrants a
  separately reviewed source/target reader, hash/relink proof and migration.
- The canonical `snowfall-life` executable currently exposes **seven
  Foundation sandbox commands**. Only diagnostic `self-check`,
  `validate` and `canonical-hash` are proposed as **basic CLI candidates**.
  `advance`, `verify-workspace`, `init-sandbox` and
  `bootstrap-propose` continue to exist as **synthetic Foundation tools**,
  not as a provider-backed Life CLI. CLI stdout/error/exit stability awaits a
  specific review; see [CLI inventory](P1_CLI_RC_REVIEW.md).
- The old `engine.life` import must remain functional for published v0.1.0
  consumers during this transition. Exact 1.x deprecation/removal timelines
  are **not decided**; no blanket compatibility promise for every internal
  import is implied.

## Regression gates before choosing exact signatures

1. Assert the **candidate** whitelist from the machine-readable record
   matches actual `__all__` in the experimental modules.
2. Ensure deferred `snowfall_life.upgrade` is never promoted into the
   candidate stable module set **merely because it remains importable**.
3. Preserve the 16-family W1 identity, immutable v0.1.0 goldens, full
   four-callback Provider consumer and strict spatial authority.
4. Probe actual installed **non-published 1.0.0rc1 wheel and sdist**, using
   the reviewed candidate canonical imports and fresh Python 3.12 venvs.
   Do not rename/freeze `engine_version`; do not regenerate goldens.
5. Re-run Fast, Full/coverage + 72h/28d/90d and all six determinism matrix
   cells on the same reviewed commit; document remaining CLI/deprecation
   decisions and request separate approval for final ABI/merge/release.

This record explicitly distinguishes **approved feature scope** from the
pending **exact API/ABI/CLI error-contract approval**. The machine-readable
record keeps `exact_public_signatures_approved=false` and
`release_candidate_passed=false` until separately verified and accepted.
