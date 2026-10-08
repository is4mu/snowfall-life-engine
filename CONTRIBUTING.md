# Contributing to Snowfall Life Engine

Thank you for your interest in Snowfall Life Engine.

## Current project phase

The repository has published **v0.1.0** as its first clean OSS baseline and is now stabilizing the **public v1 contract**.

During the pre-v1 stabilization phase:

- changes must remain inside the responsibility boundary in [docs/V1_BOUNDARY.md](docs/V1_BOUNDARY.md), unless the boundary is explicitly amended;
- documented persistent, deterministic, error, adapter, and CLI contracts require compatibility review;
- internal implementation may evolve without freezing every importable module as public API;
- behavior changes must include semantic regression coverage;
- no contribution may depend on private Snowfall application data, production authority, private Git history, or live application workflow wiring.

## Principles

Contributions should preserve the core project properties:

1. deterministic behavior for equivalent semantic inputs;
2. append-only finalized history by default;
3. restart-safe persistent state;
4. explicit versioned contracts;
5. synthetic and reproducible tests;
6. no required LLM or live external API for core simulation;
7. no application-specific private character or production data in the engine repository.

## Pull requests

Keep pull requests focused and explain:

- what behavior or contract changes;
- why the change is necessary;
- which tests prove the change;
- whether persistent schemas or deterministic semantics are affected.

Behavior changes should include regression tests.

Do not weaken deterministic-equivalence, history-integrity, or fail-closed validation merely to make a test pass.

## Tests

The public baseline is validated on **Python 3.12**. From a clean checkout:

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

Run the complete public suite with:

```bash
python -m pytest
```

**Fast** contains unit, contract, publication, and non-slow integration tests. **Full** additionally contains scenario, calibration, and the 72h/28d/90d long-horizon runners. Public CI remains secret-free and reproducible on standard GitHub-hosted runners.

## Private data

Never submit:

- private character identities or biographies;
- private environment/home assets;
- private runtime histories;
- real credentials, tokens, or production authority values;
- private social graphs or personal entity data;
- application-specific production workflow secrets.

Use synthetic fixtures for examples and tests.

## Licensing

By submitting a contribution intended for inclusion in this repository, you agree that it may be distributed under the repository's Apache-2.0 license.
