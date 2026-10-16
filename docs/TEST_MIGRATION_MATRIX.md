# Historical Test Migration Matrix

> [!NOTE]
> **Historical pre-release design/audit record.** The v0.1.0 public baseline was released on 2026-10-08 at `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`. Future-tense export steps below describe the completed extraction process, not the current project phase. See [README](../README.md) and [V1_BOUNDARY.md](V1_BOUNDARY.md) for current authority.

Original status (at time of drafting): Phase 0 design, before the engine-code export.

Source audit baseline:

- private repository: `is4mu/project-snowfall`
- private main: `9fe73f78ef8845d8c3b330faa7bf40b2a76c9fb0`
- audited Python files under `tests/life/`: 146
- executable test files: 138
- support/harness files: 8

This matrix translates the historical private suite into the public taxonomy defined by [TESTING.md](./TESTING.md).

The migration is intentionally semantic. Historical filenames, slice numbers, issue numbers, rereview rounds, private repository baselines, and workflow-wiring assertions are not public taxonomy.

## Disposition vocabulary

- **migrate** — keep the behavior and move it under the owning public test layer/subsystem.
- **merge** — preserve the assertions, but merge them into a clearer owning test module.
- **support** — move reusable builders/harnesses out of test modules into `tests/support/`.
- **consolidate** — retain the invariant but run it through shared scenario/soak infrastructure instead of a feature-specific long-run copy.
- **rewrite** — preserve the engine contract but replace private data/history/process coupling with synthetic/public fixtures.
- **private-only** — keep in `project-snowfall`; it tests Snowfall application/process wiring rather than the reusable engine.

## Non-negotiable migration rules

1. No public `test_*.py` imports another `test_*.py`.
2. No public test depends on a private Git SHA, `git show` of private history, or cumulative source-code text transforms.
3. Historical bug regressions are renamed by behavior, not issue number.
4. Historical `rereview*`, `review_fixes`, `boundaries`, and slice names are dissolved into stable owners.
5. Long-horizon tests share scenario runners and invariant libraries.
6. All public runtime/application fixtures are synthetic.
7. Private workflow YAML and Snowfall production runtime artifacts remain private.
8. Generic Git/publisher/operator semantics may remain public when exercised only against temporary local repositories/remotes.

## Support / harness migration

| Historical file | Disposition | Public destination / responsibility |
|---|---|---|
| `helpers.py` | split + rewrite | `tests/support/builders/`, `tests/support/assertions/`, `tests/support/git/`, `tests/support/constants.py`; remove private SHA/source transforms |
| `synthetic_adapter_harness.py` | support | `tests/support/harnesses/domain_adapters.py` |
| `synthetic_harness.py` | support | `tests/support/harnesses/dynamics.py` |
| `synthetic_integrated_behavior_harness.py` | support | `tests/support/harnesses/integrated_behavior.py` |
| `synthetic_resolver_harness.py` | support | `tests/support/harnesses/resolver.py` |
| `synthetic_routine_harness.py` | support | `tests/support/harnesses/routines.py` |
| `synthetic_social_harness.py` | support | `tests/support/harnesses/social_opportunities.py` |
| `synthetic_social_response_harness.py` | support | `tests/support/harnesses/social_response.py` |

## Foundation, clock, history, serialization

| Historical files | Disposition | Public owner |
|---|---|---|
| `test_foundation_core.py` | merge | `tests/unit/foundation/` + `tests/contracts/foundation/` |
| `test_advance_and_history.py` | merge | `tests/integration/time_history/` |
| `test_bootstrap_and_isolation.py` | migrate | `tests/integration/bootstrap/` |
| `test_cli.py` | migrate | `tests/integration/cli/` |
| `test_rereview2.py` … `test_rereview6.py` | merge | dissolve into time/history/checkpoint/schema/invariant owners; no rereview filenames |
| `test_brand_v1_integrity.py` | private-only unless genericized separately | repository/application branding is not Life Engine semantics |

The rereview files contain durable contracts such as atomic failure behavior, temporal ordering, canonical timestamps, append-only history, queue validity, provenance, effect validation, and partition invariance. Those contracts survive; the historical review-round containers do not.

