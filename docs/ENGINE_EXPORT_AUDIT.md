# Engine Source Export Audit

Approved private source baseline:

`is4mu/project-snowfall@458e05673a8d4a8f3b2346825b2eef8419411d64`

Scope audited: `engine/life/*`

## Result

- source files: **77**
- publish as-is: **55**
- publish with documentation-only cleanup: **16**
- publish with explicit generalization: **6**
- private-only Engine modules: **0**

The reusable Engine code itself contains no private character/runtime payload. The private boundary is carried primarily by application data, workflow wiring, historical documentation, and a small number of repository-layout/identity constants.

## Dependency closure

Python baseline: **3.12**

Runtime Python dependency:

- `jsonschema>=4,<5`

The schema layer also imports `referencing`, supplied by the jsonschema dependency stack.

System dependency for Git-backed functionality:

- `git`

Core deterministic simulation does not require a live network or GitHub API.

The Git transaction / publisher / production/operator launcher modules shell out to Git and support optional caller-supplied bearer-token environment names. Public tests for those modules must use local repositories/bare remotes and never require credentials.

## Security/privacy scan

No credential values or private-key material were found in `engine/life` at the approved SHA.

No direct code reference was found to:

- private workflow filenames;
- `project-snowfall` GitHub URLs;
- private character/environment assets;
- live GitHub API endpoints.

Historical application wording and path conventions still exist in a small set of files and are listed below for approved transformation.

## Documentation-only cleanup

These modules are executable-public as-is except for comments/docstrings that refer to the originating application's character/activation state:

- `engine/life/activity_finalization.py`
- `engine/life/activity_lifecycle.py`
- `engine/life/activity_materialization.py`
- `engine/life/activity_runtime.py`
- `engine/life/bootstrap.py`
- `engine/life/capture_decisions.py`
- `engine/life/cli.py`
- `engine/life/derived.py`
- `engine/life/runtime_capture.py`
- `engine/life/runtime_fact_provider.py`
- `engine/life/runtime_orchestrator.py`
- `engine/life/runtime_remote_publisher.py`
- `engine/life/runtime_spatial.py`
- `engine/life/schedule_state.py`
- `engine/life/social_response.py`
- `engine/life/spatial_context.py`

Allowed change: wording only. Executable semantics must remain byte-equivalent after ignoring comments/docstrings where practical.

## Explicit generalization

| File | Approved transformation |
|---|---|
| `engine/life/policy_invariants.py` | remove historical private-character field token from generic person-specific-field guard |
| `engine/life/README.md` | rewrite generic engine documentation; remove private workflow/Yukino/private-path history |
| `engine/life/runtime_bundle.py` | remove Snowfall legacy runtime path references and unused legacy-path constant |
| `engine/life/runtime_git_transaction.py` | replace private-repository invalid commit email; remove private activation wording |
| `engine/life/runtime_persistence.py` | remove Snowfall legacy data/runtime special-case/comment; preserve authoritative namespace behavior |
| `engine/life/runtime_production_authority.py` | replace Snowfall fixed data/runtime/life authority layout with public generic authority layout |

### Notes

`runtime_production_authority.py`

The loader contract is reusable, but its current fixed source layout is an application repository layout. The public form keeps fixed, fail-closed authority loading but moves the default authority documents to a generic public configuration namespace. Private Snowfall reconnect owns adaptation to its private authority storage.

`runtime_git_transaction.py`

The Git author/committer identity remains deterministic but changes from the private-repository invalid email to a public Snowfall Life Engine invalid-domain identity. This may change Git commit object SHA values in Git-transaction tests; modeled-life semantic hashes/history contracts must not change.

`runtime_persistence.py`

The special `data/runtime/` legacy-ignore branch is redundant with the general rule that non-authoritative namespaces are not consulted. The public extraction removes the private historical path wording without widening authoritative state inputs.

`policy_invariants.py`

The generic policy contract continues to reject person-specific social fields. The originating character-name key is not part of the public policy vocabulary and is removed from that historical denylist.

## Retained generic conventions

The following are intentionally public engine conventions, not private data:

- `refs/heads/life` as the runtime Git ref;
- `ops/life/operator-requests/<sha256>.json` as a reviewed operator request path;
- optional `GITHUB_TOKEN` / `GITHUB_STEP_SUMMARY` environment integration in CLI launchers;
- local Git CAS / force-with-lease semantics.

These features do not embed credentials and do not require live GitHub access in public tests.

## Export rule

No private Git history is copied.

Every exported file is recreated in the public repository from reviewed source bytes or an explicitly listed approved transformation. Source provenance is recorded only by the approved private baseline SHA.

The first public baseline must pass:

1. engine/schema import closure;
2. forbidden-content scan;
3. synthetic fixture-only test scan;
4. Fast CI;
5. Full CI;
6. 72h restart/equivalence system test;
7. local-only Git transaction/publisher tests.
