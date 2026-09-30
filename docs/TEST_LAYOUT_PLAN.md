# Concrete Test Layout Plan

Status: Phase 0 design artifact. No engine source has been exported.

Source audit baseline:

- private repository: `is4mu/project-snowfall`
- private main: `9fe73f78ef8845d8c3b330faa7bf40b2a76c9fb0`
- Python files mapped: **146 / 146**
- executable historical tests: **138**
- historical support/harness files: **8**

This document is the concrete companion to [TESTING.md](./TESTING.md) and [TEST_MIGRATION_MATRIX.md](./TEST_MIGRATION_MATRIX.md).

Every historical Python file now has an explicit destination or private-only disposition.

## Final public test tree

```text
tests/
  support/
    builders/
      workspace.py
      captures.py
      capture_runtime.py
      persistence.py
      runtime_orchestrator.py
    harnesses/
      dynamics.py
      domain_adapters.py
      integrated_behavior.py
      resolver.py
      routines.py
      social_opportunities.py
      social_response.py
    assertions/
      invariants.py
      equivalence.py
    git/
      local_repo.py
    scenarios/
      daily_life.py
      social.py
      task_pressure.py
    constants.py

  fixtures/
    policy/
    bootstrap/
      synthetic_authority.json
    schema/
    spatial/
      synthetic_city.json
    golden/

  unit/
    foundation/
    state/
    policy/
    decisions/
    schedule/
    routines/
    social/
    captures/
    activities/
    spatial/
    operator/

  contracts/
    foundation/
    core/
    effects/
    state/
    policy/
    wakeups/
    invariants/
    serialization/
    captures/
    activities/
    runtime/
    social/
    persistence/
    git_transactions/
    spatial/
    operator/

  integration/
    bootstrap/
    cli/
    time_history/
    schedule/
    decision_runtime/
    cross_domain/
    social/
    capture_pipeline/
    activity_lifecycle/
    runtime/
    runtime_sources/
    runtime_orchestrator/
    persistence/
    git_transactions/
    spatial/
    operator/

  scenarios/
    daily_life/
    time_progression/
    social/

  calibration/
    test_human_state_calibration.py
    test_behavior_policy.py

  soak/
    test_restart_equivalence_72h.py
    test_structural_28d.py
    test_invariants_90d.py

  publication/
    test_export_boundary.py
    test_private_content_absent.py
    test_workflow_safety.py
```

## Shared support API

Public tests must import reusable setup only from `tests.support`.

### Builders

- `workspace.make_workspace(...)`
- `workspace.load_state(...)`
- `workspace.save_state(...)`
- `captures.make_capture_fact(...)`
- `capture_runtime.make_runtime_capture_fixture(...)`
- `persistence.make_persistent_runtime(...)`
- `runtime_orchestrator.make_runtime_fixture(...)`

Builders construct synthetic state. They do not encode assertions.

### Harnesses

Harnesses execute a subsystem with explicit input and return structured results.

They must not:

- read private repository files;
- infer current wall-clock time;
- access the network;
- mutate global process state outside a scoped fixture;
- import `test_*.py`.

### Assertions

Reusable assertions express cross-cutting correctness:

- monotonic time;
- append-only history;
- no overlapping active activities;
- bounded Human State;
- deterministic fingerprints;
- continuous/partitioned semantic equivalence;
- queue ordering and unique event identity.

Assertions may be shared by integration/scenario/soak tests, but do not own fixture construction.

### Local Git helpers

`tests/support/git/local_repo.py` owns temporary repository creation, bare remotes, refs, commits and CAS test utilities.

No public test requires GitHub, credentials or a live network.

## Exact historical mapping

