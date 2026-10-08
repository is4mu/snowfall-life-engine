# Life Engine v1 Product Boundary

Status: **Proposed freeze for P1**  
Applies to: Snowfall Life Engine after the `v0.1.0` public baseline  
Normative scope owner: `is4mu/snowfall-life-engine`

This document defines the **product boundary for Life Engine v1**.

It is intentionally narrower than “everything Snowfall may eventually need.” The purpose of the freeze is to stop scope creep, keep the reusable engine application-agnostic, and provide a stable integration target for Character Creator and the private Snowfall application.

The v1 boundary is a **scope and contract freeze**, not a requirement to rename the package to version `1.0.0` immediately. The current public release line may continue through `0.x` while the contracts below mature.

## 1. v1 mission

Life Engine v1 owns deterministic simulation of a persistent character life from explicit inputs, policy, state, time, and keyed randomness.

Given equivalent semantic inputs, the engine must produce equivalent semantic results regardless of restart boundaries, Python hash iteration order, or host timezone.

The engine is reusable infrastructure. It does not own the product-specific identity, presentation, remote-production wiring, or user interface of Snowfall.

## 2. In scope

### 2.1 Foundation and deterministic execution

v1 owns:

- canonical serialization and semantic hashing;
- stable IDs and explicit error contracts;
- keyed deterministic randomness;
- explicit RFC3339/timezone-aware time semantics;
- target-time advancement;
- deterministic candidate selection and resolution;
- deterministic semantic equivalence across supported execution partitions.

### 2.2 Persistent life state and factual history

v1 owns:

- persistent checkpoints/state required to determine the future;
- append-only finalized factual history by default;
- explicit correction semantics instead of silent historical rewrite;
- restart-safe serialization and reload;
- persistent-state validation and fail-closed loading;
- semantic state/history hashes used for equivalence and integrity.

### 2.3 Activity and human-state simulation

v1 owns reusable mechanics for:

- activity candidate generation;
- activity materialization;
- active-interval lifecycle;
- finalization and effects;
- human-state dynamics and derived state;
- sleep/wake behavior;
- hunger, fatigue, stress, affect, social battery, and other engine-owned state dimensions represented by public schemas/contracts.

### 2.4 Schedules, commitments, tasks, and routines

v1 owns:

- schedules and schedule state;
- commitments and hard temporal constraints;
- tasks/deadlines;
- routine adapters;
- wakeups;
- deterministic conflict/priority handling;
- travel/time feasibility insofar as it is expressed through reusable engine/spatial contracts.

### 2.5 Social simulation primitives

v1 owns:

- synthetic/application-supplied social opportunities;
- availability and response logic;
- deterministic social decision mechanics;
- relation-state reduction;
- reusable social runtime primitives.

The engine does **not** own a private real-person social graph or product-specific social-network integration.

### 2.6 Spatial and world primitives

v1 owns:

- reusable spatial context contracts;
- runtime spatial feasibility/selection;
- world-state reduction;
- home-state reduction where represented as generic engine state;
- application-independent location/state semantics.

Private home geometry, visual assets, addresses, and product environment documents remain outside the engine.

### 2.7 Domain reducers

v1 includes the reusable reducers currently represented by public engine contracts, including:

- finance;
- home;
- relations;
- wardrobe;
- world state.

These reducers operate only on explicit engine/application inputs. They do not authorize the engine repository to own private source-of-truth data.

### 2.8 Logical captures

v1 owns logical capture / Camera Roll semantics:

- capture opportunities/decisions;
- capture records;
- repeat policy;
- deterministic capture pipeline behavior.

v1 does **not** own image rendering, diffusion/image-generation models, asset hosting, or product gallery UI.

### 2.9 Recovery and operator mechanics

v1 owns application-independent local mechanics for:

- correction;
- recovery;
- local Git candidate/commit/CAS construction;
- generic operator requests/actions/history;
- generic operator Git transactions;
- generic engine/policy upgrade validation.

Upgrade mechanics may depend on an injected public adapter for environment-specific identity/materialization/probing. The core must retain exact target pinning, clean-checkout verification, policy-identity validation, compatibility evidence, and fail-closed behavior.

### 2.10 Public schemas, sandbox CLI, and validation

v1 owns:

- versioned public schemas;
- schema registry closure;
- synthetic bootstrap/sandbox commands;
- validation and canonical-hash utilities;
- workspace verification;
- CLI self-check;
- public deterministic tests and CI gates.

The CLI is a developer/engine surface, not a promise of end-user product UX.

## 3. Explicitly out of scope

The following are **not Life Engine v1 responsibilities**.

### 3.1 Character authoring and presentation

- Character Creator UI;
- 3D body/appearance editor;
- MBTI/questionnaire UX;
- visual face/body asset generation;
- image generation/rendering;
- end-user wardrobe/appearance UI.

