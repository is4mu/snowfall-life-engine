# Snowfall Life Engine v1 Boundary

Status: **frozen for v1 planning**

Tracking issue: #3

This document defines the functional and compatibility boundary for the first stable public major version of Snowfall Life Engine.

The `v0.1.0` release is the clean OSS semantic baseline. The v1 boundary answers a different question: **what does the engine promise to own and stabilize for downstream consumers?**

> [!NOTE]
> “Public v1” means the first stable OSS product/API contract. It is independent from historical internal labels such as “Life Engine v2 Slice …” and from individual JSON schema-version integers.

## Boundary rule

Snowfall Life Engine v1 is a **deterministic, application-agnostic life-simulation runtime**.

It owns simulation semantics, persistent life state/history, versioned data contracts, generic runtime adapters, logical capture records, and local integrity/operator primitives.

It does not own character authoring, rendering/generation, private application data, remote production publication, credentials, hosted-product concerns, or application-specific workflow integration.

Adding a new responsibility outside the frozen ownership below requires an explicit boundary change. Improving implementation inside the frozen responsibilities does not.

## v1 owns

### 1. Deterministic target-time simulation

The engine owns deterministic advancement from one semantic state/time to another, including:

- candidate construction and decision resolution;
- activity materialization, lifecycle, and finalization;
- effects and state reduction;
- wakeups and timeline progression;
- deterministic keyed randomness;
- continuous vs partitioned/restarted semantic equivalence.

Equivalent semantic inputs, policy, seed, and target time must produce equivalent semantic results.

### 2. Persistent life state and factual history

The engine owns:

- checkpoint/current-state persistence;
- append-only finalized factual event history;
- canonical serialization and hashing;
- restart-safe reload/continuation;
- explicit correction and recovery semantics;
- versioned persistence schemas;
- migration/version gates for persisted engine state.

Plans, opportunities, and candidates are not factual history until the engine finalizes them as actual events.

### 3. Generic behavior and policy domains

The engine may model and reduce generic state for:

- Human State dynamics;
- schedules, commitments, and tasks;
- sleep, meals, routines, leisure, and travel;
- social opportunities, responses, and relationship state;
- finance, home, wardrobe, and world state where the representation is application-agnostic.

The engine owns the mechanics. Applications own the real character-specific content supplied to those mechanics.

### 4. Generic runtime inputs and adapters

The engine owns documented boundaries for receiving application-supplied information, including:

- generic fact/provider inputs;
- generic spatial/location/travel context;
- policy/bootstrap input;
- upgrade identity/materialization/probe behavior through injected adapters.

Adapters must allow consuming applications to supply production data without making that data or production workflow part of the engine repository.

### 5. Logical capture records

The engine owns logical capture/Camera Roll semantics:

- capture opportunities;
- capture decisions;
- repeat/suppression policy;
- deterministic capture metadata/event records.

The engine does **not** render or generate image/video assets.

### 6. Local integrity and operator primitives

The engine owns generic local integrity mechanics:

- correction/recovery candidate construction;
- correction/recovery transaction semantics;
- local Git candidate/commit/CAS behavior;
- engine/policy upgrade gates;
- exact target pinning and clean-checkout verification;
- compatibility-probe validation;
- injected upgrade-environment adapter boundaries.

These primitives are reusable local mechanics. Production workflow launchers, tokens, authority files, and remote publication are outside the engine.

### 7. Public tooling

v1 owns enough tooling to operate and validate the reusable engine locally:

- versioned JSON schemas;
- sandbox/bootstrap helpers;
- validation and canonical-hash commands;
- target-time advancement;
- workspace verification;
- self-check;
- synthetic fixtures/examples;
- deterministic public CI and test architecture.

## Explicitly outside v1

The following are not Snowfall Life Engine responsibilities:

### Character authoring

- Character Creator UI;
- personality questionnaires/editors;
- body/3D model editing;
- visual identity authoring;
- character canon/biography management.

### Generative/LLM product behavior

- dialogue generation;
- narration;
- prompt orchestration;
- LLM memory summarization;
- autonomous “thought” text;
- image/video generation;
- media rendering.

An application may use those systems around engine facts/state, but they are not required for core simulation.

### Private application data

