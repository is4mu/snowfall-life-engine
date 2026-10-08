"""Slice 5E1 — operator history hash chain (separate from ActualEvent history)."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .canonical import canonical_hash, normalize_persisted_object
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance

EMPTY_OPERATOR_HISTORY_HASH = canonical_hash({"genesis": True, "actions": []})

OPERATOR_HISTORY_RELPATH = "state/operator-history.json"


def empty_operator_history() -> dict[str, Any]:
    """Deterministic empty operator-history identity."""
    return {
        "schema_version": 1,
        "head_action_id": None,
        "operator_history_hash": EMPTY_OPERATOR_HISTORY_HASH,
        "action_count": 0,
    }


def normalize_operator_history(raw: Mapping[str, Any]) -> dict[str, Any]:
    reject_binary_floats(raw)
    data = normalize_persisted_object(dict(raw))
    if "schema_version" not in data:
        data["schema_version"] = 1
    validate_instance(data, "operator_history")
    head = data["head_action_id"]
    count = data["action_count"]
    if count == 0:
        if head is not None:
            raise LifeEngineError(
                ErrorCode.HISTORY_HASH_MISMATCH,
                "empty operator history requires head_action_id=null",
            )
        if data["operator_history_hash"] != EMPTY_OPERATOR_HISTORY_HASH:
            raise LifeEngineError(
                ErrorCode.HISTORY_HASH_MISMATCH,
                "empty operator history hash mismatch",
            )
    elif head is None:
        raise LifeEngineError(
            ErrorCode.HISTORY_HASH_MISMATCH,
            "non-empty operator history requires head_action_id",
        )
    return data


def next_operator_history_hash(prev_hash: str, action: Mapping[str, Any]) -> str:
    from .operator_action import normalize_operator_action

    normalized = normalize_operator_action(action)
    return canonical_hash({"prev": prev_hash, "action": normalized})


def fold_operator_history(
    actions: Sequence[Mapping[str, Any]],
    *,
    genesis: str = EMPTY_OPERATOR_HISTORY_HASH,
) -> str:
    h = genesis
    for action in actions:
        h = next_operator_history_hash(h, action)
    return h


def verify_operator_history(
    actions: Sequence[Mapping[str, Any]],
    *,
    head_action_id: str | None,
    operator_history_hash: str,
    action_count: int,
) -> None:
    if len(actions) != action_count:
        raise LifeEngineError(
            ErrorCode.HISTORY_HASH_MISMATCH,
            f"action_count {action_count} != ledger length {len(actions)}",
        )
    computed = fold_operator_history(actions)
    if computed != operator_history_hash:
        raise LifeEngineError(
            ErrorCode.HISTORY_HASH_MISMATCH,
            "operator history hash mismatch vs checkpoint",
        )
    if actions:
        last_id = actions[-1]["action_id"]
        if head_action_id != last_id:
            raise LifeEngineError(
                ErrorCode.HISTORY_HASH_MISMATCH,
                f"head_action_id {head_action_id!r} != {last_id!r}",
            )
    elif head_action_id is not None:
        raise LifeEngineError(
            ErrorCode.HISTORY_HASH_MISMATCH,
            "empty operator ledger but head set",
        )


def advance_operator_history(
    history: Mapping[str, Any],
    action: Mapping[str, Any],
) -> dict[str, Any]:
    """Return the next operator-history identity after appending one action."""
    prior = normalize_operator_history(history)
    from .operator_action import normalize_operator_action

    normalized = normalize_operator_action(action)
    prev_head = prior["head_action_id"]
    if normalized["previous_action_id"] != prev_head:
        raise LifeEngineError(
            ErrorCode.HISTORY_HASH_MISMATCH,
            "action.previous_action_id must equal current operator head",
        )
    new_hash = next_operator_history_hash(prior["operator_history_hash"], normalized)
    return normalize_operator_history(
        {
            "schema_version": 1,
            "head_action_id": normalized["action_id"],
            "operator_history_hash": new_hash,
            "action_count": prior["action_count"] + 1,
        }
    )
