# Synthetic Provider-Driven RuntimeBundle Consumer (P1 / pre-v1)

This is a **complete, review-only example of the existing full RuntimeBundle candidate path**; it is different from the Foundation sandbox CLI in Draft PR #8. It now exercises the **experimental `snowfall_life.runtime` and `snowfall_life.persistence` imports** for provider execution and persistent candidate building. Synthetic fixture construction still uses internal implementation helpers; these are **not** public facade exports. The canonical aliases themselves remain **pre-v1 review candidates, not stable 1.x APIs**.

Run in the public repository checkout with Python 3.12 after installing `engine/life/requirements.txt`:

```bash
python -m pip install -r engine/life/requirements.txt
python -m examples.provider_runtime.run
```

The script reads only the public synthetic v2 behavior-policy fixture at `tests/fixtures/policy/v2_synthetic_candidate.json` (production-*shaped* schema, **not approved production authority**). It constructs all eight mandatory runtime documents with generic IDs, explicit person/home/wardrobe/consumable reference sets, a sealed SocialResponseState, and one due IDLE wakeup.

The application-owned `SyntheticFactProvider` supplies four kinds of **deterministic call-local data** on request:

- one decision input and inert social facts, yielding a synthetic HOUSEHOLD activity;
- materialization facts bound to the engine-selected candidate ID;
- projected wakeup facts with an explicit horizon;
- an empty capture source-moment sequence.

It then uses `build_runtime_candidate_tree_with_provider_factory` to stage a candidate in a sibling directory (not publish a Git ref), reloads/validates the candidate, asserts a single state revision bump, verifies the **source tree remains byte-identical**, stages a second candidate from the same authoritative baseline, and compares **every resulting file's SHA-256**, semantic-tree hash, and callback sequence.

Expected stdout is a single JSON object containing `status: "PASS"`, `scope: "full-provider-runtime-candidate"`, `result: "CHANGE"`, `state_revision_after: 1`, `baseline_unchanged: true`, `byte_identical_replay: true`, and all four provider callback names.

The example does **not** load private Snowfall character data, approve a production policy, use credentials, execute Git transactions, call LLM/media generation or publish a candidate. Its only write targets are ephemeral synthetic temporary directories. The CI smoke test runs it with different Python hash seeds and system timezones.

## Review gate

Before 1.0, **approve** the exact canonical export whitelist/signatures and errors, validate an **actual 1.0 release-candidate** wheel/sdist against the frozen v0.1.0 and W1 save/restart goldens, and define an `engine.life` deprecation window. The first 1.0 writer format W1 has been owner-selected (Issue #16), but this **pre-v1 preview is not a published stable API**. See [canonical facade preview](../../docs/P1_CANONICAL_RUNTIME_FACADE_PREVIEW.md).
