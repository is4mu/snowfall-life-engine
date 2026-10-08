"""Typed effect application (pure; no NL summary parsing)."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .fixed_point import apply_delta
from .schema import validate_instance

SUPPORTED_EFFECTS = frozenset({"HUMAN_STATE_DELTA", "LOCATION_SET"})

# Closed #4 set. Reserved types pass schema but are not applied in Foundation.
# TASK_PROGRESS became strict in Slice 5B1; COMMITMENT_COMPLETE in Slice 5B2C2B.
# Foundation apply_effect still rejects both (and other reserved types).
RESERVED_UNIMPLEMENTED_EFFECTS = frozenset(
    {
        "TASK_PROGRESS",
        "COMMITMENT_COMPLETE",
        "RELATION_TOUCH",
        "HOME_TRANSITION",
        "WARDROBE_TRANSITION",
        "FINANCE_TRANSACTION",
    }
)
ALL_EFFECT_TYPES = SUPPORTED_EFFECTS | RESERVED_UNIMPLEMENTED_EFFECTS


def normalize_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    validate_instance(data, "effect")
    return data


def apply_effect(state: dict[str, Any], effect: Mapping[str, Any]) -> dict[str, Any]:
    data = normalize_effect(effect)
    effect_type = data["effect_type"]
    if effect_type not in SUPPORTED_EFFECTS:
        raise LifeEngineError(ErrorCode.UNSUPPORTED_EFFECT, str(effect_type))

    out = deepcopy(state)
    payload = data["payload"]

    if effect_type == "HUMAN_STATE_DELTA":
        out["human_state"] = apply_delta(out["human_state"], payload)
        return out

    if effect_type == "LOCATION_SET":
        location_id = payload.get("location_id")
        if not isinstance(location_id, str) or not location_id:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "LOCATION_SET requires location_id")
        out["context"]["location_id"] = location_id
        return out

    raise LifeEngineError(ErrorCode.UNSUPPORTED_EFFECT, str(effect_type))


def apply_effects(state: dict[str, Any], effects: list[Mapping[str, Any]]) -> dict[str, Any]:
    current = state
    for effect in effects:
        current = apply_effect(current, effect)
    return current
