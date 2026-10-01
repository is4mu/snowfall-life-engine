# Schema Export Audit

Approved source: `is4mu/project-snowfall@458e05673a8d4a8f3b2346825b2eef8419411d64`

## Result

- files under `schemas/life/`: **39**
- JSON Schema documents: **38**
- schemas registered by `engine.life.schema.SCHEMA_NAMES`: **38**
- missing registry files: **0**
- extra registry entries: **0**
- private-only schemas: **0**

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

Every relative schema reference resolves within the 38-document public schema set.

The schema registry remains local and deterministic; no schema download is required.
