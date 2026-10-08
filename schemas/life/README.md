# Life Engine Schemas

This directory contains the versioned JSON Schema contracts used by Snowfall Life Engine.

## Local schema identity

All public schema documents use the non-routable identity prefix:

`https://snowfall-life-engine.invalid/schemas/life/`

The URI is an identifier only. Validation is fully local and does not require network access.

## Registry

`engine.life.schema.SCHEMA_NAMES` registers every JSON schema in this directory. The public baseline requires registry/file closure: no registered schema may be missing, and no schema may depend on private application files.

## Compatibility

Relative `$ref` links resolve within this directory.

Schema changes are contract changes. Do not regenerate or weaken schemas merely to make tests pass. Persistent representation changes require explicit compatibility review.

## Public boundary

Schemas describe reusable engine data only. Production authority values, character canon, private social graphs, runtime history, and application-specific workflow data are not stored here.
