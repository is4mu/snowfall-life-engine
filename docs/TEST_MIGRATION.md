# Historical Test Migration Matrix

This document maps the private Snowfall Life Engine test suite into the public OSS test architecture.

Source audit baseline:

- private repository: `is4mu/project-snowfall`
- source main: `9fe73f78ef8845d8c3b330faa7bf40b2a76c9fb0`
- audited Python files under `tests/life/`: 146
- executable test files: 138
- support/harness files: 8

This is a migration plan, not an engine export. No private engine source is copied by this document.

## Migration rules

Every historical test receives one of these dispositions:

- **migrate** — keep the behavior, rename/rehome by semantic responsibility;
- **merge** — combine overlapping historical tests into a smaller owning suite;
- **rewrite** — preserve the contract but replace historical source-freeze/private assumptions;
- **consolidate-soak** — move feature-specific long-run assertions onto the shared soak runner/invariant library;
- **private-only** — keep Snowfall application/process wiring tests private;
- **support** — move reusable setup out of `test_*.py` and into `tests/support/`.

Permanent public names must describe behavior, not Slice, Issue, PR, or rereview history.

## Cross-cutting findings

### 1. Test-to-test imports exist

Historical tests import helpers from other `test_*.py` modules. Examples include:

- `test_slice2i_invariants.py` -> `test_slice2i_calibration.py`
- capture tests -> `test_slice4a_capture_camera_roll.py`
- runtime tests -> `test_slice5a_runtime_bundle.py`
- lifecycle tests -> `test_slice5b2a_runtime_decision.py`
- persistence tests -> `test_slice5c1_fixtures.py`
- operator tests -> earlier Slice 5 test modules

Public rule: no `test_*.py` imports another `test_*.py`.

Reusable objects move into:

```text
tests/support/
  builders/
  harnesses/
  assertions/
  git/
  scenarios/
  constants.py
```

### 2. Historical Git-SHA/source-freeze tests are not public contracts

Several boundary tests reconstruct expected Python/schema bytes from private baseline SHAs and cumulative source transforms.

Public replacements:

- semantic invariants;
- explicit JSON schema compatibility;
- versioned golden serialized data only where representation is contractual;
- import/public API contracts;
- publication guards.

Private repository path allowlists and historical "only these files changed" checks remain private process governance.

### 3. Long-run coverage is duplicated

The historical suite contains many Slice-specific 28d/90d soak files.

Public design consolidates these onto shared runners:

- 72h continuous vs 12 x 6h restart equivalence;
- 28d structural coverage;
- 90d invariant validation;
- deterministic calibration as a separate category.

Feature-specific long-horizon checks become reusable invariant/scenario parameters instead of separate soak implementations.

### 4. Historical regression names are migration artifacts

Files such as:

- `test_issue155_resolver_rule_ids.py`
- `test_issue157_active_same_source.py`
- `test_rereview2.py` ... `test_rereview6.py`
- `*_review_fixes.py`

must be absorbed into the owning semantic test suites.

## Target ownership map

### Foundation, clock, canonicalization, state and history

Sources:

- `test_foundation_core.py`
- `test_advance_and_history.py`
- `test_bootstrap_and_isolation.py`
- `test_rereview2.py` ... `test_rereview6.py`
- relevant parts of `test_slice2a_contracts.py`

Target:

```text
tests/unit/foundation/
tests/unit/clock/
tests/contracts/history/
tests/contracts/state/
tests/contracts/serialization/
tests/contracts/invariants/
```

Disposition: **migrate + merge**.

The rereview suites contain useful fail-closed temporal/history invariants and should not be deleted.

### Policy

Sources:

- `test_slice2a_policy.py`

Target:

```text
tests/unit/policy/
tests/contracts/policy/
tests/fixtures/policy/
```

Disposition: **rewrite**.

Keep validation, approval, version matching, monotonic bands and deterministic policy contracts.

Replace Snowfall-specific provenance such as `character/character-bible.md` with synthetic public provenance. Do not export private approval/runtime artifacts.

### Human-state dynamics / derived state

Sources:

- `test_slice2b_derived.py`
- `test_slice2b_dynamics.py`
- fast portions of `test_slice2b_synthetic.py`

Target:

```text
tests/unit/dynamics/
tests/integration/dynamics/
```