## Policy, Human State, resolver, scheduling, social

| Historical family | Disposition | Public owner |
|---|---|---|
| `test_slice2a_contracts.py` | merge | `tests/contracts/core/` |
| `test_slice2a_policy.py` | rewrite + merge | `tests/contracts/policy/`, using a public synthetic policy/canon fixture |
| `test_slice2b_derived.py` | migrate | `tests/unit/state/derived/` |
| `test_slice2b_dynamics.py` | migrate | `tests/unit/state/dynamics/` |
| `test_slice2b_synthetic.py` | split | fast dynamics scenarios + shared 28d calibration |
| `test_slice2c_scenarios.py` | split | `tests/scenarios/daily_life/` + shared soak |
| `test_slice2c_schema.py` | merge | `tests/contracts/schemas/` + invariant ownership |
| `test_slice2d_candidates.py` | migrate | `tests/unit/decisions/candidates/` |
| `test_slice2d_resolver.py` | split | `tests/unit/decisions/resolver/` + shared resolver soak |
| `test_issue155_resolver_rule_ids.py` | merge | resolver provenance contract: duplicate rule IDs canonicalize correctly |
| `test_issue157_active_same_source.py` | merge | resolver continuation contract: active same-source activity is not restarted |
| `test_slice2e_commitments.py` | migrate | `tests/unit/schedule/commitments/` |
| `test_slice2e_tasks.py` | migrate | `tests/unit/schedule/tasks/` |
| `test_slice2e_routes_integration.py` | migrate | `tests/integration/decision_runtime/` |
| `test_slice2e_soak.py` | consolidate | shared 28d/90d soak invariants |
| `test_slice2f_exercise.py` | migrate | `tests/unit/routines/exercise/` |
| `test_slice2f_household.py` | migrate | `tests/unit/routines/household/` |
| `test_slice2f_integration.py` | migrate | `tests/integration/decision_runtime/routines/` |
| `test_slice2f_soak.py` | consolidate | shared soak |
| `test_slice2g_contact.py` | migrate | `tests/unit/social/opportunities/` |
| `test_slice2g_invites.py` | migrate | `tests/unit/social/invites/` |
| `test_slice2g_post_work.py` | migrate | `tests/scenarios/social/` |
| `test_slice2g_slots.py` | migrate | `tests/unit/social/slots/` |
| `test_slice2g_soak.py` | consolidate | shared social soak parameters |
| `test_slice2h_invites.py` | migrate | `tests/unit/social/response/` |
| `test_slice2h_response.py` | migrate | `tests/unit/social/response/` |
| `test_slice2h_review1.py` | merge | social opportunity/response owners |
| `test_slice2h_integration.py` | rewrite + migrate | `tests/integration/social_schedule/`, synthetic runtime data only |
| `test_slice2h_soak.py` | consolidate | shared soak |
| `test_slice2i_cross_domain.py` | migrate | `tests/integration/cross_domain/` |
| `test_slice2i_calibration.py` | consolidate | `tests/calibration/behavior/` |
| `test_slice2i_invariants.py` | rewrite + consolidate | `tests/soak/invariants/`; remove import from `test_slice2i_calibration.py` |

## World/domain reducers

| Historical family | Disposition | Public owner |
|---|---|---|
| `test_slice3a_world_state.py` | rewrite + merge | `tests/unit/world_state/` + `tests/contracts/world_state/` |
| `test_slice3a_soak.py` | rewrite + consolidate | shared soak with synthetic world fixture |
| `test_slice3b_relation_reducer.py` | rewrite + migrate | `tests/unit/reducers/relations/` |
| `test_slice3b_soak.py` | rewrite + consolidate | shared soak |
| `test_slice3c_home_consumables_reducer.py` | rewrite + migrate | `tests/unit/reducers/consumables/`; generic synthetic inventory terminology |
| `test_slice3c_soak.py` | rewrite + consolidate | shared soak |
| `test_slice3d_wardrobe_reducer.py` | rewrite + migrate | `tests/unit/reducers/wardrobe/` with synthetic catalog |
| `test_slice3d_soak.py` | rewrite + consolidate | shared soak |
| `test_slice3e_finance_reducer.py` | rewrite + migrate | `tests/unit/reducers/finance/` with synthetic accounts |
| `test_slice3e_soak.py` | rewrite + consolidate | shared soak |