These belong to Character Creator or the consuming application.

### 3.2 Private character/application data

- character canon and biography;
- real/private identity data;
- private body/visual references;
- private wardrobe assets;
- private runtime character projection;
- private social graph/personal entity data;
- private runtime history or production state.

### 3.3 Production authority and workflow wiring

- private production bootstrap values;
- production authority values;
- application production composition;
- application production fact-source wiring;
- production/operator launchers;
- GitHub Actions handoff workflow;
- remote life-ref publication;
- bearer-token/credential plumbing;
- secrets and credentials.

The public engine may define generic local contracts/adapters, but the private application owns the production wiring.

### 3.4 Product/service concerns

- hosted API/service deployment;
- always-on orchestration service;
- real-time multiplayer;
- account/authentication systems;
- billing;
- product analytics;
- notification delivery;
- end-user operational dashboards;
- app-specific remote storage topology.

### 3.5 LLM- or connector-required core behavior

Core simulation must not require:

- an LLM;
- live web access;
- GitHub network access;
- application-specific live connectors;
- external AI/image APIs.

A consuming application may use such systems to supply facts, render outputs, or provide UX, but they are outside the deterministic core.

### 3.6 Snowfall-specific media behavior

- social-media posting;
- image-generation workflows;
- application-specific camera/photo rendering;
- Instagram-like/private product automation.

## 4. Compatibility contracts

Unless changed through an explicit reviewed migration, v1 work preserves the following.

### 4.1 Determinism

Equivalent semantic inputs, supported policy, seed, engine identity, and target time must produce equivalent semantic results.

Determinism must not depend on:

- host local timezone;
- unordered Python container iteration;
- wall-clock timing;
- unkeyed randomness;
- live network responses.

### 4.2 Restart equivalence

Partitioning execution and reloading persistent state must not change modeled life.

The public 72-hour restart-equivalence suite remains a required contract.

### 4.3 History integrity

Finalized factual history is append-only by default.

Corrections/recovery are explicit operations with explicit provenance; they are not silent rewrites.

### 4.4 Persistence and schema safety

Persistent input is validated fail-closed.

Schema/version incompatibility must fail explicitly or pass through a reviewed migration path.

### 4.5 Git/operator safety

Local Git/operator mechanics must preserve their authority/CAS guarantees and must not silently mutate unrelated refs, caller worktrees, indexes, or production authority.

### 4.6 Application independence

The public engine must run and test without access to the private Snowfall repository or private Snowfall data.

### 4.7 Public execution baseline

The v1 baseline keeps:

- Python 3.12 as the required public CI runtime;
- `engine.life` as the compatibility import path;
- pytest semantic marker tiers;
- Fast, Full, determinism, publication-boundary, and long-horizon validation.

## 5. Public API stability policy

v1 does **not** declare every public Python module/function permanently stable.

The stable surface is contract-first:

1. documented semantic behavior;
2. versioned persisted schemas/representations;
3. documented CLI behavior intended as a contract;
4. explicitly documented import/API contracts;
5. error codes and validation behavior covered by contract tests.

Internal module layout and helper functions may still be refactored if semantic and versioned contracts remain intact.

This prevents the first OSS extraction layout from accidentally becoming an unlimited permanent API promise.

## 6. Deferred decisions

The following are intentionally deferred and are **not blockers** for the v1 product boundary:

- renaming `engine.life` to `snowfall_life`;
- introducing the preferred `snowfall-life` executable/package identity;
- supporting Python versions beyond 3.12;
- publishing a generic remote publisher;
- publishing a reference production-authority implementation;
- hosted-service packaging;
- plugin/connector ecosystem design;
- performance parallelization/property-testing dependencies;
- semantic version `1.0.0` timing.

Each may be proposed separately without expanding the core product boundary by default.

## 7. Change-control rule

A proposed change is inside v1 only when it satisfies all of the following:

1. it is reusable across consuming applications;
2. it can be tested with synthetic/public fixtures;
3. it does not require private Snowfall data or production secrets;
4. it preserves or explicitly migrates deterministic/persistent contracts;
5. its behavior can run without a live network in core tests;
6. it belongs to deterministic life simulation rather than authoring, rendering, hosting, or product UX.

If any condition is false, the feature belongs outside Life Engine v1 unless this boundary is explicitly revised.

## 8. Definition of done for P1

P1 — Life Engine v1 boundary freeze — is complete when:

- this document is merged as the normative scope contract;
- README reflects that `v0.1.0` is released;
- README links to this boundary;
- obsolete “first export in progress” wording is removed;
- the OSS export/public-private contract remains consistent with this scope;
- public CI remains green;
- no engine behavior is changed merely to complete the freeze.

Once P1 is complete, the project may move to **P2: Character Creator v1** without reopening Life Engine scope unless a concrete integration blocker demonstrates a missing reusable engine contract.