Disposition: **migrate + merge**.

Long calibration portions move to `tests/calibration/`.

### Wakeups / scenario scheduling

Sources:

- `test_slice2c_scenarios.py`
- `test_slice2c_schema.py`

Target:

```text
tests/unit/wakeups/
tests/contracts/wakeups/
tests/scenarios/time_progression/
```

Disposition: **migrate + merge**.

Scheduler long-run portions move to shared soak infrastructure.

### Resolver and decisions

Sources:

- `test_slice2d_candidates.py`
- `test_slice2d_resolver.py`
- `test_issue155_resolver_rule_ids.py`
- `test_issue157_active_same_source.py`

Target:

```text
tests/unit/decisions/
tests/integration/decision_runtime/
tests/contracts/determinism/
```

Disposition: **migrate + merge**.

Permanent behavior names should include:

- resolver provenance rule IDs are canonical/unique;
- active same-source candidate cannot restart while continuation remains feasible;
- deterministic candidate ordering and rejection reasons.

No Issue-number filenames remain.

### Commitments, tasks and routes

Sources:

- `test_slice2e_commitments.py`
- `test_slice2e_tasks.py`
- `test_slice2e_routes_integration.py`
- `test_slice2e_soak.py`

Target:

```text
tests/unit/schedule/
tests/unit/tasks/
tests/integration/schedule_routes/
tests/scenarios/task_pressure/
```

Disposition: **migrate + consolidate-soak**.

### Routine behavior

Sources:

- `test_slice2f_exercise.py`
- `test_slice2f_household.py`
- `test_slice2f_integration.py`
- `test_slice2f_soak.py`

Target:

```text
tests/unit/routines/
tests/integration/routines/
tests/scenarios/daily_life/
```

Disposition: **migrate + consolidate-soak**.

### Social opportunity and response

Sources:

- `test_slice2g_*.py`
- `test_slice2h_*.py`
- social portions of `test_slice2i_*.py`

Target:

```text
tests/unit/social/opportunities/
tests/unit/social/responses/
tests/integration/social/
tests/scenarios/social/
tests/calibration/social/
```

Disposition: **migrate + merge + consolidate-soak**.

Any historical runtime-data binding is replaced by synthetic social fixtures.

### Cross-domain calibration and invariants

Sources:

- `test_slice2i_calibration.py`
- `test_slice2i_cross_domain.py`
- `test_slice2i_invariants.py`

Target:

```text
tests/integration/cross_domain/
tests/calibration/
tests/contracts/invariants/
tests/soak/
```

Disposition: **rewrite + merge**.

Important: calibration and correctness invariants remain separate. The current test-to-test import is removed.

### World state and reducers

Sources:

- `test_slice3a_world_state.py`
- `test_slice3b_relation_reducer.py`
- `test_slice3c_home_consumables_reducer.py`
- `test_slice3d_wardrobe_reducer.py`
- `test_slice3e_finance_reducer.py`
- corresponding Slice 3 soak files

Target:

```text
tests/unit/state/world/
tests/unit/state/relations/
tests/unit/state/consumables/
tests/unit/state/wardrobe/
tests/unit/state/finance/
tests/contracts/state/
tests/soak/state/
```

Disposition: **rewrite + consolidate-soak**.

Keep reducer semantics. Replace private character/environment/runtime fixtures and Git-baseline assertions with synthetic fixtures and semantic compatibility contracts.

### Capture / Camera Roll

Sources:

- `test_slice4a1_capture_subject_kind.py`
- `test_slice4a_capture_camera_roll.py`
- `test_slice4b1_capture_decisions.py`
- `test_slice4b2_capture_emitters.py`
- `test_slice4b3_capture_repeats.py`
- corresponding soak files
- `test_slice4c_viewer_migration.py`

Target:

```text
tests/unit/captures/
tests/integration/capture_pipeline/
tests/contracts/captures/
tests/soak/captures/
```

Disposition: **rewrite + merge + consolidate-soak**.

Move shared builders currently imported from test modules into `tests/support/builders/captures.py`.

Historical source-freeze and private-asset checks are not public engine contracts.

### Runtime bundle, schedule state and decision runtime

Sources:

- `test_slice5a_runtime_bundle.py`
- `test_slice5a_schedule_state.py`
- `test_slice5b1_activity_runtime.py`
- `test_slice5b1_runtime_effects.py`
- `test_slice5b1_schedule_reducer.py`
- `test_slice5b2a_runtime_decision.py`

Target:

```text
tests/unit/runtime/
tests/unit/schedule/
tests/integration/decision_runtime/
tests/contracts/runtime_bundle/
```

Disposition: **migrate + merge**.

Fixture factories currently living in tests move to support modules.

### Activity materialization, lifecycle and finalization

Sources:

- `test_slice5b2c1_activity_materialization.py`
- `test_slice5b2c2a_active_interval.py`
- `test_slice5b2c2a_wakeups.py`
- `test_slice5b2c2b_effects.py`
- `test_slice5b2c2b_finalization.py`
- `test_slice5b2c2b_finish_context.py`

Target:

```text
tests/unit/activities/
tests/integration/activity_lifecycle/
tests/contracts/activity_history/
```

Disposition: **migrate + merge**.

### Runtime capture

Sources:

- `test_slice5b2c2c_*.py`

Target:

```text
tests/integration/capture_pipeline/
tests/contracts/captures/
tests/support/builders/captures.py
```

Disposition: **migrate + support extraction**.

`test_slice5b2c2c_fixtures.py` contains zero tests and becomes support code.

### Runtime orchestrator

Sources:

- `test_slice5b2c3_*.py`

Target:

```text
tests/integration/runtime_orchestrator/
tests/contracts/invariants/
tests/support/harnesses/runtime.py
```

Disposition: **migrate + support extraction**.

`test_slice5b2c3_fixtures.py` contains zero tests and becomes support code.

### Persistence

Sources:

- `test_slice5c1_*.py` except historical boundary governance

Target:

```text
tests/integration/persistence/
tests/contracts/serialization/
tests/support/builders/persistence.py
```

Disposition: **migrate + rewrite**.

`test_slice5c1_fixtures.py` becomes support code.

Private baseline/source-freeze portions stay private or become semantic contracts.

### Local Git transaction / CAS

Sources:

- `test_slice5c2_binding_noop_change.py`
- `test_slice5c2_cas_commit.py`
- `test_slice5c2_repo_tree.py`
- `test_slice5c2_review_fixes.py`

Target:

```text
tests/integration/git_transactions/
tests/support/git/
```

Disposition: **migrate**.

Local temporary repositories are valid public integration fixtures. No live network is required.

`test_slice5c2_fixtures.py` becomes support code.

### Remote publisher abstraction

Sources:

- generic portions of `test_slice5d1_remote_publisher.py`

Target:

```text
tests/integration/git_transactions/remote_publisher/
```

Disposition: **migrate** using local/synthetic remotes only.

Historical repository-scope/source-freeze assertions from `test_slice5d1_boundaries.py` are **rewrite/private-only**.

### Production authority / fact-provider / composition primitives

Sources:

- `test_slice5d2a1_fact_provider.py`
- `test_slice5d2a2a_production_authority.py`
- `test_slice5d2a2b0_fact_sources.py`
- `test_slice5d2a2b1_production_provider.py`
- `test_slice5d2a2c_production_composition.py`

Target:

```text
tests/unit/runtime/production/
tests/integration/runtime/production/
tests/fixtures/bootstrap/
tests/fixtures/spatial/
```

Disposition: **rewrite + migrate**.

Generic primitives may be public, but all fixtures must be synthetic. Snowfall production bootstrap/authority values stay private.

### Production handoff workflow

Source:

- `test_slice5d2b_production_handoff.py`

Disposition: **split**.

Public:

- pinned launcher semantics;
- fail-closed checkout/pin validation;
- clean-worktree checks;
- local CAS/publication behavior;
- secret-safe bounded summaries.

Private-only:

- static assertions against `.github/workflows/life-production-handoff.yml`;
- Snowfall-specific workflow concurrency/permissions/wiring;
- real application branch expectations.

Target public tests:

```text
tests/integration/runtime/production_launcher/
```

### Correction / recovery / operator / upgrade

Sources:

- `test_slice5e1_operator_ledger.py`
- `test_slice5e2_operator_transaction.py`
- `test_slice5e3_upgrade_gates.py`

Target:

```text
tests/unit/operator/
tests/integration/operator/
tests/integration/correction_recovery/
tests/integration/upgrades/
```

Disposition: **rewrite + migrate**.

Generic transaction semantics remain public. Private production bootstrap assumptions are replaced by synthetic authority fixtures.

### Operator workflow wiring

Source:

- `test_slice5e4_operator_workflow.py`

Disposition: **split**.

Public:

- artifact/request validation;
- path traversal rejection;
- commit reachability checks;
- exact blob extraction;
- stale-head/CAS failure behavior;
- correction/recovery/upgrade routing primitives.

Private-only:

- static assertions against `.github/workflows/life-operator-action.yml`;
- coupling to private production-handoff workflow;
- Snowfall-specific workflow permissions/concurrency/wiring.

### End-to-end soak

Source:

- `test_slice5e5_soak.py`

Target:

```text
tests/soak/restart_equivalence_72h/
tests/soak/structural_28d/
tests/soak/invariants_90d/
```

Disposition: **rewrite + consolidate-soak**.

The current file imports many earlier test modules and private workflow helpers. Public soak must be rebuilt on shared support/harness APIs, not historical test modules.

### Spatial

Sources:

- `test_spatial_bootstrap_binding.py`
- `test_spatial_context.py`
- `test_spatial_runtime_projection.py`
- `test_spatial_production_context.py`

Target:

```text
tests/unit/spatial/
tests/contracts/spatial/
tests/integration/spatial/
tests/fixtures/spatial/
```

Disposition:

- first three: **rewrite + migrate** where private-baseline assertions are removed;
- `test_spatial_production_context.py`: **replace/private-only**.

The public replacement uses an entirely synthetic SpatialContext fixture. The private production artifact and private source provenance do not migrate.

### Brand / viewer repository assets

Source:

- `test_brand_v1_integrity.py`

Disposition: **private-only unless a public engine asset contract is deliberately introduced**.

The current tests validate repository branding/viewer assets rather than Life Engine semantics.

### CLI

Source:

- `test_cli.py`

Target:

```text
tests/integration/cli/
```

Disposition: **migrate**.

## Support/harness migration

Historical support files:

- `helpers.py`
- `synthetic_adapter_harness.py`
- `synthetic_harness.py`
- `synthetic_integrated_behavior_harness.py`
- `synthetic_resolver_harness.py`
- `synthetic_routine_harness.py`
- `synthetic_social_harness.py`
- `synthetic_social_response_harness.py`

Target:

```text
tests/support/
  builders/
  harnesses/
  assertions/
  scenarios/
```

Disposition: **support refactor**.

`helpers.py` must be split because it mixes generic builders with private historical SHA/source transforms.

The following must not survive in public support code:

- `SLICE*_BASELINE_MAIN`;
- private `git show` dependencies;
- `approved_issue155_decisions_bytes()`;
- `approved_issue157_decisions_bytes()`;
- cumulative private source transforms.

Equivalent behaviors are asserted directly in owning semantic tests.

## CI tier mapping

The historical class-prefix exclusion model is retired.

Public pytest markers are explicit:

- `unit`
- `contract`
- `integration`
- `scenario`
- `calibration`
- `soak`
- `publication`

Fast:

- unit;
- contract;
- publication;
- fast integration.

Full:

- all public tests including scenarios, calibration and soak.

Main/release additionally runs:

- deterministic `PYTHONHASHSEED`/timezone matrix;
- packaging/self-check;
- coverage report;
- publication/export guards.

## Migration acceptance criteria

Before deleting the historical private suite:

1. every historical file has a recorded disposition;
2. no public test imports another public `test_*.py`;
3. no public test depends on private Git SHAs;
4. no public test reads private workflow YAML;
5. no public test reads private runtime/character/environment data;
6. all public fixtures are synthetic;
7. bug regressions are represented by behavior names;
8. schema compatibility is explicit and versioned;
9. 72h restart equivalence is a first-class system invariant;
10. 28d/90d long-run coverage uses shared runners;
11. Fast and Full are marker-driven, not class-prefix exclusion driven;
12. public Full CI is green before the private historical suite is retired.

## Export gate

This document does not change the existing export gate.

The first engine-code export remains blocked until the private pre-split validation (#152 / PR #154) is completed and the approved source main SHA is fixed.