Private canon/environment references and private-history exact-source checks are removed from these public tests while preserving reducer semantics.

## Capture / Camera Roll

| Historical family | Disposition | Public owner |
|---|---|---|
| `test_slice4a_capture_camera_roll.py` | split + migrate | `tests/unit/captures/` + `tests/integration/capture_pipeline/` |
| `test_slice4a1_capture_subject_kind.py` | rewrite + merge | capture contracts with synthetic subjects |
| `test_slice4a_soak.py` | rewrite + consolidate | shared capture soak |
| `test_slice4b1_capture_decisions.py` | rewrite + migrate | `tests/unit/captures/decisions/`; remove cross-test helper import |
| `test_slice4b1_soak.py` | consolidate | shared soak |
| `test_slice4b2_capture_emitters.py` | rewrite + migrate | `tests/integration/capture_pipeline/`; shared support builders |
| `test_slice4b2_soak.py` | consolidate | shared soak |
| `test_slice4b3_capture_repeats.py` | rewrite + migrate | `tests/unit/captures/repeats/` + integration |
| `test_slice4b3_soak.py` | consolidate | shared soak |
| `test_slice4c_viewer_migration.py` | split | retain schema/data compatibility contracts; private viewer/application wiring stays private if present |

## Runtime bundle, activity lifecycle, persistence

| Historical family | Disposition | Public owner |
|---|---|---|
| `test_slice5a_runtime_bundle.py` | rewrite + migrate | `tests/contracts/runtime_bundle/` + `tests/integration/runtime_bundle/` |
| `test_slice5a_schedule_state.py` | migrate | `tests/unit/schedule/state/` |
| `test_slice5b1_activity_runtime.py` | migrate | `tests/unit/activities/runtime/` |
| `test_slice5b1_runtime_effects.py` | migrate | `tests/integration/activity_lifecycle/effects/` |
| `test_slice5b1_schedule_reducer.py` | migrate | `tests/unit/schedule/reducer/` |
| `test_slice5b1_boundaries.py` | replace | semantic/schema contracts survive; private SHA/source-freeze/path-diff guards do not |
| `test_slice5b2a_runtime_decision.py` | migrate | `tests/integration/decision_runtime/` |
| `test_slice5b2a_boundaries.py` | replace/private split | module ownership contracts become public semantic tests; private repository scope guards stay private |
| `test_slice5b2b_social_state.py` | migrate | `tests/unit/social/runtime_state/` |
| `test_slice5b2b_social_runtime.py` | migrate | `tests/integration/social_schedule/` |
| `test_slice5b2b_boundaries.py` | replace/private split | same boundary policy |
| `test_slice5b2c1_activity_materialization.py` | split + migrate | `tests/unit/activities/materialization/` + integration |
| `test_slice5b2c1_boundaries.py` | replace/private split | semantic ownership only in public |
| `test_slice5b2c2a_active_interval.py` | migrate | `tests/integration/activity_lifecycle/intervals/` |
| `test_slice5b2c2a_wakeups.py` | migrate | `tests/integration/activity_lifecycle/wakeups/` |
| `test_slice5b2c2a_boundaries.py` | replace/private split | semantic ownership only in public |
| `test_slice5b2c2b_effects.py` | migrate | `tests/integration/activity_lifecycle/effects/` |
| `test_slice5b2c2b_finalization.py` | migrate | `tests/integration/activity_lifecycle/finalization/` |
| `test_slice5b2c2b_finish_context.py` | merge | finalization context contracts |
| `test_slice5b2c2b_boundaries.py` | replace/private split | semantic ownership only in public |
| `test_slice5b2c2c_fixtures.py` | support | `tests/support/builders/capture_runtime.py` |
| `test_slice5b2c2c_primary.py` | migrate | `tests/integration/capture_pipeline/runtime/` |
| `test_slice5b2c2c_repeat.py` | migrate | same owner |
| `test_slice5b2c2c_runtime_bind.py` | migrate | same owner |
| `test_slice5b2c2c_camera_roll.py` | migrate | same owner |
| `test_slice5b2c2c_c2b_integration.py` | merge | activity-finalization → capture integration |
| `test_slice5b2c2c_mutation.py` | merge | append-only / mutation rejection contract |
| `test_slice5b2c2c_review_fixes.py` | merge | owning capture/runtime contracts |
| `test_slice5b2c2c_boundaries.py` | replace/private split | semantic ownership only in public |

