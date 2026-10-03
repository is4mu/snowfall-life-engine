"""Life Engine v2 Slice 1 Foundation — public package."""

from __future__ import annotations

ENGINE_VERSION = "0.1.0-foundation"
SUPPORTED_ENGINE_VERSIONS = frozenset({ENGINE_VERSION})
SUPPORTED_SCHEMA_VERSION = 1

from .errors import ErrorCode, LifeEngineError  # noqa: E402
from .canonical import canonical_bytes, canonical_hash, canonical_json  # noqa: E402

__all__ = [
    "ENGINE_VERSION",
    "SUPPORTED_ENGINE_VERSIONS",
    "SUPPORTED_SCHEMA_VERSION",
    "ErrorCode",
    "LifeEngineError",
    "canonical_bytes",
    "canonical_hash",
    "canonical_json",
]