| Historical file | Disposition | Exact public destination / private disposition | Notes |
|---|---|---|---|
| `helpers.py` | split-support | `tests/support/builders/workspace.py`<br>`tests/support/builders/captures.py`<br>`tests/support/assertions/invariants.py`<br>`tests/support/constants.py` | remove private SHA/git-show/source-transform helpers |
| `synthetic_adapter_harness.py` | support | `tests/support/harnesses/domain_adapters.py` |  |
| `synthetic_harness.py` | support | `tests/support/harnesses/dynamics.py` |  |
| `synthetic_integrated_behavior_harness.py` | support | `tests/support/harnesses/integrated_behavior.py` |  |
| `synthetic_resolver_harness.py` | support | `tests/support/harnesses/resolver.py` |  |
| `synthetic_routine_harness.py` | support | `tests/support/harnesses/routines.py` |  |
| `synthetic_social_harness.py` | support | `tests/support/harnesses/social_opportunities.py` |  |
| `synthetic_social_response_harness.py` | support | `tests/support/harnesses/social_response.py` |  |
| `test_advance_and_history.py` | merge | `tests/integration/time_history/test_advance_history.py` |  |
| `test_bootstrap_and_isolation.py` | migrate | `tests/integration/bootstrap/test_workspace_bootstrap.py` |  |
| `test_brand_v1_integrity.py` | private-only | `private project-snowfall brand/viewer integrity tests` | not Life Engine semantics |
| `test_cli.py` | migrate | `tests/integration/cli/test_self_check.py` |  |
| `test_foundation_core.py` | split | `tests/unit/foundation/test_primitives.py`<br>`tests/contracts/foundation/test_serialization.py` |  |
| `test_issue155_resolver_rule_ids.py` | merge | `tests/unit/decisions/test_resolver_provenance.py` | behavior name replaces issue number |
| `test_issue157_active_same_source.py` | merge | `tests/unit/decisions/test_active_continuation.py` | behavior name replaces issue number |
| `test_rereview2.py` | merge | `tests/contracts/invariants/test_time_history_invariants.py`<br>`tests/integration/time_history/test_partition_equivalence.py` |  |
| `test_rereview3.py` | merge | `tests/contracts/serialization/test_timestamp_checkpoint.py`<br>`tests/contracts/invariants/test_time_history_invariants.py` |  |
| `test_rereview4.py` | merge | `tests/contracts/invariants/test_event_queue_provenance.py` |  |
| `test_rereview5.py` | merge | `tests/contracts/invariants/test_active_activity_temporal.py` |  |
| `test_rereview6.py` | merge | `tests/contracts/effects/test_effect_contracts.py` |  |
| `test_slice2a_contracts.py` | merge | `tests/contracts/core/test_domain_contracts.py` |  |
| `test_slice2a_policy.py` | rewrite-split | `tests/contracts/policy/test_policy_schema.py`<br>`tests/unit/policy/test_policy_rules.py` | synthetic public provenance only |
| `test_slice2b_derived.py` | migrate | `tests/unit/state/test_derived_state.py` |  |
| `test_slice2b_dynamics.py` | migrate | `tests/unit/state/test_dynamics.py` |  |
| `test_slice2b_synthetic.py` | split | `tests/scenarios/daily_life/test_human_state_progression.py`<br>`tests/calibration/test_human_state_calibration.py` | long-run calibration separated from correctness |
| `test_slice2c_scenarios.py` | split | `tests/scenarios/time_progression/test_wakeup_scenarios.py`<br>`tests/soak/test_structural_28d.py` |  |
| `test_slice2c_schema.py` | merge | `tests/contracts/wakeups/test_wakeup_contracts.py` |  |
| `test_slice2d_candidates.py` | migrate | `tests/unit/decisions/test_candidates.py` |  |
| `test_slice2d_resolver.py` | split | `tests/unit/decisions/test_resolver.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice2e_commitments.py` | migrate | `tests/unit/schedule/test_commitments.py` |  |
| `test_slice2e_routes_integration.py` | migrate | `tests/integration/schedule/test_routes.py` |  |
| `test_slice2e_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice2e_tasks.py` | migrate | `tests/unit/schedule/test_tasks.py` |  |
| `test_slice2f_exercise.py` | migrate | `tests/unit/routines/test_exercise.py` |  |
| `test_slice2f_household.py` | migrate | `tests/unit/routines/test_household.py` |  |
| `test_slice2f_integration.py` | migrate | `tests/integration/decision_runtime/test_routines.py` |  |
| `test_slice2f_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice2g_contact.py` | migrate | `tests/unit/social/test_contact_opportunities.py` |  |
| `test_slice2g_invites.py` | migrate | `tests/unit/social/test_invites.py` |  |
| `test_slice2g_post_work.py` | migrate | `tests/scenarios/social/test_post_work_contact.py` |  |
| `test_slice2g_slots.py` | migrate | `tests/unit/social/test_availability_slots.py` |  |
| `test_slice2g_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice2h_integration.py` | rewrite-migrate | `tests/integration/social/test_social_response_runtime.py` | synthetic runtime data only |
| `test_slice2h_invites.py` | migrate | `tests/unit/social/test_invite_response.py` |  |
| `test_slice2h_response.py` | migrate | `tests/unit/social/test_response_rules.py` |  |
| `test_slice2h_review1.py` | merge | `tests/unit/social/test_contact_opportunities.py`<br>`tests/unit/social/test_response_rules.py` |  |
| `test_slice2h_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice2i_calibration.py` | consolidate | `tests/calibration/test_behavior_policy.py` |  |
| `test_slice2i_cross_domain.py` | migrate | `tests/integration/cross_domain/test_behavior_runtime.py` |  |
| `test_slice2i_invariants.py` | rewrite-merge | `tests/contracts/invariants/test_behavior_invariants.py`<br>`tests/soak/test_invariants_90d.py` | remove test-to-test import |
| `test_slice3a_soak.py` | rewrite-consolidate | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` | synthetic world fixture |
| `test_slice3a_world_state.py` | rewrite-split | `tests/unit/state/test_world_state.py`<br>`tests/contracts/state/test_world_state_schema.py` |  |
| `test_slice3b_relation_reducer.py` | rewrite-migrate | `tests/unit/state/test_relation_reducer.py` | synthetic entities |
| `test_slice3b_soak.py` | rewrite-consolidate | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice3c_home_consumables_reducer.py` | rewrite-migrate | `tests/unit/state/test_consumables_reducer.py` | generic synthetic inventory |
| `test_slice3c_soak.py` | rewrite-consolidate | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice3d_soak.py` | rewrite-consolidate | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice3d_wardrobe_reducer.py` | rewrite-migrate | `tests/unit/state/test_wardrobe_reducer.py` | synthetic wardrobe catalog |
| `test_slice3e_finance_reducer.py` | rewrite-migrate | `tests/unit/state/test_finance_reducer.py` | synthetic accounts |
| `test_slice3e_soak.py` | rewrite-consolidate | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice4a1_capture_subject_kind.py` | rewrite-merge | `tests/contracts/captures/test_subject_contracts.py` | synthetic subjects |
| `test_slice4a_capture_camera_roll.py` | split | `tests/unit/captures/test_camera_roll.py`<br>`tests/integration/capture_pipeline/test_camera_roll.py` | shared builders extracted |
| `test_slice4a_soak.py` | rewrite-consolidate | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice4b1_capture_decisions.py` | rewrite-migrate | `tests/unit/captures/test_capture_decisions.py` | remove test-module helper imports |
| `test_slice4b1_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice4b2_capture_emitters.py` | rewrite-migrate | `tests/integration/capture_pipeline/test_emitters.py` | shared builders extracted |
| `test_slice4b2_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice4b3_capture_repeats.py` | rewrite-split | `tests/unit/captures/test_repeat_policy.py`<br>`tests/integration/capture_pipeline/test_repeats.py` |  |
| `test_slice4b3_soak.py` | consolidate-soak | `tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` |  |
| `test_slice4c_viewer_migration.py` | split | `tests/contracts/captures/test_camera_roll_compatibility.py`<br>`private viewer wiring tests` |  |
| `test_slice5a_runtime_bundle.py` | rewrite-split | `tests/contracts/runtime/test_runtime_bundle.py`<br>`tests/integration/runtime/test_bundle_loading.py` | synthetic runtime fixture |
| `test_slice5a_schedule_state.py` | migrate | `tests/unit/schedule/test_schedule_state.py` |  |
| `test_slice5b1_activity_runtime.py` | migrate | `tests/unit/activities/test_runtime.py` |  |
| `test_slice5b1_boundaries.py` | replace-split | `tests/contracts/activities/test_activity_schema.py`<br>`private repository source-freeze guard` | no private SHA in public tests |
| `test_slice5b1_runtime_effects.py` | migrate | `tests/integration/activity_lifecycle/test_effect_application.py` |  |
| `test_slice5b1_schedule_reducer.py` | migrate | `tests/unit/schedule/test_schedule_reducer.py` |  |
| `test_slice5b2a_boundaries.py` | replace-split | `tests/contracts/runtime/test_decision_runtime_boundaries.py`<br>`private repository scope guard` |  |
| `test_slice5b2a_runtime_decision.py` | migrate | `tests/integration/decision_runtime/test_runtime_decision.py` | shared runtime builders extracted |
| `test_slice5b2b_boundaries.py` | replace-split | `tests/contracts/social/test_social_runtime_boundaries.py`<br>`private repository scope guard` |  |
| `test_slice5b2b_social_runtime.py` | migrate | `tests/integration/social/test_social_runtime.py` |  |
| `test_slice5b2b_social_state.py` | migrate | `tests/unit/social/test_runtime_state.py` |  |
| `test_slice5b2c1_activity_materialization.py` | split | `tests/unit/activities/test_materialization.py`<br>`tests/integration/activity_lifecycle/test_materialization.py` |  |
| `test_slice5b2c1_boundaries.py` | replace-split | `tests/contracts/activities/test_materialization_contracts.py`<br>`private repository scope guard` |  |
| `test_slice5b2c2a_active_interval.py` | migrate | `tests/integration/activity_lifecycle/test_active_interval.py` |  |
| `test_slice5b2c2a_boundaries.py` | replace-split | `tests/contracts/activities/test_lifecycle_boundaries.py`<br>`private repository scope guard` |  |
| `test_slice5b2c2a_wakeups.py` | migrate | `tests/integration/activity_lifecycle/test_wakeups.py` |  |
| `test_slice5b2c2b_boundaries.py` | replace-split | `tests/contracts/activities/test_finalization_boundaries.py`<br>`private repository scope guard` |  |
| `test_slice5b2c2b_effects.py` | migrate | `tests/integration/activity_lifecycle/test_finalization_effects.py` |  |
| `test_slice5b2c2b_finalization.py` | migrate | `tests/integration/activity_lifecycle/test_finalization.py` |  |
| `test_slice5b2c2b_finish_context.py` | merge | `tests/integration/activity_lifecycle/test_finish_context.py` |  |
| `test_slice5b2c2c_boundaries.py` | replace-split | `tests/contracts/captures/test_runtime_capture_boundaries.py`<br>`private repository scope guard` |  |
| `test_slice5b2c2c_c2b_integration.py` | merge | `tests/integration/capture_pipeline/test_finalization_capture.py` |  |
| `test_slice5b2c2c_camera_roll.py` | migrate | `tests/integration/capture_pipeline/test_runtime_camera_roll.py` |  |
| `test_slice5b2c2c_fixtures.py` | support | `tests/support/builders/capture_runtime.py` | zero-test module becomes support |
| `test_slice5b2c2c_mutation.py` | merge | `tests/contracts/captures/test_append_only_capture.py` |  |
| `test_slice5b2c2c_primary.py` | migrate | `tests/integration/capture_pipeline/test_runtime_capture.py` |  |
| `test_slice5b2c2c_repeat.py` | migrate | `tests/integration/capture_pipeline/test_runtime_repeat.py` |  |
| `test_slice5b2c2c_review_fixes.py` | merge | `tests/integration/capture_pipeline/test_runtime_capture.py`<br>`tests/integration/capture_pipeline/test_runtime_repeat.py` |  |
| `test_slice5b2c2c_runtime_bind.py` | migrate | `tests/integration/capture_pipeline/test_runtime_binding.py` |  |
| `test_slice5b2c3_boundaries.py` | replace-split | `tests/contracts/runtime/test_orchestrator_boundaries.py`<br>`private repository scope guard` |  |
| `test_slice5b2c3_capture_repeat.py` | merge | `tests/integration/runtime_orchestrator/test_capture_repeat.py` |  |
| `test_slice5b2c3_continue_select_start.py` | migrate | `tests/integration/runtime_orchestrator/test_selection.py` |  |
| `test_slice5b2c3_decision_social.py` | merge | `tests/integration/runtime_orchestrator/test_social_decision.py` |  |
| `test_slice5b2c3_entry.py` | migrate | `tests/integration/runtime_orchestrator/test_entry.py` |  |
| `test_slice5b2c3_fixtures.py` | support | `tests/support/builders/runtime_orchestrator.py` | zero-test module becomes support |
| `test_slice5b2c3_history_budget_stable.py` | migrate | `tests/integration/runtime_orchestrator/test_history_budget.py` |  |
| `test_slice5b2c3_immediate.py` | migrate | `tests/integration/runtime_orchestrator/test_immediate_events.py` |  |
| `test_slice5b2c3_integration.py` | merge | `tests/integration/runtime_orchestrator/test_end_to_end.py` |  |
| `test_slice5b2c3_no_active.py` | merge | `tests/integration/runtime_orchestrator/test_no_active.py` |  |
| `test_slice5b2c3_post_finalize.py` | migrate | `tests/integration/runtime_orchestrator/test_post_finalize.py` |  |
| `test_slice5b2c3_queue_ordering.py` | migrate | `tests/integration/runtime_orchestrator/test_queue_ordering.py` |  |
| `test_slice5c1_baseline_verify.py` | replace | `tests/contracts/persistence/test_persistent_golden.py` | semantic/versioned golden, not private commit baseline |
| `test_slice5c1_boundaries.py` | replace-split | `tests/contracts/persistence/test_persistence_boundaries.py`<br>`private process guard` |  |
| `test_slice5c1_c3_handoff.py` | migrate | `tests/integration/persistence/test_orchestrator_handoff.py` |  |
| `test_slice5c1_camera.py` | migrate | `tests/integration/persistence/test_camera_roll.py` |  |
| `test_slice5c1_closed_integrity.py` | migrate | `tests/contracts/persistence/test_closed_history_integrity.py` |  |
| `test_slice5c1_fixtures.py` | support-rewrite | `tests/support/builders/persistence.py` | remove private baseline dependency |
| `test_slice5c1_isolation.py` | migrate | `tests/integration/persistence/test_isolation.py` |  |
| `test_slice5c1_last_run.py` | migrate | `tests/contracts/persistence/test_last_run_metadata.py` |  |
| `test_slice5c1_noop_revision.py` | migrate | `tests/integration/persistence/test_noop_revision.py` |  |
| `test_slice5c1_review_fixes.py` | merge | `tests/integration/persistence/test_camera_roll.py`<br>`tests/integration/persistence/test_isolation.py`<br>`tests/contracts/persistence/test_closed_history_integrity.py` |  |
| `test_slice5c1_social_persistence.py` | migrate | `tests/integration/persistence/test_social_state.py` |  |
| `test_slice5c1_timeline.py` | migrate | `tests/integration/persistence/test_timeline.py` |  |
| `test_slice5c2_binding_noop_change.py` | migrate | `tests/integration/git_transactions/test_binding_noop.py` |  |
| `test_slice5c2_boundaries.py` | replace-split | `tests/contracts/git_transactions/test_safety_boundaries.py`<br>`private source-freeze/path guard` |  |
| `test_slice5c2_cas_commit.py` | migrate | `tests/integration/git_transactions/test_cas_commit.py` |  |
| `test_slice5c2_fixtures.py` | support-rewrite | `tests/support/git/local_repo.py` | remove private baseline dependency |
| `test_slice5c2_repo_tree.py` | migrate | `tests/integration/git_transactions/test_repo_tree.py` |  |
| `test_slice5c2_review_fixes.py` | merge | `tests/integration/git_transactions/test_cas_commit.py`<br>`tests/integration/git_transactions/test_repo_tree.py` |  |
| `test_slice5d1_boundaries.py` | rewrite-split | `tests/contracts/git_transactions/test_remote_publisher_safety.py`<br>`private repository source-freeze guard` |  |
| `test_slice5d1_remote_publisher.py` | migrate | `tests/integration/git_transactions/test_remote_publisher.py` | local synthetic remote only |
| `test_slice5d2a1_fact_provider.py` | migrate | `tests/integration/runtime_sources/test_fact_provider.py` |  |
| `test_slice5d2a2a_production_authority.py` | rewrite-split | `tests/contracts/runtime/test_runtime_authority.py`<br>`tests/fixtures/bootstrap/synthetic_authority.json` | no Snowfall authority values |
| `test_slice5d2a2b0_fact_sources.py` | migrate | `tests/integration/runtime_sources/test_fact_sources.py` |  |
| `test_slice5d2a2b1_production_provider.py` | migrate | `tests/integration/runtime_sources/test_production_provider.py` |  |
| `test_slice5d2a2c_production_composition.py` | migrate | `tests/integration/runtime_sources/test_production_composition.py` |  |
| `test_slice5d2b_production_handoff.py` | split-private | `tests/integration/runtime/test_production_launcher.py`<br>`private workflow-wiring tests` | no private workflow YAML in public |
| `test_slice5e1_operator_ledger.py` | split | `tests/unit/operator/test_ledger.py`<br>`tests/contracts/operator/test_history.py` |  |
| `test_slice5e2_operator_transaction.py` | migrate | `tests/integration/operator/test_transaction.py` |  |
| `test_slice5e3_upgrade_gates.py` | rewrite-migrate | `tests/integration/operator/test_upgrade_gates.py` | synthetic authority/runtime |
| `test_slice5e4_operator_workflow.py` | split-private | `tests/unit/operator/test_request_artifact.py`<br>`tests/integration/operator/test_launcher.py`<br>`private workflow-wiring tests` |  |
| `test_slice5e5_soak.py` | decompose-soak | `tests/soak/test_restart_equivalence_72h.py`<br>`tests/soak/test_structural_28d.py`<br>`tests/soak/test_invariants_90d.py` | private workflow/process assertions stay private |
| `test_spatial_bootstrap_binding.py` | rewrite-migrate | `tests/integration/spatial/test_bootstrap_binding.py` |  |
| `test_spatial_context.py` | rewrite-split | `tests/unit/spatial/test_context.py`<br>`tests/contracts/spatial/test_context_schema.py` | remove private baseline coupling |
| `test_spatial_production_context.py` | private-replace | `private production-artifact test`<br>`tests/contracts/spatial/test_synthetic_context.py`<br>`tests/fixtures/spatial/synthetic_city.json` |  |
| `test_spatial_runtime_projection.py` | rewrite-migrate | `tests/integration/spatial/test_runtime_projection.py` |  |

