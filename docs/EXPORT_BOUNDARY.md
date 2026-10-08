# Export Boundary

This document defines the publication boundary for the initial Snowfall Life Engine OSS extraction.

**Status:** the initial extraction is complete and published as `v0.1.0`. This document remains normative for the public/private publication boundary. The broader product scope freeze is defined separately in [V1_BOUNDARY.md](V1_BOUNDARY.md).

The first engine-source export was performed only after the originating private application's final pre-split validation and human review were complete.

## Public repository owns

The public repository may contain:

- reusable deterministic Life Engine source;
- versioned Life Engine schemas;
- synthetic fixtures;
- generic runtime/persistence tests;
- local-only Git transaction tests;
- generic CLI code;
- public documentation;
- public CI;
- generic production-runtime primitives whose tests do not require private application data.

## Private application owns

The public repository must not contain:

- character canon, visual identity, or body/wardrobe references;
- private home/environment documents, geometry, or image assets;
- private runtime character projections;
- private social graphs or personal entity data;
- private runtime Life history/state;
- approved private production bootstrap/policy/authority values;
- production credentials, tokens, or secrets;
- application-specific image generation/social-media behavior;
- private production workflow wiring.

## Initial mechanical export candidates

After the pre-split gate completes, the first extraction is expected to start from:

- `engine/life/**`
- `schemas/life/**`
- `tests/life/**` after explicit review
- `tests/fixtures/life/**`
- `scripts/run_checks.py`
- `scripts/run_fast_checks.py`

The extraction will preserve `engine.life` imports initially. Package/import renaming is intentionally deferred.

## Files requiring generalization or exclusion

The private source currently contains some tests that bind generic engine code to application-specific workflow or production artifacts.

Those tests must not be copied mechanically. They must either be generalized to synthetic/public fixtures or excluded from the initial export.

Known categories include:

- tests that statically read private production workflow YAML;
- tests that bind to an application-specific production SpatialContext artifact;
- tests that assert private repository path allowlists;
- tests whose only purpose is private CI/process governance.

Generic engine modules used by those tests may still be public when their contracts are application-agnostic.

## Clean-history rule

The public repository starts from a clean public history.

Do not publish the private application's Git history, deleted files, issue history, branches, or prior repository objects.

The first engine commit must contain only explicitly reviewed public files.

## Semantic-preserving extraction

The initial split is an extraction, not a behavior redesign.

During the first public code transfer:

- do not tune Behavior Policy;
- do not weaken deterministic equivalence;
- do not change append-only history semantics;
- do not rename the package/import tree;
- do not silently alter persistent schemas;
- do not replace real engine paths with simplified test-only behavior.

Any required semantic change must be handled separately after the extracted baseline is proven equivalent.

## Verification before first engine code push

The export process must verify:

1. the exported path manifest;
2. the exclusion manifest;
3. no forbidden private paths are present;
4. no secrets/credentials are present;
5. all runtime imports close inside the public repository;
6. all schema references resolve;
7. tests use synthetic/public fixtures only;
8. Fast regression passes;
9. Full regression passes;
10. long restart/equivalence tests pass;
11. no core test requires a live GitHub network call;
12. source provenance is recorded without exposing private Git history.

## Reconnection rule

The public baseline is now authoritative. The private Snowfall application should consume a reviewed public engine identity (commit/tag/version) and keep application-specific data/integration tests private.

The public engine must not need access to the private application repository to run or test itself.
