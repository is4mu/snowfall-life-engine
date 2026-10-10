# Snowfall Life Engine

**Snowfall Life Engine** is a deterministic, event-driven runtime for simulating continuous character lives.

It is designed to advance persistent character state through time using explicit policies, schedules, tasks, social opportunities, append-only events, deterministic randomness, and restart-safe serialization — without requiring an LLM, a live API, or an always-on server.

> [!IMPORTANT]
> **v0.1.0 is released** as the first clean public baseline.
> The project is now freezing the **public v1 boundary** before broader API/package stabilization. See [docs/V1_BOUNDARY.md](docs/V1_BOUNDARY.md).

## Project goals

Snowfall Life Engine is intended to provide a reusable core for:

- deterministic target-time simulation;
- append-only factual event history;
- persistent and restart-safe state;
- schedules, commitments, tasks, travel, sleep, meals, leisure, and social behavior;
- deterministic keyed randomness;
- logical capture / Camera Roll records;
- continuous vs partitioned/restarted semantic equivalence;
- offline/local execution without an LLM dependency.

## Architecture principles

1. **Actual events are facts.** Plans and opportunities are not history until they occur.
2. **Determinism is a contract.** Same semantic inputs, policy, seed, and target time must produce the same semantic result.
3. **History is append-only by default.** Corrections are modeled explicitly rather than silently rewriting finalized history.
4. **State stays minimal.** Persist only information needed to determine the future or expensive to reconstruct.
5. **The engine is application-agnostic.** Character canon, private application data, and product-specific workflows remain outside this repository.
6. **No LLM is required for core simulation.**
7. **Restart boundaries must not change modeled life.**

## Repository status

Current public status: **v0.1.0 baseline released; public v1 boundary frozen for planning**.

Current baseline:

- release: `v0.1.0`
- baseline commit: `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`
- maturity: experimental
- license: Apache-2.0
- current compatibility import: `engine.life`
- CI Python: `3.12`
- planned canonical v1 package/CLI identity: `snowfall_life` / `snowfall-life`

The clean public history does not expose private project Git history. The frozen v1 responsibility boundary is defined in [docs/V1_BOUNDARY.md](docs/V1_BOUNDARY.md). The concrete contract inventory, compatibility proposal, and remaining implementation gates are tracked in [docs/V1_CONTRACT_PLAN.md](docs/V1_CONTRACT_PLAN.md); they are not yet shipped as stable APIs.

## Pre-v1 package prototype (unreleased)

The canonical `snowfall_life` import and `snowfall-life` CLI are under review in a **0.2.0.dev0 development build**, not in the published v0.1.0 release. From a checkout of the prototype branch, `python -m pip install .` creates both entry points; the existing `engine.life` import and `python -m engine.life` command remain functional. This proposal does not change persisted `ENGINE_VERSION = "0.1.0-foundation"` or claim stable v1 API compatibility.

`snowfall-life advance` currently delegates to the **foundation sandbox** workflow; full provider-driven runtime and persistent candidate operations are separate. The package build must include the exact 37 public JSON Schemas. See the packaging CI job for isolated wheel/sdist checks.

See [the standalone synthetic foundation sandbox consumer](examples/foundation_sandbox/README.md) for a full offline example of installing this prototype, creating and advancing a synthetic checkpoint, verifying it, and testing idempotent NOOP behavior. This is **not** the separate full provider-driven RuntimeBundle consumer gate.

## Canonical full-runtime preview (Draft; not stable v1)

The **unreleased** `snowfall_life.runtime` and `snowfall_life.persistence`
facades re-export only reviewed-candidate types and provider-driven runtime
operations. The [synthetic full RuntimeBundle consumer](examples/provider_runtime/README.md)
now uses these canonical imports for verification and candidate staging;
synthetic bootstrap helpers remain implementation-only. These imports are
**not** yet a frozen public API. See
[the exact proposed export/ownership contract](docs/P1_CANONICAL_RUNTIME_FACADE_PREVIEW.md).

The proposed [spatial and upgrade-host preview](docs/P1_SPATIAL_UPGRADE_FACADE_PREVIEW.md)
also exposes **sealed spatial data projection** and a **host-injected
read-only compatibility probe**. Neither performs production publication
or promises a stable 1.x API.

The [pre-v1 CLI behavior/1.0 RC review checklist](docs/P1_CLI_RC_REVIEW.md)
distinguishes the seven Foundation sandbox commands from the full provider
runtime. It is a **candidate error/exit contract**, not a 1.0 release.

