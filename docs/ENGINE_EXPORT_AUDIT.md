# Engine export audit

Approved private source: `458e05673a8d4a8f3b2346825b2eef8419411d64`

This audit covers all **77** top-level files in `engine/life/` before the first clean public code export.

## Dispositions

- **direct_export — 51 files:** no private-boundary hit in the approved-source content scan; byte-preserving export is allowed once dependencies are present.
- **sanitize_then_export — 19 files:** reusable semantics, but comments, identifiers, legacy private paths, or project-specific constants must be neutralized mechanically.
- **hold_private_or_generalize — 7 files:** production authority / remote publication / credential / fixed Life-ref wiring. Excluded from the initial public baseline unless separately generalized.

## Initial held modules

- `engine/life/runtime_operator_launcher.py`
- `engine/life/runtime_production_authority.py`
- `engine/life/runtime_production_composition.py`
- `engine/life/runtime_production_fact_provider.py`
- `engine/life/runtime_production_fact_sources.py`
- `engine/life/runtime_production_launcher.py`
- `engine/life/runtime_remote_publisher.py`

These modules are withheld because the current implementations combine generic mechanics with Snowfall production authority, remote publication, credential handling, GitHub Actions integration, or a fixed `refs/heads/life` target.

## Sanitation rules

Allowed sanitation is mechanical only: neutralize project-specific comments/docstrings, private legacy-path wording, private author identity, and person-specific guard names. It must not tune behavior, alter policy values, weaken validation, or change deterministic semantics.

## Export policy

1. Public history remains clean; private Git history is never imported.
2. Every exported file is traceable to the approved source SHA.
3. Direct files remain byte-identical.
4. Sanitized files require an explicit transform record.
5. Held production modules remain private until a dedicated genericization change.
6. Public CI, private-name scans, dependency closure, schemas, synthetic fixtures, and deterministic tests must be green before `v0.1.0`.
