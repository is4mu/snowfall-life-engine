# Schema Export Audit

> [!NOTE]
> **Historical pre-release design/audit record.** The v0.1.0 public baseline was released on 2026-10-08 at `a274470dcc1b6a2f22c1ae74df19d94d66a21f34`. Future-tense export steps below describe the completed extraction process, not the current project phase. See [README](../README.md) and [V1_BOUNDARY.md](V1_BOUNDARY.md) for current authority.

Approved source: `is4mu/project-snowfall@458e05673a8d4a8f3b2346825b2eef8419411d64`

## Result

- files under `schemas/life/`: **39**
- source JSON Schema documents: **38**
- public JSON Schema documents: **37**
- public registry target: **37** schemas
- missing registry files: **0**
- extra registry entries: **0**
- held private/application schema: **1** (`runtime_production_authority.schema.json`)

## Required public transform

All 38 historical schema documents use the old application identity:

`https://my-insta-girl.local/schemas/life/`

The public baseline rewrites only the `$id` namespace to:

`https://snowfall-life-engine.invalid/schemas/life/`

The `.invalid` top-level domain is deliberately non-routable. Schema identity does not require network access.

Relative `$ref` values and all validation semantics remain unchanged.

## Documentation

`schemas/life/README.md` is rewritten to remove:

- originating private character/activation wording;
- the old schema namespace explanation;
- private repository links.

## Closure

Every relative schema reference in the exported set resolves within the 37-document public schema set.

The schema registry remains local and deterministic; no schema download is required.