- real/private character canon or identity assets;
- private social graphs or personal entity data;
- private environment/home assets;
- private runtime history or production state;
- production bootstrap/authority values.

### Production workflow wiring

- credentials, tokens, or secrets;
- private production authority loading;
- GitHub workflow/operator launchers;
- live remote Life-ref publication;
- application-specific handoff/approval workflows;
- social-media posting.

### Hosted-product concerns

- always-on daemon/service operation;
- cloud deployment;
- user accounts/authentication;
- billing;
- application UI;
- connectors to calendars, messaging, social networks, or other external services.

These belong to consuming applications or separate integration packages.

## Character Creator handoff

Character Creator is a separate repository/product.

Its responsibility is to create or edit application-owned character profiles and related assets. Life Engine consumes only generic validated runtime inputs required for simulation.

The engine must not depend on:

- Character Creator UI code;
- its 3D model implementation;
- image-generation workflow;
- private character asset storage.

The integration contract should be data/adapters, not repository coupling.

## Stable v1 contracts

Once 1.0 is declared, compatibility applies to the following documented contracts.

### Persistence and schemas

- persisted-schema compatibility;
- migration/deprecation rules;
- canonical serialization/hashing;
- append-only history/correction semantics.

### Determinism

- semantic equivalence for equivalent inputs;
- restart equivalence;
- explicit time semantics independent of host timezone;
- deterministic keyed randomness independent of Python hash iteration order.

### Public errors

Documented public error codes and their high-level meaning are compatibility contracts.

### Public adapter protocols

Documented fact, spatial, and upgrade adapter/protocol shapes are compatibility contracts.

### CLI

Documented canonical CLI commands, required arguments, exit behavior, and machine-relevant output contracts are stable.

### Python API

Only a **small documented public facade** is stable.

Internal module layout under the implementation tree is not automatically part of the compatibility contract merely because a module is importable.

## Package and CLI identity

The current `engine.life` import path is a compatibility path inherited from the clean split.

Before 1.0, the canonical public identity will become:

- Python package: `snowfall_life`
- CLI: `snowfall-life`

`engine.life` may remain as a compatibility alias during a documented transition, but it is not the intended long-term v1 identity.

The identity change must happen before v1 stability is declared so consumers are not forced through an avoidable post-1.0 breaking rename.

## Compatibility policy for 1.x

The v1 implementation must follow these rules:

1. additive non-breaking API/schema changes may ship in minor versions;
2. persisted-format changes require an explicit migration or backward-compatible reader;
3. removing or changing a documented public facade symbol requires deprecation before removal, unless required to fix a security/correctness flaw;
4. deterministic semantics may not silently change merely for tuning convenience;
5. calibration/policy tuning must be distinguishable from hard engine invariant changes;
6. internal/private modules may be reorganized without compatibility promises when they are not part of the documented facade.

## v1 release readiness

The boundary is frozen now; v1 is not yet release-ready.

Before the v1 release candidate:

- [ ] define the minimal stable Python facade;
- [ ] introduce canonical `snowfall_life` packaging and `snowfall-life` CLI;
- [ ] publish a compatibility/deprecation plan for `engine.life`;
- [ ] document and contract-test fact/spatial/upgrade adapter protocols;
- [ ] define persisted-schema migration/deprecation policy for 1.x;
- [ ] provide one complete synthetic consumer example;
- [ ] decide and document the supported Python-version window;
- [ ] preserve the accepted coverage baseline or explicitly review any regression;
- [ ] pass Fast, Full, determinism, publication, and coverage validation on the v1 release candidate.

## Current baseline

The boundary is grounded in the first public baseline:

- release: `v0.1.0`;
- main baseline commit: `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`;
- Fast: 1473 passed;
- Full: 1492 passed;
- determinism matrix: 6/6 passed;
- statement coverage: 80.84%;
- branch coverage: 64.78%;
- combined coverage.py percentage: 75.93%.

The baseline proves the extracted semantics. It does not by itself make every currently importable implementation module a stable v1 API.

## Scope-change rule

After this document is merged, a proposal that adds a new engine responsibility outside **v1 owns** must explicitly update this boundary and explain why that responsibility belongs in the reusable engine rather than in an application or adapter.

This rule is intended to keep v1 finite and to prevent Character Creator, Snowfall application work, generative media, and production workflow concerns from expanding the Life Engine scope.
