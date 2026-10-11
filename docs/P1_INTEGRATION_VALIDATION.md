# P1 Integration Validation — Combined Draft (not a public 1.0 release)

Status: **temporary integration-only test candidate**, 2026-10-09. It combines the independently reviewed source commits listed below into one **separate, unmerged branch**. The four source Draft PRs remain unchanged and require individual approval and merge review.

| Source | Unmerged exact revision |
| --- | --- |
| Contracts and release-status alignment: #7 | `706184134babae5f9c451eb95cf64de205c4eabd` |
| Canonical package and Foundation consumer: #8 | `e370f5314e4b1a50caf499e6142d9c34adfe45ab` |
| Persisted-schema baseline inventory: #10 | `e5b3835ce839758917553e72ab7893a6666b01ba` |
| Full provider-backed synthetic consumer: #11 | `76fd63699f832c599117f764a9ffba09d7382433` |

## Integration risks specifically tested

1. **Document/manifest coherence:** status `baseline-released`, stable v1 responsibility boundary, unapproved API/migration decisions and current release metadata do not contradict publication CI.
2. **Package composition:** a clean wheel/sdist includes the canonical facade, unchanged old `engine.life` imports, exactly 37 schema resources and no private application/production launchers.
3. **Consumer composition:** both the Foundation CLI consumer and full provider-backed RuntimeBundle synthetic candidate operate against **installed artifacts outside the checkout**. No Character Creator or private production state is imported.
4. **Persistence compatibility:** 16 stored-document families and current version-fail-closed behavior remain anchored to real schema/path readers; all proposed 1.x read/write/migration fields remain **unset**, not silently implemented.
5. **Reproducibility:** two provider candidate replays are byte-identical, and Python hashseed + timezone matrix does not change semantic results.
6. **Core regression:** combined Publication, Core import/schema closure, Fast, Full+coverage and determinism matrix should be green on this exact integration head before considering approved merges.

## Explicitly not done

No `main` merge, package publish, release tag, schema format migration, stable 1.0 facade acceptance, production authority, private data, social-media output or UI. CI success on a staging branch is **evidence only** and does not authorize those decisions.

## Review checkpoint

The source PRs #7/#8/#10/#11 are independent; review their net effects and resolve overlapping README edits when deliberately integrating to main. After any approved merges, regenerate this validation evidence on the resulting main/release-candidate SHA. Do **not** use a green temporary integration branch as a shortcut to an unreviewed 1.0 tag.

## Complete-matrix validation trigger

This temporary PR's normal Draft CI checks only Fast and publication/import integrity. To validate the combined tree with **Full plus the six hashseed/timezone cells**, briefly mark this integration PR ready, synchronize this review-only documentation, then immediately restore Draft. This is a CI trigger operation only, **not** consent to merge or release. Compare the run's exact head commit SHA to the documented integration head before interpreting results.
