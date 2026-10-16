# P1 private 1.0.0rc1 build and compatibility rehearsal

**Unreleased RC evidence only, not a stable API, merged integration commit,
tagged release or production activation.** The owner approved *essential
functions first*, and chose the W1 persisted writer previously. Both
decisions are recorded independently of final ABI/release approval.

CI at `.github/workflows/p1-private-rc.yml` runs **only on a deliberate
`p1/v1-rc-` Draft head**. The job fetches the **exact PR-head SHA** with
read-only checkout permissions and disabled persisted checkout credentials.
It copies that source using `git archive HEAD` into temporary runner space
and changes exactly **one scratch-file line**:
`version = "0.2.0.dev0"` to `version = "1.0.0rc1"`.

The checkout package metadata stays at `0.2.0.dev0`. No source code,
W1 `engine_version="0.1.0-foundation"`, existing schema-v1 files, old
goldens or Git refs are changed. The output wheel and sdist have **actual
RC package version metadata**, are built and installed in new Python 3.12
venvs **outside the repository**, and are **not uploaded/published**.
Only wheel/sdist SHA-256 values are printed in the workflow job summary.

## Exact runtime/compatibility checks

- Isolated wheel and sdist both report version `1.0.0rc1` (not merely
  relabelled `0.2.0.dev0` files) and contain all **37** schema resources.
- Import the five candidate **core** namespaces by the exact proposed
  symbol whitelist (root, schema, runtime, persistence, spatial) and
  confirm installed modules originate from the venv's site-packages.
  `snowfall_life.upgrade` stays **importable but excluded from initial
  stable scope**. No private production launcher/authority enters archives.
- Each RC distribution runs the unchanged v0.1.0 legacy-reader synthetic
  goldens, all **16 persisted families**, canonical/semantic hash, events,
  capture and operator chains, with no automatic rewriting or acceptance
  of unsupported future engine epochs.
- The unchanged legacy probe **also executes actual W1 correction write,
  candidate Git objects and restart replay**, comparing the pinned W1
  synthetic result. This is not a host ref publish.
- Repeat the RC read+W1 golden proof under **3 hash seeds × 2 timezones**
  for each archive (**12 executions**), comparing entire canonical report
  across all runs and both archive forms.
- Run a four-callback full `RuntimeFactProvider` synthetic consumer for
  each installed archive: two equal candidate trees, verified reload,
  byte-identical source and no application private inputs or Git ref writes.
- Run installed canonical CLI `self-check`. Separately, the source-head
  P1 CI supplies Fast/Full/72h/28d/90d, six persistence environments,
  private-export tests, Provider replay and coverage on the same PR head.

**API follow-up:** the [ABI candidate review](P1_V1_PUBLIC_ABI_FINAL_REVIEW.md) compares the function/Provider/data-shape signatures across installed wheel and sdist. The [CLI error/legacy review](P1_CLI_INPUT_ERROR_COMPATIBILITY.md) additionally checks the canonical basic JSON input error envelope and legacy eight root exports on **both** installed RC archives. Matching reports are evidence only, not approval.

## What passing this private RC does *not* authorize

This is a **provisional package rehearsal from an unmerged Draft stack**,
not the final reviewed/public `1.0.0rc1` release candidate. The exact
stable API/ABI names and error behavior, Python support window, legacy
`engine.life` deprecation and Foundation CLI contract still require
approval. An actual final reviewed integration candidate must be retested.

No release object, tag, `main` merge, production Life-ref CAS publication,
host upgrade, W2 migration, saved-data rewrite, Character Creator or P2 work
is created here. Scratch artifacts disappear with the CI runner. The
user must explicitly approve each later stabilization/merge/release action.