**First-1.0 scope:** The owner selected [basic stable-functionality first](docs/P1_V1_BASIC_API_SCOPE.md): deterministic provider runtime, safe W1 read/stage and sealed spatial validation. Complex upgrade/recovery stays experimental. This selects **functional scope**, not exact public ABI or a released 1.0 binary.

## Synthetic full RuntimeBundle consumer (pre-v1)

The [provider-driven synthetic consumer](examples/provider_runtime/README.md) exercises a complete **RuntimeBundle** persistent candidate using an injected four-method fact provider, two deterministic replays, verified candidate reload, and byte-for-byte baseline immutability. It is **not** the foundation sandbox CLI, a production-character launcher, a migration path, or a declaration that the current implementation imports are stable v1 APIs.

## Local development

The public baseline is validated on **Python 3.12**. A clean local setup is:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r engine/life/requirements.txt
python -m pip install -r tests/requirements.txt
```

Run the normal development gate with:

```bash
python -m pytest -m "unit or contract or publication or (integration and not slow)"
```

Run the complete public suite, including scenario, calibration, and long-horizon soak tests, with:

```bash
python -m pytest
```

The three public long-horizon runners are the 72-hour restart-equivalence, 28-day structural, and 90-day invariant suites under `tests/soak/`. See `docs/TESTING.md` and `docs/CI_TIERS.md` for the normative test/CI contract.

## Public / private boundary

This repository contains only reusable engine code, versioned schemas, synthetic fixtures, generic documentation, and public CI.

It **does not** contain:

- private character canon or identity assets;
- private environment / home assets;
- private runtime character or social-graph data;
- production credentials or secrets;
- private production authority values;
- application-specific image generation or social-media workflows;
- private runtime history or production state.

See:

- [docs/V1_BOUNDARY.md](docs/V1_BOUNDARY.md) for the frozen public v1 responsibility boundary;
- [docs/V1_CONTRACT_PLAN.md](docs/V1_CONTRACT_PLAN.md) for contract candidates, compatibility decisions, and remaining v1 acceptance gates;
- [docs/EXPORT_BOUNDARY.md](docs/EXPORT_BOUNDARY.md) for the publication contract;
- [docs/TESTING.md](docs/TESTING.md) for the normative public test architecture;
- [docs/TEST_MIGRATION_MATRIX.md](docs/TEST_MIGRATION_MATRIX.md) for historical-suite dispositions;
- [docs/TEST_LAYOUT_PLAN.md](docs/TEST_LAYOUT_PLAN.md) for the concrete 146-file migration map and target file layout;
- [docs/FIXTURES.md](docs/FIXTURES.md) for synthetic fixture ownership and golden-file rules;
- [docs/CI_TIERS.md](docs/CI_TIERS.md) for Fast, Full, determinism, and coverage tiers.

## Roadmap

### Phase 0 — repository preparation

- [x] reserve public repository
- [x] establish project identity
- [x] add public governance and security files
- [x] add export / exclusion contract
- [x] add public CI skeleton
- [x] define public test architecture
- [x] audit and map the historical Life Engine test suite
- [x] define fixture architecture
- [x] define public CI tiers
- [x] complete pre-split private validation

### Phase 1 — clean engine export

Approved extraction baseline: `is4mu/project-snowfall@458e05673a8d4a8f3b2346825b2eef8419411d64`.

- [x] export only approved engine/schema/test paths;
- [x] preserve engine semantics during extraction;
- [x] replace private-only fixture assumptions with synthetic equivalents;
- [x] run public Fast and Full regression suites;
- [x] verify that no private data or Git history is present.

### Phase 2 — public baseline

- [x] tag and publish `v0.1.0`;
- [x] make the public engine repository authoritative for reusable engine code;
- [x] validate main with Fast, Full, determinism, publication, and coverage reporting.

### Phase 3 — v1 contract stabilization

- [x] freeze the functional/public-private v1 boundary;
- [ ] define the minimal stable Python facade;
- [ ] introduce canonical `snowfall_life` packaging and `snowfall-life` CLI;
- [ ] document compatibility/deprecation rules for `engine.life`;
- [ ] contract-test public fact/spatial/upgrade adapter protocols;
- [ ] define persisted-schema migration policy for 1.x;
- [ ] publish a complete synthetic consumer example;
- [ ] finalize the supported Python-version window and coverage ratchet;
- [ ] validate a v1 release candidate.

## Security

Please see [SECURITY.md](SECURITY.md).

## Contributing

For the current pre-v1 review, testing, and compatibility requirements, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).

---

Snowfall Life Engine originated as the reusable simulation core of the Snowfall project. The public engine is intentionally separated from project-specific character data and production state.
