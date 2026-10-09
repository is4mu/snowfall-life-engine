# P1 current-format golden and migration preflight evidence

Status: experimental review evidence for Issue #9. No stable API, approved v1
reader/writer promise, registered migration or persisted-version change.
This work is stacked on the integration of Draft PRs #7/#8/#10/#11 in #12.

## Frozen evidence

`tests/fixtures/golden/persistence-v0.1.0/` contains synthetic serialized
representations of all 16 inventoried families. `artifacts.json` pins each
schema and normalized canonical digest; `expected.json` pins every file's raw
SHA-256, semantic-tree hash, ActualEvent history, capture/event/action IDs,
operator chain, two-step correction lineage and effective overlay hash.
Tests read the checked-in expectations; they never regenerate them.

The fixture has one closed March timeline day and Camera Roll month, one
finalized event/capture, and two linked corrections of the summary. The raw
event and capture remain unchanged; the second correction supersedes the first.
Synthetic operator requests use `fixture:synthetic-review-*`, not production
approval. Last-run metadata is a synthetic current-revision binding sample,
not evidence of a real simulation or a real Git publication.

The representations were initially constructed from the synthetic public
builders, current correction candidate builder and metadata writers at
integration baseline `156e9c68cdaafd3853983ac3ce3fe37da5480b0a`.
All runtime provenance SHAs and review references are synthetic. An intentional
change to these goldens requires a documented serialization/compatibility reason
and review; never regenerate digests merely to make failing tests pass.

## Read-only experimental invocation

From a checkout with Python 3.12 and engine dependencies installed:

```sh
python -m tools.persistence_preflight \
  --root tests/fixtures/golden/persistence-v0.1.0/runtime \
  --references tests/fixtures/golden/persistence-v0.1.0/references.json \
  --target-engine 0.1.0-foundation
```

The tool requires explicit approved identity sets and an explicit target.
It uses existing full snapshot/schema/history/camera/operator/metadata checks,
then validates correction targets and supersession against raw history.
It returns detached digest/link evidence only; it never calls a migration
callback, stages files, writes source, moves refs or commits. It is a checkout
review tool, excluded from the wheel/sdist and canonical Python/CLI facade.
No deprecation window or stable migration facade is declared.

Only **current->current inspection** succeeds. Unsupported source/target engines,
future schema versions across all 16 families, missing/malformed documents,
symlinks, unknown files, broken hash/capture/metadata bindings and changed source
fingerprints fail closed. This tool rejects unknown files anywhere in its root,
which is stricter than the existing runtime reader's legacy-path exception.

The existing OperatorHistory reader still normalizes a missing schema_version
in memory. Removing that field from a previously hash-bound tree changes its
semantic hash; matching-version execution metadata must then reject. The test
records both behaviors and does not silently rewrite or reseal source data.

The caller must hold source data quiescent. Fingerprints detect ordinary changes
between validation and report generation; they do not provide a filesystem lock,
an atomic snapshot or protection against arbitrary concurrent writers.

## Acceptance and limits

- Current reader/writer round-trip keeps all golden file bytes and pinned links.
- Fresh processes inspect original and staged copies identically across three
  hashseeds and UTC/Asia-Tokyo; both trees retain their source bytes.
- Failure injection checks unsupported versions, source changes and integrity
  damage without repairs or source writes.
- The dedicated CI runs all persistence contracts in six environments and Full
  with branch coverage once, including 72h restart equivalence, 28d structural
  and 90d invariant suites. Existing package/provider CI continues independently.

**Remaining approval gates:** decide v0.1.0 readability under v1, per-family
oldest reader/newest writer, exact target and staged migration algorithm,
verified commit/rollback semantics and public migration facade. There is no
supported old->new transition to test yet. Copy replay is inspection evidence,
not proof of a future migration. Schema/engine values and migration registry
remain unchanged; no merge, release, P0 or Character Creator work is included.