## Runtime orchestrator

| Historical files | Disposition | Public owner |
|---|---|---|
| `test_slice5b2c3_fixtures.py` | support | `tests/support/builders/runtime_orchestrator.py` |
| `test_slice5b2c3_entry.py` | migrate | `tests/integration/runtime_orchestrator/entry/` |
| `test_slice5b2c3_continue_select_start.py` | migrate | `tests/integration/runtime_orchestrator/selection/` |
| `test_slice5b2c3_decision_social.py` | merge | decision/social orchestrator contracts |
| `test_slice5b2c3_immediate.py` | migrate | immediate-event semantics |
| `test_slice5b2c3_post_finalize.py` | migrate | post-finalization semantics |
| `test_slice5b2c3_no_active.py` | merge | no-active runtime behavior |
| `test_slice5b2c3_queue_ordering.py` | migrate | queue ordering/invariants |
| `test_slice5b2c3_history_budget_stable.py` | migrate | history-budget determinism |
| `test_slice5b2c3_capture_repeat.py` | merge | orchestrator/capture repeat integration |
| `test_slice5b2c3_integration.py` | merge | orchestrator end-to-end integration |
| `test_slice5b2c3_boundaries.py` | replace/private split | public semantic boundaries + private repository guards |

## Persistence and local Git transactions

| Historical files | Disposition | Public owner |
|---|---|---|
| `test_slice5c1_fixtures.py` | support + rewrite | `tests/support/builders/persistence.py`; remove private-baseline dependency |
| `test_slice5c1_baseline_verify.py` | replace | persistent semantic baseline/golden contracts, not private commit baseline |
| `test_slice5c1_c3_handoff.py` | migrate | `tests/integration/persistence/handoff/` |
| `test_slice5c1_camera.py` | migrate | persistence/camera integration |
| `test_slice5c1_closed_integrity.py` | migrate | closed-history integrity |
| `test_slice5c1_isolation.py` | migrate | persistence isolation |
| `test_slice5c1_last_run.py` | migrate | last-run metadata |
| `test_slice5c1_noop_revision.py` | migrate | no-op revision stability |
| `test_slice5c1_social_persistence.py` | migrate | social persistence |
| `test_slice5c1_timeline.py` | migrate | timeline persistence |
| `test_slice5c1_review_fixes.py` | merge | persistence owners |
| `test_slice5c1_boundaries.py` | replace/private split | public API/schema contracts + private process guards |
| `test_slice5c2_fixtures.py` | support + rewrite | `tests/support/git/local_repo.py` |
| `test_slice5c2_cas_commit.py` | migrate | `tests/integration/git_transactions/cas/` |
| `test_slice5c2_repo_tree.py` | migrate | local repository tree semantics |
| `test_slice5c2_binding_noop_change.py` | migrate | binding/no-op semantics |
| `test_slice5c2_review_fixes.py` | merge | Git transaction owners |
| `test_slice5c2_boundaries.py` | replace/private split | public no-network/no-dangerous-command contracts + private source-freeze/path guards |

## Remote publisher and production-runtime primitives

| Historical files | Disposition | Public owner |
|---|---|---|
| `test_slice5d1_remote_publisher.py` | migrate | `tests/integration/git_transactions/remote_publisher/`, local synthetic remote only |
| `test_slice5d1_boundaries.py` | rewrite/private split | retain narrow publisher/ref/CAS safety contracts; private path/source freeze stays private |
| `test_slice5d2a1_fact_provider.py` | migrate | `tests/integration/runtime_sources/fact_provider/` |
| `test_slice5d2a2a_production_authority.py` | rewrite + migrate | generic authority loader contract with synthetic authority fixture |
| `test_slice5d2a2b0_fact_sources.py` | migrate | generic production fact-source composition |
| `test_slice5d2a2b1_production_provider.py` | migrate | generic provider integration |
| `test_slice5d2a2c_production_composition.py` | migrate | generic composition integration |
| `test_slice5d2b_production_handoff.py` | split | generic pinned-launcher / fail-closed / local-CAS behavior public; private workflow-YAML assertions private-only |