## Naming rules for migrated tests

New test names describe the contract directly.

Preferred:

- `test_active_same_source_is_rejected_while_continuation_is_feasible`
- `test_rule_ids_are_unique_and_canonically_sorted`
- `test_partitioned_execution_matches_continuous_execution`
- `test_history_append_rejects_non_monotonic_event`
- `test_cas_conflict_does_not_publish`

Not allowed as permanent names:

- `test_issue157_...`
- `test_slice5b2c3_...`
- `test_review_fix_...`
- `test_rereview_...`
- `test_regression_for_pr_...`

Issue/PR identifiers belong in commit history or an explanatory comment, not in the public API of the test suite.

## File-size rule

The migration should not recreate the current very large test modules.

Guideline:

- target: under ~400 lines per normal test module;
- split when one file owns multiple subsystems or fixture construction dominates assertions;
- shared setup above ~30-40 lines used by multiple modules belongs in `tests/support/`;
- a single scenario definition may be large if it is data-oriented and remains easy to inspect.

This is a maintainability guideline, not a semantic pass/fail threshold.

## CI selection

Use markers as explicit positive selection.

Fast target expression:

```text
pytest -m "unit or contract or publication or (integration and not slow)"
```

Full target:

```text
pytest
```

Long horizon tests carry both semantic and cost markers, for example:

