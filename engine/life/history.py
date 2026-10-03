"""History hash chain for finalized ActualEvents."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .canonical import canonical_hash
from .errors import ErrorCode, LifeEngineError

EMPTY_HISTORY_HASH = canonical_hash({"genesis": True, "events": []})


def next_history_hash(prev_hash: str, event: Mapping[str, Any]) -> str:
    # ActualEvent-aware normalization (local captures sort); preserves no-capture hashes.
    from .events import normalize_actual_event_for_persistence

    normalized = normalize_actual_event_for_persistence(dict(event))
    return canonical_hash({"prev": prev_hash, "event": normalized})


def fold_history(events: Sequence[Mapping[str, Any]], *, genesis: str = EMPTY_HISTORY_HASH) -> str:
    h = genesis
    for event in events:
        h = next_history_hash(h, event)
    return h


def verify_history(
    events: Sequence[Mapping[str, Any]],
    *,
    head_event_id: str | None,
    history_hash: str,
    event_count: int,
) -> None:
    if len(events) != event_count:
        raise LifeEngineError(
            ErrorCode.HISTORY_HASH_MISMATCH,
            f"event_count {event_count} != ledger length {len(events)}",
        )
    computed = fold_history(events)
    if computed != history_hash:
        raise LifeEngineError(ErrorCode.HISTORY_HASH_MISMATCH, "hash mismatch vs checkpoint")
    if events:
        last_id = events[-1]["event_id"]
        if head_event_id != last_id:
            raise LifeEngineError(
                ErrorCode.HISTORY_HASH_MISMATCH,
                f"head_event_id {head_event_id!r} != {last_id!r}",
            )
    elif head_event_id is not None:
        raise LifeEngineError(ErrorCode.HISTORY_HASH_MISMATCH, "empty ledger but head set")


def collect_ledger_events(days: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Flatten timeline days in date order; within day preserve append order."""
    ordered_days = sorted(days, key=lambda d: d["date"])
    out: list[dict[str, Any]] = []
    for day in ordered_days:
        out.extend(dict(e) for e in day.get("actual_events", []))
    return out