## Operator correction / recovery / upgrade

| Historical files | Disposition | Public owner |
|---|---|---|
| `test_slice5e1_operator_ledger.py` | migrate | `tests/unit/operator/` + `tests/contracts/operator/` |
| `test_slice5e2_operator_transaction.py` | migrate | `tests/integration/operator/transactions/` |
| `test_slice5e3_upgrade_gates.py` | rewrite + migrate | `tests/integration/operator/upgrades/`, synthetic authority/runtime |
| `test_slice5e4_operator_workflow.py` | split | generic artifact validation/launcher/operator semantics public; private workflow-YAML wiring private-only |
| `test_slice5e5_soak.py` | decompose + consolidate | hard engine/runtime invariants → shared 72h/28d/90d soak; Snowfall workflow/process assertions remain private |

The 72h continuous-vs-partitioned restart equivalence is a first-class public system invariant and must not be weakened during consolidation.

## Spatial

| Historical files | Disposition | Public owner |
|---|---|---|
| `test_spatial_context.py` | rewrite + migrate | `tests/unit/spatial/context/` + schema contracts; remove private baseline coupling |
| `test_spatial_bootstrap_binding.py` | rewrite + migrate | `tests/integration/spatial/bootstrap/` |
| `test_spatial_runtime_projection.py` | rewrite + migrate | `tests/integration/spatial/runtime_projection/` |
| `test_spatial_production_context.py` | private-only + public replacement | keep Snowfall artifact/hash/path test private; add wholly synthetic public SpatialContext fixture and equivalent generic contracts |

## Public soak architecture

Historical feature-specific long tests are not copied one-for-one.

Public shared runners:

- `tests/soak/test_restart_equivalence_72h.py`
- `tests/soak/test_structural_28d.py`
- `tests/soak/test_invariants_90d.py`

Reusable invariants belong in `tests/support/assertions/invariants.py`.

Reusable synthetic worlds/scenarios belong in `tests/support/scenarios/`.

Feature-specific long-run expectations are parameters or calibration contracts rather than independent duplicated harnesses.

## CI migration

The historical `scripts/run_fast_checks.py` class-prefix exclusion manifest is retired.

Public CI selects explicitly marked layers:

- **Fast:** unit + contract + publication + fast integration
- **Full:** all public tests, including scenario, calibration, and soak
- **Release/main matrix:** Full + self-check + deterministic environment matrix + coverage/publication guard

The public runner is pytest. Existing `unittest.TestCase` bodies may remain temporarily while files are reorganized.

## Known architectural defects confirmed by audit

1. Multiple public-candidate tests currently import other `test_*.py` modules.
2. Several zero-test `*_fixtures.py` modules act as hidden support APIs.
3. `helpers.py` mixes ordinary builders with private historical SHA/source transforms.
4. Historical boundary tests mix semantic contracts with repository-process governance.
5. Feature-specific soak suites duplicate long-horizon infrastructure.
6. Current Fast CI identifies slow tests by historical class-name prefixes and exact count.
7. Several tests bind generic engine code to private workflow YAML or private runtime artifacts.
8. Some reducer/capture/spatial tests mix synthetic semantics with application-specific canon/environment paths.
9. Issue/review/slice naming obscures stable ownership.
10. The existing suite is strong semantically, but its dependency graph and taxonomy reflect development history rather than a reusable library.

## Export gate

This document does **not** authorize engine export.

The first source/test export remains blocked until the private #152 validation is complete, PR #154 is merged, and the approved post-validation source SHA is fixed.

At export time, every historical source test listed above must receive a concrete destination file or an explicit private-only record before the old suite is retired.