```python
@pytest.mark.soak
@pytest.mark.slow
def test_restart_equivalence_72h(...):
    ...
```

`slow` is a cost marker only. It must never be inferred from a historical class/file prefix.

## Migration order after the export gate opens

### Batch A — support extraction

Move and clean the 8 historical support/harness files plus zero-test fixture modules.

Goal: remove test-to-test imports before migrating assertions.

### Batch B — foundation through social

Migrate foundation, policy, Human State, decisions, schedule, routine and social tests.

Goal: establish the clean unit/contract baseline first.

### Batch C — world state and capture

Migrate reducers and capture/Camera Roll tests with wholly synthetic fixtures.

### Batch D — runtime lifecycle

Migrate runtime bundle, activity materialization, lifecycle, finalization, runtime capture and orchestrator tests.

### Batch E — persistence and Git

Migrate persistence, local Git transactions and remote-publisher abstractions.

### Batch F — production-runtime primitives, operator and spatial

Migrate only generic semantics. Keep Snowfall workflow wiring, production artifacts and private authority values private.

### Batch G — shared scenario/calibration/soak

Rebuild 72h/28d/90d coverage on the cleaned support layer. Do not copy historical per-feature soak harnesses as independent implementations.

### Batch H — publication and deterministic matrix

Add publication guards, timezone/PYTHONHASHSEED matrix, coverage baseline and final private-content scan.

## Completion condition

The historical `tests/life/` suite can be retired only when:

1. all mapped public contracts are represented in their destination files;
2. all private-only assertions remain covered in `project-snowfall`;
3. no public test imports another `test_*.py`;
4. no private SHA/source transform exists in public tests/support;
5. Fast and Full public CI are green;
6. 72h continuous vs restart execution is semantically equivalent;
7. 28d structural and 90d invariant runners are green;
8. migration coverage is reviewed against this 146-file map.

## Export gate

This plan does not authorize source export.

Engine/test source transfer remains blocked until private #152 validation is complete, PR #154 is merged, and the exact approved post-validation private main SHA is fixed.
