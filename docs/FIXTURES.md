# Fixture Architecture

Status: Phase 0 design artifact. No engine source or private runtime data has been exported.

## Principles

Public fixtures are inputs, not hidden authority.

Every fixture must be:

- wholly synthetic;
- deterministic;
- small enough to inspect;
- owned by one semantic domain;
- explicit about version and purpose;
- free of private character, environment, runtime, workflow, issue, PR, or Git-history references.

A fixture may model production-shaped data, but it must not contain Snowfall production values.

## Fixture classes

### Input fixtures

Stable synthetic inputs used by unit/integration tests.

Examples:

- Behavior Policy;
- bootstrap proposal / authority;
- spatial graph;
- schedule/task seed data;
- social graph;
- wardrobe/catalog;
- finance/account state.

### Scenario fixtures

Human-readable exogenous inputs for scenario/soak tests.

They describe facts and opportunities, not expected chronological engine output.

### Golden fixtures

Allowed only for contractual serialized representations.

Examples:

- versioned checkpoint;
- closed history shard;
- Camera Roll shard;
- schema compatibility sample.

Golden files must not be Python implementation snapshots.

## Target layout

```text
tests/fixtures/
  policy/
    v1_test_fixture.json
    v2_synthetic_candidate.json
  bootstrap/
    proposal.json
    synthetic_authority.json
  spatial/
    synthetic_city.json
  social/
  schedule/
  wardrobe/
  finance/
  golden/
    camera_roll_v2_shard.json
    checkpoints/
    history/
```

## Existing private fixture disposition

| Private source | Public disposition |
|---|---|
| `tests/fixtures/life/behavior_policy_fixture.json` | migrate/rename to `tests/fixtures/policy/v1_test_fixture.json` |
| `tests/fixtures/life/behavior_policy_v2_candidate_fixture.json` | rewrite as `tests/fixtures/policy/v2_synthetic_candidate.json` |
| `tests/fixtures/life/bootstrap_proposal_fixture.json` | migrate/rename to `tests/fixtures/bootstrap/proposal.json` |
| `tests/fixtures/life/camera_roll_viewer_v2_shard.json` | migrate engine-compatible data to `tests/fixtures/golden/camera_roll_v2_shard.json`; viewer wiring is not an engine fixture |

## Behavior Policy v2 rewrite

The historical v2 candidate is not copied mechanically because it contains application-specific provenance, including private-project paths/issues and Yukino-specific wording.

The public synthetic replacement preserves only reusable contract shape and test-relevant values.

Requirements:

- synthetic character ID;
- synthetic provenance refs such as `fixture:policy-source:...`;
- no `character/...` paths;
- no GitHub Issue/PR URLs;
- no Yukino/Snowfall character wording;
- no approval claim;
- explicit note that the file is synthetic and not production authority.

A `policy_mode` value that exercises production-mode validation may be retained if all authority/provenance values are synthetic and the fixture is clearly marked as non-approved test data.

## Builders vs fixtures

Use a checked-in JSON fixture when the serialized representation itself matters or the object is shared broadly.

Use a Python builder when a test needs small variations of an object and the serialized bytes are not contractual.

Do not maintain dozens of near-duplicate JSON files solely to vary one field.

## Golden update rule

Golden fixture updates require an explicit contract reason.

A failing golden test must not be fixed by blindly regenerating expected output.

The change must identify whether it is:

- intentional schema/serialization evolution;
- a bug fix with an approved compatibility impact;
- an accidental semantic drift.

## Synthetic identity rule

Synthetic IDs should make their status obvious:

- `char-synthetic-001`
- `loc-synthetic-home`
- `fixture-policy-v2`
- `fixture-world-seed`

Do not use real/private entity names disguised as examples.

## Time and timezone

Fixtures use explicit RFC3339 timestamps with offsets.

The runner's wall clock is never semantic authority.

`Asia/Tokyo` is valid fixture data when a timezone-sensitive case is intentional, but correctness must also be verified under runner `TZ=UTC` where appropriate.

## Network and filesystem

Fixtures never point to live HTTP resources as required inputs.

Temporary local Git repositories/remotes are created by `tests/support/git/local_repo.py`, not stored as fixture repositories.

## Migration acceptance

Before first public Full CI:

1. all checked-in fixtures pass the private-content scan;
2. every fixture is referenced by at least one public test or documented example;
3. no fixture contains private Snowfall paths or identifiers;
4. golden fixtures are limited to contractual representations;
5. scenario fixtures contain inputs, not pre-authored engine histories.
