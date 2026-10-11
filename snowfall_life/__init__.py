"""Experimental canonical pre-v1 facade for Snowfall Life Engine.

Only listed symbols are intentionally re-exported. This 0.2.0.dev0 preview
does not declare a stable v1 API or alter persisted engine_version.
"""

from __future__ import annotations

from engine.life import (
    ErrorCode,
    LifeEngineError,
    canonical_bytes,
    canonical_hash,
    canonical_json,
)

__all__ = [
    "ErrorCode",
    "LifeEngineError",
    "canonical_bytes",
    "canonical_hash",
    "canonical_json",
]
