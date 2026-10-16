# CI Tiers

Status: active public CI contract after the v0.1.0 release (P1 v1 stabilization in progress).

The initial public baseline uses **Python 3.12** because the pre-split private Life Engine validation runs on Python 3.12. Supporting additional Python versions is a separate post-baseline compatibility decision.

## Jobs

### Publication boundary

Runs on every push and pull request.

Responsibilities:

- required Phase 0/public files exist;
- exclusion manifest is respected;
- no unsafe public workflow trigger;
- test migration manifest is complete and internally consistent;
- no private workflow/data paths are introduced.

This job requires no engine dependencies.

### Fast

Runs on every pull request and main push after engine export.

Command:

```bash
python -m pytest -m "unit or contract or publication or (integration and not slow)"
```

Fast includes:

- all unit tests;
- all contract tests;
- all publication tests;
- integration tests not marked `slow`.

Fast selection is positive marker-based selection. There is no historical filename/class exclusion list and no fixed expected slow-test count.

### Full

Runs for non-draft pull requests and main/release validation.

Command:

```bash
python -m pytest
```

Full includes:

- Fast;
- scenarios;
- deterministic calibration;
- all soak tests.

A behavior-changing pull request is not merge-ready until Full is green.

### Determinism matrix

Runs on main validation (and is intended for release validation when release automation is enabled).

Selected semantic fingerprint/scenario tests run under combinations including:

- distinct `PYTHONHASHSEED` values;
- `TZ=UTC`;
- `TZ=Asia/Tokyo`.

The matrix verifies that unordered container iteration and host timezone do not change modeled semantics.

### Coverage

Coverage is collected by the Full job on main pushes and uploaded as `coverage.json`. Non-draft pull requests run the same Full semantics without making coverage a merge threshold.

Accepted public baseline (main Run #126, commit `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`):

- statement coverage: **80.84%** (12390 / 15327 lines);
- branch coverage: **64.78%** (4375 / 6754 branches);
- combined coverage.py percentage: **75.93%**.

Ratchet policy:

1. preserve this accepted baseline as the v0.1.0 reference point;
2. investigate any coverage decrease in a behavior-changing pull request;
3. require explicit review/rationale before accepting a regression;
4. prefer targeted branch-level tests for integrity-critical modules over chasing a global vanity target;
5. do not use a global 100% target as a substitute for semantic/invariant tests.

CI currently reports coverage and stores `coverage.json`. Turning the documented baseline into an automated numeric fail threshold is pre-v1 stabilization work and must account for intentional source additions rather than blindly rejecting all denominator growth.

## Markers

Required semantic markers:

- `unit`
- `contract`
- `integration`
- `scenario`
- `calibration`
- `soak`
- `publication`

Cost marker:

- `slow`

A test has one primary semantic marker. `slow` may be added independently.

Examples:

```python
@pytest.mark.unit
def test_rule_ids_are_unique_and_canonically_sorted():
    ...

@pytest.mark.integration
@pytest.mark.slow
def test_large_local_git_transaction_sequence():
    ...

@pytest.mark.soak
@pytest.mark.slow
def test_restart_equivalence_72h():
    ...
```

## Long-horizon ownership

Only three primary public long-horizon runners exist:

- `tests/soak/test_restart_equivalence_72h.py`
- `tests/soak/test_structural_28d.py`
- `tests/soak/test_invariants_90d.py`

Feature-specific long-run behavior is expressed through shared scenarios/invariants, not duplicate runners.

## Dependency installation

Initial engine/runtime dependency:

- `jsonschema>=4,<5`

Initial test dependencies:

- `pytest`
- `pytest-cov`

Do not add parallel/property-testing plugins during the initial semantic-preserving export unless a concrete migration need requires them.

## Pull request policy

Draft pull requests:

- Publication boundary;
- Fast.

Non-draft pull requests:

- Publication boundary;
- Fast;
- Full.

Main (and release-candidate validation when explicitly run):

- Publication boundary;
- Fast;
- Full;
- Determinism matrix;
- coverage reporting;
- package/self-check.

No public workflow uses private secrets for core tests.

No core test requires live GitHub access.

## Failure interpretation

- Unit/contract/integration failure: engine or contract regression.
- Scenario failure: end-to-end semantic regression.
- Calibration failure: deterministic public fixture behavior changed outside its approved envelope.
- Soak failure: long-horizon invariant/equivalence regression.
- Publication failure: repository boundary/security regression.

Do not weaken a semantic assertion solely to make a tier green.

## Initial export transition (historical)

During the completed initial export, existing `unittest.TestCase` bodies were allowed to execute under pytest while files were reorganized. The following sequence records the original migration policy.

Migration sequence:

1. preserve assertions;
2. remove cross-test imports via `tests/support/`;
3. attach semantic markers;
4. prove Fast and Full green;
5. only then modernize individual tests to native pytest style where useful.

This avoids mixing test-framework refactoring with semantic extraction.
