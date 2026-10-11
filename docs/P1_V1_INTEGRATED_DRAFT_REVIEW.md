# P1 — integrated **Draft-only** 1.0 candidate review

**NOT the 1.0 release, NOT a stable API declaration and NOT permission to
merge into main.** This is a single cumulative review view of the stacked
P1 Draft branch series. It exists so reviewers can inspect the complete
integrated diff **against main** and run the *entire* test suite and private
`1.0.0rc1` packaging rehearsal against **one exact cumulative commit**.

The current main baseline recorded when creating this review is
`025773712c3a3b85652b9eea5434a5c3ddef3ad1`, and the prior stacked Draft #27 head is
`f34fb7bc13d5d3ad12e93b2628328e6a782ed0b6`. The machine-readable
[integration gate record](../oss/p1-v1-integration-review.json)
keeps every irreversible approval **false**.

## Already selected by the owner

- **Initial persisted writer W1**: stored
  `engine_version="0.1.0-foundation"` and all 16 schema-v1 stored
  families. Package SemVer remains independent of the saved format.
- Existing valid v0.1.0 saves must be readable without rewriting their
  source bytes. The narrow legacy OperatorHistory missing-version
  *read-only* normalization still requires complete integrity checks.
- **Basic initial 1.0 functional scope**: deterministic Provider-driven
  in-memory RuntimeBundle, verified W1 history loading and **separate**
  W1 candidate staging, sealed spatial validation and approved route
  projection. No advanced upgrade/recovery, new W2 format, migration or
  application-specific credentials/data in the initial stable API.

## Candidate API under review, **not yet owner-approved**

The [API/ABI proposal](P1_V1_PUBLIC_ABI_FINAL_REVIEW.md) contains five
canonical Python modules, 13 main callable parameter shapes, four
keyword-only Provider callbacks and seven principal frozen result
shapes. The canonical CLI [error candidate](P1_CLI_INPUT_ERROR_COMPATIBILITY.md)
narrows malformed JSON/UTF-8 and I/O translation to `validate`
and `canonical-hash`; historic `engine.life.cli` behavior is
unaltered. The new CLI input mapping is a **Draft implementation**, not
a frozen stable wire contract.

**Recommendation awaiting owner sign-off:** initially test/support only
Python 3.12.x; keep the eight published v0.1.0 `engine.life` root
names through 1.x. Do not promise compatibility for the internal
implementation module tree or raw exception `detail` strings.
Document whether Python file/JSON native exceptions propagate as part
of the stable runtime boundary.

The current source `pyproject.toml` remains `0.2.0.dev0`.
The private CI workflow changes **only a temporary archive's version
metadata** to real `1.0.0rc1` to test wheel/sdist in new venvs.
No upload, release, tag, production writer or Git ref update.

## Evidence required against the EXACT integration PR head

1. All public Fast + source/publication checks green.
2. Full suite including 72-hour restart / 28-day structure / 90-day
   invariant tests, required branch coverage and accepted baseline.
3. P1 frozen 16-family v0.1.0 archive, operator/correction/capture
   lineage checks and **all six** independent hashseed/timezone
   persistence jobs green.
4. Installed wheel **and** sdist each validate all 37 bundled schemas,
   private module exclusion, canonical API signature probe, CLI typed
   input-failure probe, and eight legacy root imports.
5. Installed private `1.0.0rc1` release rehearsal runs 12 matrix
   legacy/W1 write-restart replays and full four-callback synthetic
   Provider consumer from clean Python 3.12 environments outside the
   source checkout, with original archives and W1 facts unchanged.
6. Inspect combined cumulative diff for unintended public exports,
   changed stored schema/epoch or private data, and confirm no
   migration registry activation or production ref publication.

## Mandatory separate human decisions and actions

- **API go/no-go:** accept or revise the exact canonical Python symbols,
  signatures, type shapes, error/exit policy, Python version and legacy
  import support window. This cumulative Draft does **not** decide them.
- **Source go/no-go:** review the full stacked diff/coverage and choose
  the integration head to freeze for an *actual final*, privately tested
  1.0 release candidate. A passing workflow alone does not authorize
  calling every import stable.
- **Merge go/no-go:** separately approve main merge. Opening this Draft
  with `base=main` creates **no merge**; every prior stacked PR remains
  Draft and unmerged unless separately authorized.
- **Release go/no-go:** independently approve final tag/publish, with
  production Life-ref and real saved-data operations out of scope.

The accumulated tests and private build remain useful evidence, but
**P1 is not complete until those approvals and exact-head checks
are finished**. Character Creator/P2 is not started.
