# P1 v1 Readiness — Decision and Evidence Matrix

Status: **proposal for review**, 2026-10-09. The **functional responsibility boundary** was frozen by [V1_BOUNDARY.md](V1_BOUNDARY.md) (merged PR #4). This file does **not** declare API stability or select a migration guarantee. P1 stays in scope: no Character Creator, application integration, or private production wiring.

## Evidence already obtained on public branches

| Workstream | Evidence | Readiness meaning |
| --- | --- | --- |
| Baseline OSS extraction | v0.1.0, target `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`, main CI Run #126 | P0 completed, **not** a stable 1.0 API |
| Post-release alignment, API/adapter proposals | Draft [PR #7](https://github.com/is4mu/snowfall-life-engine/pull/7), Run #131 green | Candidate facade/CLI/protocol language and baseline regression guards |
| Canonical package prototype | Draft [PR #8](https://github.com/is4mu/snowfall-life-engine/pull/8), public CI Run #133 green (including 6/6 determinism), isolated wheel/sdist Run #3 green | `snowfall_life` / `snowfall-life` installation, all 37 schemas packaged; still unreleased |
| Persistence artifact evidence | Draft [PR #10](https://github.com/is4mu/snowfall-life-engine/pull/10), Run #134 green | 16 stored-document families with `null` future migration/read/write promises; special OperatorHistory legacy normalization identified |
| Provider-backed full runtime synthetic consumer | Draft [PR #11](https://github.com/is4mu/snowfall-life-engine/pull/11) | Exercised all four fact-provider callbacks, repeat candidate replay, byte-immutable baseline and malformed-host-result rejection; independent provider CI verified; Full/determinism final gate pending latest Run completion |
| Migration policy implementation | [Issue #9](https://github.com/is4mu/snowfall-life-engine/issues/9) | **Not implemented**; no v0.1.0 -> 1.x readability declaration |

Do not interpret green validation on separate PR branches as an integrated 1.0 release candidate. Rebased/combined testing is needed after the explicitly approved merge order.

## Decisions requiring owner approval before any 1.0 stability declaration

| ID | Decision and proposed default (review required) | Alternatives / risks | Acceptance proof needed |
| --- | --- | --- | --- |
| D1: Public Python facade | Use **small root exports** for canonical/errors; explicit `schema`, `runtime`, `persistence`, `spatial`, and optional `upgrade` subfacades. Do not stabilize internal module paths by importability. | Exposing all implementation modules would make future refactors breaking; a too-small facade would force adapters to rely on internals. | Named whitelist, signatures, types, exception contract and synthetic consumer tests for every supported entry. |
| D2: Package/CLI identity | Canonical `snowfall_life` and `snowfall-life` **before** 1.0, already decided by frozen boundary. Propose keeping `engine.life` as a documented deprecated 1.x compatibility path, pending removal window. | Immediate removal breaks pre-v1 consumers; permanent alias doubles API support burden. | Installed wheel/sdist and clean-Python-3.12 tests for both import paths, CLI and all 37 schemas. |
| D3: v0.1.0 persisted data | **Recommend** making first 1.x reader able to import known clean v0.1.0 synthetic artifact families via an explicit staged, validated, deterministic migration, but **do not promise** this until all migration vectors pass. | Disclaim all 0.x readability (simpler but burdens adopters), or silently accept/normalize unknown formats (unacceptable fail-open). | Family-level reader/writer matrix, migration registry, golden source/target snapshots and full failure-injection invariants. |
| D4: Engine-version policy | Preserve existing `0.1.0-foundation` stored bytes until a deliberate migration; keep package SemVer, per-family schema version, behavior-policy version and exact engine SHA separate. | Changing the version constant without migration breaks checkpoints. | Unsupported future/old format rejection, read-only inspection tests, staged copy and CAS/immutability checks. |
| D5: OperatorHistory absent version | Document and decide whether v1 retains existing `normalize_operator_history` fallback (missing version is normalized to 1), or explicitly migrates such records. | Uniform strict rejection would break this historical reader exception; permissive normalization must not spread to unrelated formats. | Pinned legacy fixture, version-absence/future-version tests, operator ledger integrity. |
| D6: CLI commands and error protocol | Treat old seven commands as **experimental** until `snowfall-life` command/exit/output tables are documented; clearly separate Foundation `advance` from provider-backed runtime candidate operation. | Accidentally branding the sandbox CLI as a production runtime creates wrong consumer expectations. | Exact stdout/stderr and exit/error-code contract; installed CLI smoke tests. |
| D7: Fact/spatial/upgrade adapters | Stabilize exact four-method `RuntimeFactProvider`, validated SpatialContext **data** boundary, and four-method `UpgradeEnvironmentAdapter` only after contract review; no live private authority. | Passing tests do not imply unreviewed implementations are durable APIs. | Named signatures, version/identity/hash invariants, invalid-host injection, replay/no-network conformance. |
| D8: Supported Python versions | Initial **candidate** support window: Python 3.12 (existing CI baseline). Widen only with an explicitly proven matrix. | Wider untested compatibility promise risks breakage; narrower requirement may limit consumers. | Clean-wheel/sdist install, CLI, all schemas, runtime and operator contracts in each promised interpreter. |
| D9: Coverage + release-candidate gate | Start from accepted main Run #126 baseline: statement 80.84%, branch 64.78%, combined 75.93%. Review denominator after package/protocol code before ratcheting. | A fixed percentage can be distorted by newly added code or generated schema resources. | Publication, import/schema closure, Fast, Full+coverage, 6/6 hashseed/TZ determinism, synthetic consumers and migration/failure-injection tests on one integrated release candidate. |

**D1, D2 alias specifics, D3–D9 remain review decisions**. This document does not grant a release, migrate real state, or override the frozen scope.

## Review and integration sequence (no automatic merges)

1. Finalize and review contract proposals in #7 and current baseline family inventory in #10; resolve any overlapping README/manifest documentation. Neither declares a stable 1.x API.
2. Review canonical package prototype #8 and provider consumer #11; copy only the **approved** public symbols to the future facade. Keep any compatibility bridge explicit, with tests.
3. Complete Issue #9 schema/version migration guarantees **before** changing persisted engine version or declaring 1.0 readability.
4. For each individually approved merge, rebase on latest main, review net diff for private data and changed paths, and rerun required CI. Update downstream PR branches to avoid losing their tests/docs; don't assume independently-green branches compose automatically.
5. Only after all contracts, migrations, Python window and coverage targets are approved and integrated: run the 1.0 release-candidate gate. Obtain a separate explicit go/no-go decision for tagging/releasing.

## Hard stop

No `main` merge, tag, release, destructive history rewrite, schema mass-renumbering, backward-compatibility promise or irreversible public API designation is authorized by this plan. The boundary remains fixed and Character Creator is P2, **after** P1 is deliberately accepted.
