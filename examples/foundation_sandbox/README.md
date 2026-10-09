# Synthetic Foundation Sandbox Consumer (pre-v1)

This example is **entirely synthetic**. It demonstrates a downstream caller using the `snowfall-life` CLI installed from the experimental package in Draft PR #8. **It does not activate a production character, use any private Snowfall input, or exercise the full provider-backed RuntimeBundle.**

From a checkout of the pre-v1 packaging prototype:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/python examples/foundation_sandbox/run.py
```

The sample uses its own `policy.json` (TEST_FIXTURE only), creates an isolated temporary workspace, initializes a synthetic checkpoint, advances five minutes with explicit target time, verifies the history and checkpoint schema, calculates a persisted-object hash, and proves that advancing to the same target again is a read-only `NOOP`. The temporary workspace is removed when the process exits.

**Expected output:** one JSON object with `status: "PASS"`, `scope: "foundation-sandbox-only"`, `repeat_advance: "NOOP"`, a 64-character checkpoint digest, and the unchanged stored `engine_version: "0.1.0-foundation"`. It needs no network calls after installation and makes no claims about v1 public API stability.

The package CI runs this script using a clean wheel install and a clean sdist install outside the repository, comparing the results across both packaging forms. A **separate** complete provider-backed RuntimeBundle consumer example remains a pre-v1 acceptance gate.
