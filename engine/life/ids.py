"""Stable deterministic IDs (no random UUID / order dependence)."""

from __future__ import annotations

import hashlib
from typing import Iterable

from .canonical import canonical_json
from .errors import ErrorCode, LifeEngineError


def stable_id(namespace: str, *parts: object) -> str:
    """Derive a hex ID from namespace + stable parts."""
    payload = {"namespace": namespace, "parts": list(parts)}
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"{namespace}:{digest[:32]}"


def assert_unique(ids: Iterable[str]) -> None:
    seen: set[str] = set()
    for item in ids:
        if item in seen:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, item)
        seen.add(item)


class IdRegistry:
    """Fail-closed registry: same ID with different semantic payload is rejected."""

    def __init__(self) -> None:
        self._binding: dict[str, str] = {}

    def register(self, event_id: str, semantic_hash: str) -> None:
        prior = self._binding.get(event_id)
        if prior is None:
            self._binding[event_id] = semantic_hash
            return
        if prior != semantic_hash:
            raise LifeEngineError(
                ErrorCode.DUPLICATE_ID,
                f"id collision with different semantics: {event_id}",
            )

    def known(self, event_id: str) -> bool:
        return event_id in self._binding
