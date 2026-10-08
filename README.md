# Snowfall Life Engine

**Snowfall Life Engine** is a deterministic, event-driven runtime for simulating continuous character lives.

It is designed to advance persistent character state through time using explicit policies, schedules, tasks, social opportunities, append-only events, deterministic randomness, and restart-safe serialization — without requiring an LLM, a live API, or an always-on server.

> [!IMPORTANT]
> This repository has completed **Phase 0: public repository preparation** and the pre-split validation gate is satisfied.
> The first clean engine export is now being prepared from the approved private source baseline `458e05673a8d4a8f3b2346825b2eef8419411d64`. No private Git history is imported.

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

Current public status: **Phase 1 clean export in progress**. The reusable engine and public test architecture are being validated as an independent repository before the first `v0.1.0` baseline.

Planned initial baseline:

- release: `v0.1.0`
- maturity: experimental
- license: Apache-2.0
- initial import compatibility: `engine.life`
- initial CI Python: `3.12`
- future preferred package/CLI identity: `snowfall_life` / `snowfall-life`

The first extraction uses a clean public history and does not expose private project Git history.

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

This repository will contain only reusable engine code, versioned schemas, synthetic fixtures, generic documentation, and public CI.

It will **not** contain:

- private character canon or identity assets;
- private environment / home assets;
- private runtime character or social-graph data;
- production credentials or secrets;
- private production authority values;
- application-specific image generation or social-media workflows;
- private runtime history or production state.

See:

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

- export only approved engine/schema/test paths;
- preserve engine semantics during extraction;
- replace private-only fixture assumptions with synthetic equivalents;
- run public Fast and Full regression suites;
- verify that no private data or Git history is present.

### Phase 2 — public baseline

- tag `v0.1.0`;
- make the public engine identity authoritative for reusable engine code;
- reconnect the originating application through an explicit reviewed engine version/commit.

## Security

Please see [SECURITY.md](SECURITY.md).

## Contributing

Contribution guidelines will evolve as the first engine baseline is published. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0. See [LICENSE](LICENSE).

---

Snowfall Life Engine originated as the reusable simulation core of the Snowfall project. The public engine is intentionally separated from project-specific character data and production state.
