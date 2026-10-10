# P1 pre-v1 canonical CLI + actual 1.0 release-candidate review gates

**Owner scope direction: only `self-check`, `validate`, `canonical-hash` are basic initial-1.0 CLI candidates; remaining Foundation sandbox commands are not stable-provider-runtime APIs.** See [scope](P1_V1_BASIC_API_SCOPE.md). The following is still a behavior inventory and reversible regression tests — not a stable
wire protocol, approved deprecation window, actual 1.0 RC or release.**
Source: existing `engine.life.cli`, delegated by `snowfall_life.cli`
in the unreleased `0.2.0.dev0` preview. P1 parent: Issue #3.

## Current seven commands (Foundation sandbox CLI only)

| Command | Minimum required inputs | Current successful stdout | Current exit |
| --- | --- | --- | --- |
| `self-check` | none | `OK self-check engine=0.1.0-foundation package=engine.life` | 0 |
| `validate` | positional JSON file, `--schema NAME` (one of 37) | `OK schema=NAME` | 0 |
| `canonical-hash` | JSON file; optional `--raw` | single lower-case 64-hex digest + newline; by default uses persisted-object normalization, `--raw` hashes input structure as-is | 0 |
| `init-sandbox` | `--workspace DIR`; other fixture defaults/flags optional | `wrote PATH/current_state.json`; creates sandbox checkpoint + timeline | 0 |
| `verify-workspace` | `--workspace DIR` | `OK workspace history verified` | 0 |
| `bootstrap-propose` | `--output FILE`; other fixture defaults/flags optional | proposal hash digest; writes synthetic proposal to selected file | 0 |
| `advance` | `--workspace DIR --target RFC3339 --policy FILE`; optional `--production` | indented JSON object with target result, e.g. `ADVANCED` then same-target `NOOP` | 0 |

**This is not a full provider-driven runtime CLI.** `advance` uses the
Foundation checkpoint/timeline sandbox; it does *not* invoke the injected
`RuntimeFactProvider` persistent candidate or live application facts.
`--production` forbids test-fixture policies and does not grant production
authority. `init-sandbox` and `bootstrap-propose` are fixture tooling.

## Current failure observations (not yet stable contract)

- Missing subcommand, required flag or unknown option: `argparse` writes
  usage/error to **stderr** and raises `SystemExit(2)`.
- A caught `LifeEngineError` produces stderr
  `ERROR {ErrorCode.value}: {diagnostic detail}` and returns **2**; stdout is
  empty for the exercised schema-error path.
- The CLI does **not** uniformly produce JSON. Do **not** treat free-form
  diagnostic strings as canonical machine-readable output.
- Current handler catches only `LifeEngineError`: file I/O, malformed JSON
  and unexpected Python exceptions can propagate. **This is an identified
  error-contract gap before v1**, not an approved behavior to freeze. Do not
  silently invent an error-code mapping without independent review.
- Optional argument defaults are presently intended for **synthetic
  bootstrap**, not application-owned production data. No live Git ref
  publication, private character sources or remote side effects.

These observations are pinned by
`tests/contracts/runtime/test_canonical_cli_review.py` under Fast/Full.
Isolated wheel/sdist CI already executes `snowfall-life self-check` and
`python -m snowfall_life self-check`, and runs the standalone Foundation
sandbox example plus full provider-backed W1 tests **separately**.

## Legacy import/CLI review (unapproved)

`engine.life` is the published v0.1.0 implementation import and currently
continues to work. Recommend maintaining a **documented 1.x compatibility
alias** for the exact legacy entrypoints needed by existing consumers; the
actual removal version, deprecation warnings, import constraints and public
support matrix must be explicitly approved before shipping 1.0. Merely being
importable from `engine.life` or `snowfall_life` does **not** make a
symbol stable.

## Actual 1.0 release-candidate evidence required

1. Approve the *exact* root/schema/runtime/persistence/spatial/upgrade export
   whitelist, signatures, typed errors and CLI stdout/stderr/exit contract.
   Resolve whether extra operator-local Git candidate APIs are included.
2. Agree on Python interpreter support (only Python **3.12** is proven by
   current CI) and the legacy `engine.life` alias/deprecation window.
3. From the **exact reviewed source SHA**, create an **isolated, non-published
   candidate build** with distribution version `1.0.0rcN`; record built
   wheel/sdist SHA-256 checksums. Keep the **persisted W1 epoch**
   `0.1.0-foundation` and sixteen family schema versions `1`. Do not
   tag/push/release the RC without a separate authorization.
4. Install **both** exact RC archives in clean environments **outside
   checkout**, using only approved canonical public imports/commands.
   Re-run the immutable v0.1.0/16-family read+hash/correction/capture goldens,
   synthetic W1 Git write/restart golden, Provider callbacks and schema
   validation for **all 37** included schemas. No source reserialization.
5. Run publication/private-exclusion checks, Fast, Full/coverage including
   72h restart / 28d structural / 90d invariant suites, and the six-cell
   Python-hashseed/timezone persistence replay **on one exact candidate SHA**.
6. Document any coverage denominator changes and approve the 1.x ratchet.
   Review export/migration/deprecation issues, then request separate human
   authorization for `main` merge, stable contract designation, tag/release
   and any production publication. Each authorization is distinct.

**Today no actual 1.0 RC archive exists in this Draft.** A green
`0.2.0.dev0` pre-v1 wheel/sdist cannot be relabeled as validated 1.0.
No private Snowfall/Character Creator data, live refs, current schemas or
original W1/legacy goldens are modified.
