# Installed candidate reader of a frozen v0.1.0 Life save

The user has selected a **future v1 goal**: continue a valid historical
v0.1.0 saved life. This example exercises that goal on the **current pre-v1
candidate package**, not on a released 1.0 binary. The 16 synthetic
historical file families are stored in
`tests/fixtures/golden/persistence-v0.1.0/` and pinned by SHA and
canonical/semantic hashes.

From the repository checkout (Python 3.12):

```sh
python -m pip install -r engine/life/requirements.txt
python -m examples.legacy_v010_reader.run
```

The isolated package CI additionally builds wheel and sdist, installs each
**outside the checkout** and executes `run.py` under `python -I` for all
six hashseed/timezone combinations. It requires byte-identical output across
those environments and archive forms.

The script copies the frozen runtime into a **temporary scratch directory**,
loads it through the installed current RuntimeBundle reader, validates the
entire 16-family schema/canonical-hash inventory, checks timeline, camera
records, operator corrections and execution metadata bindings, and compares
the raw file SHA set and semantic digest with immutable expectations.
It ensures neither the copy nor the source changes during read. It also
tests that an unknown future engine version **fails closed** without
rewriting even the invalid disposable copy.

The script deliberately invokes `engine.life` implementation imports while
the explicit stable `snowfall_life` runtime facade remains under review.
A future v1 reader should pass this exact frozen input **through its reviewed
public facade**. If the stored `engine_version` changes, a separate legacy
decoder should be added; **do not change expected goldens** simply to make
a new build green.

This is backward-read evidence, **not** a new writer version,
migration/commit/rollback facility, source-data conversion, stable public
API promise or permission to release. See
[Persistence Evolution Design](../../docs/P1_PERSISTENCE_EVOLUTION_DESIGN.md)
and [Issue #9](https://github.com/is4mu/snowfall-life-engine/issues/9).
