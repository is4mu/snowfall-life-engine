"""Canonical serialization and SHA-256 hashing (no binary floats).

#5 Requirement 4: semantically unordered lists/sets are sorted by a documented
stable key before canonical serialization. Ordered collections keep list order.

Unordered Foundation collections (path key → primary sort key):
- ``conditions`` → ``condition_id``
- ``companions`` / ``participants`` → ``entity_id``
- ``causes`` → ``(cause_type, ref or "")``
- ``rule_ids`` / ``input_state_refs`` → string value

Primary-key ties are broken by the item's full canonical JSON (total order).
Duplicate ``condition_id`` values are rejected by checkpoint invariants.

Ordered (not sorted): timeline ``actual_events``, scheduler ``pending_queue.events``,
effect sequences (``effects``, ``effects_on_end``).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

# Parent dict keys whose list values are semantically unordered.
_UNORDERED_LIST_KEYS = frozenset(
    {
        "conditions",
        "companions",
        "participants",
        "causes",
        "rule_ids",
        "input_state_refs",
        "location_constraints",
    }
)


def _normalize_str(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _canonicalize(obj: Any) -> Any:
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, int) and not isinstance(obj, bool):
        return obj
    if isinstance(obj, float):
        raise TypeError("binary floats are forbidden in canonical Life Engine objects")
    if isinstance(obj, str):
        return _normalize_str(obj)
    if isinstance(obj, dict):
        items = []
        for key, value in obj.items():
            if not isinstance(key, str):
                raise TypeError("object keys must be strings")
            items.append((_normalize_str(key), _canonicalize(value)))
        items.sort(key=lambda kv: kv[0])
        return {k: v for k, v in items}
    if isinstance(obj, (list, tuple)):
        return [_canonicalize(v) for v in obj]
    raise TypeError(f"unsupported type for canonicalization: {type(obj)!r}")


def canonical_json(obj: Any) -> str:
    """UTF-8 JSON string: NFC strings, sorted keys, compact separators, no floats."""
    return json.dumps(
        _canonicalize(obj),
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(obj: Any) -> bytes:
    return canonical_json(obj).encode("utf-8")


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def _unordered_primary_key(parent_key: str, item: Any) -> Any:
    if parent_key in {"rule_ids", "input_state_refs", "location_constraints"}:
        # String arrays: primary key is the string itself (total with tie-break).
        if isinstance(item, str):
            return item
        return (str(type(item)), repr(item))
    if parent_key == "participants" and isinstance(item, str):
        # Commitment participants are unordered string IDs.
        return item
    if not isinstance(item, dict):
        return (str(type(item)), repr(item))
    if parent_key == "conditions":
        return item.get("condition_id") or ""
    if parent_key in {"companions", "participants"}:
        return item.get("entity_id") or ""
    if parent_key == "causes":
        return (item.get("cause_type") or "", item.get("ref") or "")
    return ""


def _unordered_total_key(parent_key: str, item: Any) -> tuple[Any, str]:
    """Total deterministic order: semantic primary key, then full canonical item JSON."""
    if parent_key in {"rule_ids", "input_state_refs", "location_constraints"} and isinstance(
        item, str
    ):
        return (item, item)
    if parent_key == "participants" and isinstance(item, str):
        return (item, item)
    return (_unordered_primary_key(parent_key, item), canonical_json(item))


def sort_unordered_collections(obj: Any, *, parent_key: str | None = None) -> Any:
    """Deep transform: sort unordered lists with total order; preserve ordered lists."""
    if isinstance(obj, dict):
        return {k: sort_unordered_collections(v, parent_key=k) for k, v in obj.items()}
    if isinstance(obj, list):
        items = [sort_unordered_collections(v, parent_key=None) for v in obj]
        if parent_key in _UNORDERED_LIST_KEYS:
            return sorted(items, key=lambda it: _unordered_total_key(parent_key, it))
        return items
    return obj


def normalize_persisted_object(obj: Any) -> Any:
    """Foundation persisted-object normalization before hash/write.

    1. Canonicalize Foundation timestamp fields (UTC ≡ +09:00, reject sub-seconds)
    2. Sort semantically unordered collections with a total deterministic order
    """
    from .timeutil import canonicalize_timestamps

    timed = canonicalize_timestamps(obj)
    return sort_unordered_collections(timed)


def persisted_hash(obj: Any) -> str:
    """Hash after Foundation persisted-object normalization (timestamps + unordered)."""
    return canonical_hash(normalize_persisted_object(obj))
