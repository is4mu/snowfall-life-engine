"""Engine / behavior-policy version gate and migration registry."""

from __future__ import annotations

from typing import Any, Callable

from . import ENGINE_VERSION, SUPPORTED_ENGINE_VERSIONS
from .errors import ErrorCode, LifeEngineError

MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]

# Interface/registry present; current migrations are no-ops for supported versions.
_MIGRATIONS: dict[tuple[str, str], MigrationFn] = {}


def register_migration(from_version: str, to_version: str, fn: MigrationFn) -> None:
    _MIGRATIONS[(from_version, to_version)] = fn


def ensure_supported_engine(version: str) -> None:
    if version not in SUPPORTED_ENGINE_VERSIONS:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION, f"engine_version={version}")


def ensure_supported_policy(policy_version: str, *, allowed: set[str] | frozenset[str]) -> None:
    if policy_version not in allowed:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"behavior_policy_version={policy_version}",
        )


def migrate_state(state: dict[str, Any], *, target_engine: str = ENGINE_VERSION) -> dict[str, Any]:
    current = state.get("engine_version")
    if current == target_engine:
        ensure_supported_engine(current)
        return state
    key = (current, target_engine)
    if key not in _MIGRATIONS:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            f"no migration {current} -> {target_engine}",
        )
    return _MIGRATIONS[key](state)
