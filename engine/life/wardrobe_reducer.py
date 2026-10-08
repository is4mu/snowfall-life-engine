"""Slice 3D — pure WardrobeState reducer for strict WARDROBE_TRANSITION.

Applies explicit item-state SET only (WARDROBE_ITEM_STATE_SET). Does not wire
into Foundation ``apply_effect``, CurrentState, clock/queue, runtime persistence,
or ActualEvent finalization. No outfit selection, brand/color/style, laundry
policy inference, wear-count increment, or Bible I/O.

Layering:
- JSON Schema (``effect.schema.json``) is the **structural** layer (required keys,
  types, ``additionalProperties: false``, closed ``transition_kind`` /
  ``lifecycle_state``).
- ``normalize_wardrobe_transition_effect`` is the **semantic** layer: canonicalize
  ``transition_at`` / non-null ``last_worn_at`` to Asia/Tokyo, reject
  bad/naive/fractional timestamps, and enforce ``last_worn_at <= transition_at``.
- ``reduce_wardrobe_transition`` consumes that already-canonicalized payload and
  applies domain transition rules (approved item, monotonic ``as_of``,
  row replace-or-create).
"""

from __future__ import annotations

from copy import deepcopy
from typing import AbstractSet, Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339
from .world_state import compute_domain_state_hash, validate_wardrobe_state

_KIND = "WARDROBE_ITEM_STATE_SET"
_LIFECYCLE = frozenset(
    {
        "CLEAN_AVAILABLE",
        "DIRTY",
        "WASHING",
        "DRYING",
        "DAMAGED",
        "UNAVAILABLE",
    }
)


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def normalize_wardrobe_transition_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic normalize for strict WARDROBE_TRANSITION; does not mutate caller input.

    Structural validation via JSON Schema, then:
    - canonicalize ``transition_at`` to Asia/Tokyo ``+09:00`` second precision
    - canonicalize non-null ``last_worn_at`` the same way
    - reject bad / naive / fractional timestamps (via WorldState-local time rules)
    - require ``last_worn_at`` is null or ``<= transition_at``
    - preserve exact explicit lifecycle / wear_count / location values

    Returns a new envelope whose ``payload`` timestamps are already canonical.
    """
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    reject_binary_floats(data, path="$.effect")
    validate_instance(data, "effect")
    if data.get("effect_type") != "WARDROBE_TRANSITION":
        _fail(f"expected WARDROBE_TRANSITION, got {data.get('effect_type')!r}")
    raw_payload = data.get("payload")
    if not isinstance(raw_payload, Mapping):
        _fail("WARDROBE_TRANSITION payload must be an object")

    kind = raw_payload.get("transition_kind")
    if kind != _KIND:
        _fail(f"unknown transition_kind: {kind!r}")

    item_id = raw_payload.get("item_id")
    if not isinstance(item_id, str) or not item_id:
        _fail("item_id: expected non-empty string")

    lifecycle = raw_payload.get("lifecycle_state")
    if lifecycle not in _LIFECYCLE:
        _fail(f"lifecycle_state invalid: {lifecycle!r}")

    wear_count = raw_payload.get("wear_count_since_clean")
    if isinstance(wear_count, bool) or not isinstance(wear_count, int):
        _fail("wear_count_since_clean: expected integer")
    if wear_count < 0:
        _fail("wear_count_since_clean: must be >= 0")

    location_ref = raw_payload.get("location_ref")
    if location_ref is not None:
        if not isinstance(location_ref, str) or not location_ref:
            _fail("location_ref: expected null or non-empty string")

    transition_at = canonicalize_timestamp(
        raw_payload["transition_at"], field="transition_at"
    )
    raw_last_worn = raw_payload["last_worn_at"]
    if raw_last_worn is None:
        last_worn_at: str | None = None
    else:
        last_worn_at = canonicalize_timestamp(raw_last_worn, field="last_worn_at")
        if parse_rfc3339(last_worn_at, field="last_worn_at") > parse_rfc3339(
            transition_at, field="transition_at"
        ):
            _fail("last_worn_at must be null or <= transition_at")

    data["payload"] = {
        "transition_kind": _KIND,
        "transition_at": transition_at,
        "item_id": item_id,
        "lifecycle_state": lifecycle,
        "last_worn_at": last_worn_at,
        "wear_count_since_clean": wear_count,
        "location_ref": location_ref,
    }
    return data


def reduce_wardrobe_transition(
    wardrobe_state: Mapping[str, Any],
    effect: Mapping[str, Any],
    *,
    source_event_id: str,
    approved_item_ids: AbstractSet[str],
) -> dict[str, Any]:
    """Pure WardrobeState transition for one WARDROBE_ITEM_STATE_SET effect.

    Inputs are not mutated. No file/network I/O, wall clock, PRNG, or hidden
    wear-count increment / lifecycle graph inference.
    """
    if not isinstance(source_event_id, str) or not source_event_id:
        _fail("source_event_id: expected non-empty string")

    approved = frozenset(approved_item_ids)
    prior = validate_wardrobe_state(wardrobe_state, approved_item_ids=approved)
    typed = normalize_wardrobe_transition_effect(effect)
    payload = typed["payload"]

    if payload["transition_kind"] != _KIND:
        _fail(
            f"reduce_wardrobe_transition requires {_KIND}, "
            f"got {payload['transition_kind']!r}"
        )

    item_id = payload["item_id"]
    transition_at = payload["transition_at"]

    if item_id not in approved:
        _fail(f"wardrobe unknown item_id: {item_id}")

    prior_as_of = prior["as_of"]
    if parse_rfc3339(transition_at, field="transition_at") < parse_rfc3339(
        prior_as_of, field="as_of"
    ):
        _fail(
            f"WARDROBE_TRANSITION transition_at before state.as_of "
            f"(transition_at={transition_at}, as_of={prior_as_of})"
        )

    # Explicit absolute state-set — no increment/decrement or inferred lifecycle.
    new_row = {
        "item_id": item_id,
        "lifecycle_state": payload["lifecycle_state"],
        "last_worn_at": payload["last_worn_at"],
        "wear_count_since_clean": payload["wear_count_since_clean"],
        "location_ref": payload["location_ref"],
    }

    items: list[dict[str, Any]] = deepcopy(prior["items"])
    match_idx = next(
        (i for i, row in enumerate(items) if row["item_id"] == item_id),
        None,
    )
    if match_idx is None:
        items.append(new_row)
    else:
        items[match_idx] = new_row

    out: dict[str, Any] = {
        "schema_version": prior["schema_version"],
        "domain": prior["domain"],
        "character_id": prior["character_id"],
        "as_of": transition_at,
        "provenance": {
            "origin": "ACTUAL_EVENT",
            "source_event_id": source_event_id,
        },
        "items": items,
        "revision": "",
        "state_hash": "0" * 64,
    }
    digest = compute_domain_state_hash(out)
    out["state_hash"] = digest
    out["revision"] = digest
    return validate_wardrobe_state(out, approved_item_ids=approved)
