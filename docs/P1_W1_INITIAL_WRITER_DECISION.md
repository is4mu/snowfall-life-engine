# Initial v1 writer decision: W1

**Owner decision recorded 2026-10-09: W1 is selected for the first stable 1.0 writer format.** This is no longer a proposal requiring the owner to choose W1 or W2. It is **not** a declaration that 1.0 has been built, tested, merged or released.

## Concrete format of the first 1.0 writer

- Persisted checkpoint `engine_version`: **`0.1.0-foundation`** (separate from the distribution's eventual `1.0.0` label).
- Existing sixteen stored document families: **`schema_version: 1`**, including nested ActualEvent and Camera Roll records and all OperatorAction variants.
- Existing strict runtime file paths, canonical serialization/hashing, append-only ActualEvents, correction supersession, capture links, metadata bindings and state-revision semantics are preserved. No new runtime envelope/manifest file.
- A valid historical `v0.1.0` Life save must be readable by the 1.0 reader **without changing source bytes**; current narrow OperatorHistory missing-version behavior remains as a reader-only legacy exception subject to integrity checks.
- No implicit load-time migration, no in-place rewriting of old saved data and no silent acceptance of future/unknown formats.

The machine-readable [owner decision](../oss/p1-w1-initial-writer-decision.json) pins these choices for all sixteen families. Its `status` is a **selected design**. The experimental [capability inventory](../oss/persistence-capabilities.experimental.json) and [baseline inventory](../oss/persistence-contract-inventory.json) may still have `null` **shipped-stability** fields until the actual 1.0 package and public facade are implemented and validated; these nulls do not undo the owner's W1 selection.

## Evidence supporting the choice

- [Draft #13](https://github.com/is4mu/snowfall-life-engine/pull/13): unchanged synthetic v0.1.0 family/archive goldens, raw/semantic hashes, capture links, two-step operator correction history.
- [Draft #15](https://github.com/is4mu/snowfall-life-engine/pull/15): installed pre-v1 wheel and sdist each read the frozen legacy data across three seeds and two timezones.
- [Draft #17](https://github.com/is4mu/snowfall-life-engine/pull/17): explicit sixteen-family current-format decoder/writer capability table and fail-closed planning.
- [Draft #18](https://github.com/is4mu/snowfall-life-engine/pull/18): actual W1 correction writer appends a third action to a synthetic save; exact candidate Git blobs reload after restart with unchanged past facts.
- [Draft #19](https://github.com/is4mu/snowfall-life-engine/pull/19): W2 synthetic container and interrupted transaction recovery laboratory shows feasibility but not a real target schema/reader. The initial 1.0 writer **does not** use this W2 lab format.

W1 is selected on this evidence without gratuitous renumbering. Historical goldens must not be regenerated to make failing tests pass.

## Conditions for reconsidering W2 later

**Do not schedule a W2 migration merely because the package version changes.** Reopen format design when a concrete incompatibility, persisted semantic requirement, mixed-version constraint, performance/storage limit or maintenance/security issue **cannot be safely handled within W1**. Document the problem, compare less risky alternatives and obtain separate approval. Any switch must implement a new versioned schema/epoch, retained old reader, explicit deterministic staged migration, preservation/rekey rules with goldens, fail-closed errors, no-erase source policy and independently approved host CAS publication/recovery. No production Life-ref updates or forced history rewrites.

## Still required before v1 can ship

1. Review the minimal canonical `snowfall_life` Python/CLI facade, error/exit contracts and Python support window. This W1 format selection does **not** select those APIs.
2. Verify an **actual 1.0 release-candidate wheel and sdist**, not just experimental `0.2.0.dev0`, reads the unchanged v0.1.0 goldens and writes/reloads exact W1 structure using the reviewed facade.
3. Rerun Fast, Full, 72h/28d/90d invariants, provider simulation, 37 schema imports and six deterministic environments against the same RC commit; review coverage and private-boundary exclusions.
4. Obtain separate human approval **before any main merge, stable API declaration, tag, release or production publication**.

Main stays unchanged, all source PRs stay Draft, no real save or schema is rewritten, and P0/Character Creator remain out of scope.
