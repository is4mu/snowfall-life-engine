# P1 W1 actual save and restart evidence

Status: experimental pre-v1 evidence, 2026-10-09. W1 remains the first-writer
candidate; neither a stable writer decision nor a shipped v1 guarantee.
Refs #9, #16; stacked on Draft #17.

`examples/w1_persistence/run.py` invokes the existing correction Git transaction
writer against a disposable local repository made from the immutable synthetic
v0.1.0 archive. It adds a third correction superseding the second, writes a
verified **unreferenced** candidate commit, exports its actual Git blobs to a
separate save directory and reloads it in a fresh interpreter. No source or
production repository/ref is updated. Genesis refs exist only in the owned
synthetic test repository.

This is an actual engine writer, not just a JSON serializer or identity copy.
The prior simulation/provider writer is covered by the installed provider
consumer; this additional probe exercises the history-rich correction path.

The new pinned `w1-write-reload.experimental.json` records the generated
candidate tree/commit, all raw file SHA256 digests, semantic tree hash, effective
correction hash, event/capture/operator identities and state revision. Its
source is the frozen archive; the historic archive is never regenerated.

Contracts verify:

- all 16 families retain explicit schema version 1; persisted epoch stays
  `0.1.0-foundation`, separate from package `0.2.0.dev0`;
- original event head/count/hash, closed timeline, camera shard and prior action
  blobs remain identical; the operator ledger appends one action and the state
  revision advances 2 → 3 exactly once;
- new correction binds to and supersedes the prior action; effective projection
  changes while raw historical facts remain intact;
- saved blobs reload with full schema, history/camera/metadata verification;
  restart reports equal the pinned result;
- candidate has one frozen parent and no containing ref; repeated creation from
  the same base yields the same commit and files;
- source worktree, HEAD, all refs and real index remain unchanged;
- committed future engine/schema versions, broken history/capture bindings and
  invalid supersession reject without altering those inputs;
- fresh processes replay across hashseeds 0/1/8675309 × UTC/Asia-Tokyo;
- wheel and sdist installed outside checkout run the same writer/restart probe
  under isolated Python, compare all 12 outputs and the pinned report.

Run: `python -m pytest -q tests/contracts/persistence/test_w1_write_reload.py`.
Installed probe: `python -I /path/to/examples/w1_persistence/run.py`.

Limit: this uses synthetic saves and a pre-v1 implementation entrypoint. It
proves this specific current-format write/reload operation, not all possible
future W1 semantic changes. Actual approved 1.0 binaries must rerun unchanged
historical and W1 output goldens through a reviewed facade before release.
Future capability fields, migration registry, schemas and engine constants
remain unchanged. No merge/release or first-writer selection is performed.
