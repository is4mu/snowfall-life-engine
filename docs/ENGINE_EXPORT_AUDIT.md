# Engine Export Audit

Approved private source baseline:

`is4mu/project-snowfall@458e05673a8d4a8f3b2346825b2eef8419411d64`

Scope audited:

`engine/life/` top-level files: **77**

Audit purpose:
- prevent private Snowfall/Yukino data or workflow coupling from crossing the OSS boundary;
- preserve reusable engine semantics;
- distinguish mechanical debranding/generalization from private-only application wiring.

## Result

| Disposition | Count | Meaning |
| --- | ---: | --- |
| Direct export | 48 | Reusable engine source can be copied byte-for-byte from the approved SHA. |
| Genericize before export | 21 | Reusable semantics, but source contains private naming, fixed application paths, or coupling that must be transformed without weakening behavior. |
| Private-only / replace | 8 | Do not copy directly into the public repository. Replace with public documentation or later public adapters if needed. |
| **Total** | **77** | Complete top-level `engine/life/` inventory. |

No embedded credential, real character payload, real private runtime state, or private social-graph content was found in the audited source. The problems found are structural/publication-boundary issues rather than secret-value leaks.

## Direct export — 48

These files have no identified Snowfall-private data/path/workflow dependency and may be copied byte-for-byte from the approved source SHA:

```text
__init__.py
__main__.py
camera_roll.py
canonical.py
capture_emitters.py
capture_pipeline.py
capture_repeats.py
captures.py
checkpoint.py
clock.py
contracts.py
correction.py
decisions.py
domain_adapters.py
dynamics.py
effects.py
errors.py
events.py
finance_reducer.py
fixed_point.py
history.py
home_reducer.py
ids.py
invariants.py
last_operator_action.py
operator_action.py
operator_candidate.py
operator_history.py
operator_recovery.py
operator_request.py
policy.py
relation_reducer.py
requirements.txt
rng.py
routine_adapters.py
runtime_decision.py
runtime_effects.py
schedule_reducer.py
schema.py
social_opportunities.py
social_runtime.py
timeline.py
timeutil.py
versioning.py
wakeups.py
wardrobe_reducer.py
workspace.py
world_state.py
```

## Genericize before export — 21

### Documentation/debranding-only transforms

The following modules are reusable but contain historical Yukino/Snowfall language in module comments, docstrings, or explanatory text:

```text
activity_finalization.py
activity_lifecycle.py
activity_materialization.py
activity_runtime.py
bootstrap.py
capture_decisions.py
cli.py
derived.py
runtime_capture.py
runtime_fact_provider.py
runtime_orchestrator.py
runtime_spatial.py
schedule_state.py
social_response.py
spatial_context.py
```

Required transform:
- replace Yukino-specific wording with neutral “application character”, “production character”, or “private application” terminology;
- do not alter executable semantics.

### Application-path / branding transforms

`runtime_git_transaction.py`
- replace the private Git author identity `Snowfall Life Engine <life-engine@project-snowfall.invalid>` with the public project identity;
- replace Snowfall-specific commit-message/prefix strings;
- preserve transaction/CAS semantics byte-for-byte apart from those strings.

`operator_git_transaction.py`
- replace Snowfall-specific commit message and temporary-directory prefixes;
- preserve correction/upgrade Git transaction semantics.

`policy_invariants.py`
- replace the literal private-name sentinel `yukino` with a public fixture/private-identity sentinel that serves the same invariant purpose;
- retain the rule that production policy artifacts must not embed private character identity.

`runtime_bundle.py`
- remove direct coupling to legacy private paths `data/runtime/character.json` and `data/runtime/social-graph.json`;
- preserve the rule that legacy application-owned mutable files are not consumed as v2 engine state;
- express excluded legacy paths through a generic/application boundary instead of Snowfall-specific filenames.

`runtime_persistence.py`
- remove/generalize the private legacy `data/runtime/` path assumption;
- preserve all state/history persistence semantics and fail-closed path validation.

`operator_upgrade.py`
- currently imports private production authority/launcher composition;
- retain generic upgrade request/candidate/probe mechanics, but invert the production identity dependency behind an injected/public adapter;
- do not weaken target pinning, policy identity checks, or clean-checkout verification.

## Private-only / replace — 8

These files must not be copied directly in the initial OSS export:

```text
README.md
runtime_operator_launcher.py
runtime_production_authority.py
runtime_production_composition.py
runtime_production_fact_provider.py
runtime_production_fact_sources.py
runtime_production_launcher.py
runtime_remote_publisher.py
```

### Rationale

`README.md`
- private historical implementation document;
- contains extensive Yukino, private path, private workflow, and activation-history references;
- public repository already has a purpose-built README and architecture documents.

`runtime_production_authority.py`
- hardcodes application-owned production authority paths under `data/runtime/life/**`;
- loads private application authority/bootstrap/policy identities.

`runtime_production_composition.py`
- composes the private application production authority and production fact-provider stack.

`runtime_production_fact_provider.py`
- application production composition over the private authority contract.

`runtime_production_fact_sources.py`
- production-source interfaces are coupled to the private authority type.
- Generic source protocols may be extracted later if required by public API closure.

`runtime_production_launcher.py`
- owns GitHub/production handoff behavior, bearer-token plumbing, `refs/heads/life`, workflow summary output, and application production bootstrap behavior.

`runtime_operator_launcher.py`
- owns reviewed-request workflow integration, GitHub summary/token plumbing, and application production authority loading.

`runtime_remote_publisher.py`
- owns live remote publication/CAS behavior for the application `life` ref.
- The public baseline keeps local Git transaction semantics but does not ship a production remote publisher by default.

These private-only dispositions match the public/private contract: production workflow wiring, production credentials/authority, and application runtime publication remain in `project-snowfall`.

## Import-closure consequence

The initial public export must not simply delete the private-only modules if exported modules import them.

Known closure work:
- `operator_upgrade.py` must be refactored to depend on a generic injected identity/probe interface rather than `runtime_production_authority` / `runtime_production_launcher`.
- Any tests targeting private launchers/publishers stay private or are rewritten against local synthetic adapters.
- Public `__init__`, CLI, and schema registry must be checked for references to private-only modules/schemas.

The next gate is an explicit import/dependency closure audit before copying source.

## Transformation rules

All public transformations must satisfy:

1. no policy/resolver/activity timing changes;
2. no state schema weakening;
3. no determinism weakening;
4. no test deletion merely to make CI green;
5. private-path removal must be replaced by generic boundaries, not silently ignored;
6. no network/GitHub requirement in core tests;
7. local Git transaction tests remain synthetic/local-only;
8. transformed files record their private source SHA and transformation reason in the export manifest.

## Next step

1. audit module import closure for the 48 direct + 21 genericized candidates;
2. export the direct dependency-safe foundation batch;
3. add transformed modules only with focused semantic-equivalence tests;
4. migrate schemas and synthetic fixtures;
5. migrate tests into the semantic public layout;
6. run public Fast + Full + determinism + privacy guards before merging the export branch.
