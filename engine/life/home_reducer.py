"""Slice 3C — pure HomeState / ConsumablesState reducers for strict HOME_TRANSITION.

Applies coarse entity/stock status updates only. Does not wire into Foundation
``apply_effect``, CurrentState, clock/queue, runtime persistence, or ActualEvent
finalization. No exact quantities, brands/products, geometry, or wardrobe/laundry
item lifecycle.

Layering:
- JSON Schema (``effect.schema.json``) is the **structural** layer (required keys,
  types, ``additionalProperties: false``, closed ``transition_kind``).
- ``normalize_home_transition_effect`` is the **semantic** layer: canonicalize
  ``transition_at`` to Asia/Tokyo and reject bad/naive/fractional timestamps.
- ``reduce_home_transition`` / ``reduce_consumables_transition`` consume that
  already-canonicalized payload and apply domain transition rules.
"""

from __future__ import annotations

from copy import deepcopy
from typing import AbstractSet, Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339
from .world_state import (
    compute_domain_state_hash,
    validate_consumables_state,
    validate_home_state,
)

_HOME_KIND = "HOME_ENTITY_STATUS_SET"
_CONSUMABLE_KIND = "CONSUMABLE_STATUS_SET"
_HOME_STATUSES = frozenset({"UNKNOWN", "NORMAL", "NEEDS_ATTENTION"})
_CONSUMABLE_STATUSES = frozenset({"UNKNOWN", "AVAILABLE", "LOW", "OUT"})


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def normalize_home_transition_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic normalize for strict HOME_TRANSITION; does not mutate caller input.

    Structural validation via JSON Schema, then:
    - canonicalize ``transition_at`` to Asia/Tokyo ``+09:00`` second precision
    - reject bad / naive / fractional timestamps (via WorldState-local time rules)

    Returns a new envelope whose ``payload.transition_at`` is already canonical.
    """
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    reject_binary_floats(data, path="$.effect")
    validate_instance(data, "effect")
    if data.get("effect_type") != "HOME_TRANSITION":
        _fail(f"expected HOME_TRANSITION, got {data.get('effect_type')!r}")
    raw_payload = data.get("payload")
    if not isinstance(raw_payload, Mapping):
        _fail("HOME_TRANSITION payload must be an object")

    kind = raw_payload.get("transition_kind")
    if kind not in (_HOME_KIND, _CONSUMABLE_KIND):
        _fail(f"unknown transition_kind: {kind!r}")

    transition_at = canonicalize_timestamp(
        raw_payload["transition_at"], field="transition_at"
    )

    if kind == _HOME_KIND:
        entity_id = raw_payload.get("entity_id")
        if not isinstance(entity_id, str) or not entity_id:
            _fail("entity_id: expected non-empty string")
        status = raw_payload.get("status")
        if status not in _HOME_STATUSES:
            _fail(f"home status invalid: {status!r}")
        data["payload"] = {
            "transition_kind": _HOME_KIND,
            "transition_at": transition_at,
            "entity_id": entity_id,
            "status": status,
        }
    else:
        consumable_id = raw_payload.get("consumable_id")
        if not isinstance(consumable_id, str) or not consumable_id:
            _fail("consumable_id: expected non-empty string")
        status = raw_payload.get("status")
        if status not in _CONSUMABLE_STATUSES:
            _fail(f"consumable status invalid: {status!r}")
        data["payload"] = {
            "transition_kind": _CONSUMABLE_KIND,
            "transition_at": transition_at,
            "consumable_id": consumable_id,
            "status": status,
        }
    return data


def reduce_home_transition(
    home_state: Mapping[str, Any],
    effect: Mapping[str, Any],
    *,
    source_event_id: str,
    known_entity_ids: AbstractSet[str],
) -> dict[str, Any]:
    """Pure HomeState transition for one HOME_ENTITY_STATUS_SET effect.

    Inputs are not mutated. No file/network I/O, wall clock, or PRNG.
    """
    if not isinstance(source_event_id, str) or not source_event_id:
        _fail("source_event_id: expected non-empty string")

    known = frozenset(known_entity_ids)
    prior = validate_home_state(home_state, known_entity_ids=known)
    typed = normalize_home_transition_effect(effect)
    payload = typed["payload"]

    if payload["transition_kind"] != _HOME_KIND:
        _fail(
            f"reduce_home_transition requires {_HOME_KIND}, "
            f"got {payload['transition_kind']!r}"
        )

    entity_id = payload["entity_id"]
    status = payload["status"]
    transition_at = payload["transition_at"]

    if entity_id not in known:
        _fail(f"home unknown entity_id: {entity_id}")

    prior_as_of = prior["as_of"]
    if parse_rfc3339(transition_at, field="transition_at") < parse_rfc3339(
        prior_as_of, field="as_of"
    ):
        _fail(
            f"HOME_TRANSITION transition_at before state.as_of "
            f"(transition_at={transition_at}, as_of={prior_as_of})"
        )

    entities: list[dict[str, Any]] = deepcopy(prior["entities"])
    match_idx = next(
        (i for i, row in enumerate(entities) if row["entity_id"] == entity_id),
        None,
    )

    if match_idx is None:
        entities.append({"entity_id": entity_id, "status": status})
    else:
        entities[match_idx]["status"] = status

    out: dict[str, Any] = {
        "schema_version": prior["schema_version"],
        "domain": prior["domain"],
        "character_id": prior["character_id"],
        "as_of": transition_at,
        "provenance": {
            "origin": "ACTUAL_EVENT",
            "source_event_id": source_event_id,
        },
        "entities": entities,
        "revision": "",
        "state_hash": "0" * 64,
    }
    digest = compute_domain_state_hash(out)
    out["state_hash"] = digest
    out["revision"] = digest
    return validate_home_state(out, known_entity_ids=known)


def reduce_consumables_transition(
    consumables_state: Mapping[str, Any],
    effect: Mapping[str, Any],
    *,
    source_event_id: str,
    approved_consumable_ids: AbstractSet[str],
) -> dict[str, Any]:
    """Pure ConsumablesState transition for one CONSUMABLE_STATUS_SET effect.

    Inputs are not mutated. No quantity arithmetic or status progression inference.
    """
    if not isinstance(source_event_id, str) or not source_event_id:
        _fail("source_event_id: expected non-empty string")

    approved = frozenset(approved_consumable_ids)
    prior = validate_consumables_state(
        consumables_state, approved_consumable_ids=approved
    )
    typed = normalize_home_transition_effect(effect)
    payload = typed["payload"]

    if payload["transition_kind"] != _CONSUMABLE_KIND:
        _fail(
            f"reduce_consumables_transition requires {_CONSUMABLE_KIND}, "
            f"got {payload['transition_kind']!r}"
        )

    consumable_id = payload["consumable_id"]
    status = payload["status"]
    transition_at = payload["transition_at"]

    if consumable_id not in approved:
        _fail(f"consumables unknown consumable_id: {consumable_id}")

    prior_as_of = prior["as_of"]
    if parse_rfc3339(transition_at, field="transition_at") < parse_rfc3339(
        prior_as_of, field="as_of"
    ):
        _fail(
            f"HOME_TRANSITION transition_at before state.as_of "
            f"(transition_at={transition_at}, as_of={prior_as_of})"
        )

    stocks: list[dict[str, Any]] = deepcopy(prior["stocks"])
    match_idx = next(
        (i for i, row in enumerate(stocks) if row["consumable_id"] == consumable_id),
        None,
    )

    if match_idx is None:
        stocks.append({"consumable_id": consumable_id, "status": status})
    else:
        stocks[match_idx]["status"] = status

    out: dict[str, Any] = {
        "schema_version": prior["schema_version"],
        "domain": prior["domain"],
        "character_id": prior["character_id"],
        "as_of": transition_at,
        "provenance": {
            "origin": "ACTUAL_EVENT",
            "source_event_id": source_event_id,
        },
        "stocks": stocks,
        "revision": "",
        "state_hash": "0" * 64,
    }
    digest = compute_domain_state_hash(out)
    out["state_hash"] = digest
    out["revision"] = digest
    return validate_consumables_state(out, approved_consumable_ids=approved)
