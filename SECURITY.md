# Security Policy

## Supported versions

Snowfall Life Engine has not published its first engine baseline yet.

Until `v0.1.0` is released, only the current `main` branch is considered for security fixes.

## Reporting a vulnerability

Please do not publish exploit details, credentials, private data, or proof-of-concept material in a public issue.

Preferred reporting path:

1. Use GitHub's private vulnerability reporting / Security Advisory flow if it is available for this repository.
2. If private reporting is not available, open a minimal public issue asking the maintainer for a private contact channel. Do not include exploit details in that issue.

Useful information includes:

- affected commit or version;
- affected component;
- impact;
- minimal reproduction steps;
- whether the issue can expose private data, execute untrusted code, or bypass deterministic/persistence integrity checks.

## Security scope

Security-sensitive areas include:

- parsing and validation of persistent state;
- Git/runtime transaction boundaries;
- remote publication helpers;
- operator/recovery/upgrade authorization contracts;
- unsafe filesystem or subprocess behavior;
- CI workflow permissions;
- secret or private-data exposure;
- deterministic-state corruption that can bypass integrity checks.

## Public repository boundary

This repository must remain free of private Snowfall application data, production credentials, production runtime history, and private character/environment assets.

A publication-boundary CI check exists specifically to catch accidental path-level leaks.
