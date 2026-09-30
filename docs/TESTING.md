# Testing Architecture

Snowfall Life Engine tests are organized by **stable semantic responsibility**, not by historical implementation slice, issue number, or review round.

This document is normative for the first public engine baseline.

## Goals

The test suite must be:

- deterministic;
- application-independent;
- understandable from file/test names;
- restart/persistence aware;
- strict about append-only history and semantic equivalence;
- free of private Snowfall data and Git history;
- free of live-network requirements;
- structured so long-horizon coverage is shared rather than duplicated.

## Test layers

### Unit

Pure or nearly pure subsystem behavior.

Examples:

- canonicalization and IDs;
- Human State dynamics;
- candidate/resolver behavior;
- schedule/task reducers;
- social opportunity/response logic;
- capture decisions;
- operator validation.

Unit tests do not use Git repositories or subprocesses.

### Contract

Versioned representations and externally meaningful guarantees.

Examples:

- JSON schema validity;
- canonical serialized forms;
- error-code behavior;
- persistent round trips;
- deterministic ordering;
- stable public import/API contracts.

Golden files are allowed only when the representation itself is contractual.

Python implementation source files are not golden contracts.

### Integration

Real subsystem boundaries.

Examples:

- decision -> activity -> finalization;
- runtime -> persistence -> reload -> continuation;
- local Git candidate/commit/CAS;
- correction/recovery;
- engine/policy upgrade gates.

Integration tests may use temporary local repositories and subprocess Git. They never require a live network or private workflow file.

### Scenario

Human-readable synthetic stories.

A scenario supplies facts, constraints, state, commitments, tasks, and opportunities. It does **not** supply a pre-authored chronological ActualEvent script.

Scenario tests should clearly separate:

- fixture inputs;
- engine-selected outcomes;
- invariants being asserted.

### Soak

Long-horizon deterministic validation built on a shared scenario runner and shared invariant library.

The initial public suite has three primary horizons:

1. **72h restart equivalence**
   - continuous execution;
   - exactly 12 x 6h partitioned execution;
   - serialization/reload between partitions;
   - semantic equivalence.

2. **28d structural**
   - domain participation and structural coverage;
   - no pathological loops/dead periods;
   - contractual bounded rates/counts only.

3. **90d invariant**
   - monotonic time/history;
   - bounded state;
   - no impossible overlaps;
   - no priority inversion;
   - no duplicate causal records;
   - deterministic fingerprints.

Feature-specific assertions should be scenario parameters or reusable invariants, not separate copies of the 28d/90d harness.

### Calibration

Deterministic policy-quality checks that assert expected distributions, rates, or behavioral bands for a specific public fixture policy.

Calibration is intentionally separate from hard engine invariants:

- invariant failure means the engine violated a correctness contract;
- calibration failure means a reviewed fixture policy no longer produces its expected deterministic behavioral envelope.

Calibration tests must use fixed synthetic inputs/seeds and must never be probabilistically flaky.

### Publication

Repository safety rather than engine behavior:

- no private paths/data;
- no secrets;
- no live-network requirement;
- safe public workflow permissions/triggers;
- export-manifest compliance.

## Target layout

```text
tests/
  support/
    builders/
    assertions/
    scenarios/
    fixtures.py
    constants.py

  fixtures/
    policy/
    bootstrap/
    schema/
    spatial/
    golden/

  unit/
  contracts/
  integration/
  scenarios/
  calibration/
  soak/
  publication/
```

Subsystem folders are created within those layers as needed.

## Shared test support

No public `test_*.py` file may import another `test_*.py` file.

Reusable setup belongs in `tests/support/`:

- builders create synthetic domain objects;
- assertions encode reusable invariants;
- scenario definitions encode reusable exogenous input;
- Git helpers create local temporary repositories/remotes.

Avoid a single catch-all `helpers.py` as the suite grows.

## Historical regression policy

A fixed bug becomes an ordinary contract of its owning subsystem.

Examples:

- duplicate resolver rule IDs belong in resolver provenance tests;
- active same-source restart belongs in active-continuation resolver tests.

Do not keep permanent files named after issue numbers.

Likewise, `review_fixes`, `rereview`, and implementation-slice names are migration artifacts, not public test taxonomy.

## Historical source-freeze policy

The public suite must not depend on private Git SHAs or reconstruct expected Python source through cumulative text transforms.

Replace those checks with one of:

- semantic invariant tests;
- explicit schema/API compatibility tests;
- versioned golden persistent data;
- private-only process guards that remain outside the OSS repository.

## Framework

The public runner is **pytest**.

Existing `unittest.TestCase` assertions may run under pytest during migration, which allows the suite to be reorganized without simultaneously rewriting every assertion.

New tests should prefer native pytest style.

Initial test dependencies:

- pytest;
- pytest-cov.

Additional property-testing or parallelization dependencies should wait until the first public baseline is stable.

## Markers

Semantic markers:

- `unit`
- `contract`
- `integration`
- `scenario`
- `calibration`
- `soak`
- `publication`

Markers describe cost/responsibility, not development history.

## CI tiers

### Fast

Required on every pull request.

Includes:

- unit;
- contract;
- publication;
- fast integration tests.

### Full

Required before merging engine behavior changes.

Includes the complete public suite, including scenarios, deterministic calibration, and long-horizon soaks.

### Main / release matrix

In addition to Full:

- packaging/self-check;
- deterministic environment matrix;
- export/publication guards;
- coverage report.

## Determinism matrix

Selected semantic fingerprints must remain identical under at least:

- distinct `PYTHONHASHSEED` values;
- `TZ=UTC`;
- `TZ=Asia/Tokyo`.

Explicit timestamp semantics must not depend on the runner's local timezone or unordered Python container iteration.

## Coverage

Coverage is a diagnostic and a ratchet, not the definition of correctness.

For the first public baseline:

1. record statement and branch coverage;
2. preserve that baseline;
3. require explicit review for regressions;
4. give integrity-critical modules direct branch-level review.

A global 100% target is not a substitute for invariant and scenario tests.

## Quality bar

A public test is clean only when:

- the path/name describes behavior;
- one subsystem clearly owns it;
- it contains no private application knowledge;
- it imports support code, not another test module;
- it does not depend on private Git history;
- it is deterministic;
- it requires no live network;
- failure output is actionable;
- fixture setup is proportional to the contract;
- it does not assert implementation trivia unless that representation is explicitly part of the contract.

## Migration rule

No historical private test is deleted merely because it is inconvenient.

Every source test file must receive an explicit disposition:

- migrate;
- merge into owning test;
- replace with stronger semantic contract;
- consolidate into shared soak;
- keep private as application/process guard;
- remove only after proving it asserted no surviving public-engine contract.

The historical suite remains available until migration equivalence is documented and the public Full suite is green.
