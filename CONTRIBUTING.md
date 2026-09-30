# Contributing to Snowfall Life Engine

Thank you for your interest in Snowfall Life Engine.

## Current project phase

The repository is currently in **Phase 0**. The public shell, publication boundary, and CI safeguards are being established before the first engine-source export.

During Phase 0:

- documentation and repository-safety improvements are welcome;
- design discussion is welcome through GitHub Issues;
- engine behavior changes should wait until the initial public baseline is published;
- no contribution should depend on private Snowfall application data.

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

After the engine baseline is exported, the repository will expose two main test levels:

- **Fast**: normal development regression suite;
- **Full**: complete regression suite including longer deterministic soak/restart tests.

Public CI is expected to remain secret-free and reproducible on standard GitHub-hosted runners.

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
