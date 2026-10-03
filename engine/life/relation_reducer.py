"""Slice 3B — pure RelationState reducer for strict RELATION_TOUCH.

Applies factual contact recency updates only. Does not wire into Foundation
``apply_effect``, CurrentState, clock/queue, runtime persistence, or ActualEvent
finalization. No friendship/closeness/trust, promise open/close, or canon I/O.

Layering:
- JSON Schema (``effect.schema.json``) is the **structural** layer (required keys,
  types, ``additionalProperties: false``). Schema alone does **not** enforce
  Asia/Tokyo canonical form or ``in_person_at`` same-instant semantics.
- ``normalize_relation_touch_effect`` is the **semantic** layer: canonicalize
  timestamps, reject bad/naive/fractional inputs, and enforce null-or-same-instant.
- ``reduce_relation_touch`` consumes that already-canonicalized payload and applies
  domain transition rules (known person, monotonic ``as_of``, row update/create).
"""

from __future__ import annotations

from copy import deepcopy
from typing import AbstractSet, Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339
from .world_state import compute_domain_state_hash, validate_relation_state


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def normalize_relation_touch_effect(effect: Mapping[str, Any]) -> dict[str, Any]:
    """Semantic normalize for strict RELATION_TOUCH; does not mutate caller input.

    Structural validation via JSON Schema, then:
    - canonicalize ``contact_at`` to Asia/Tokyo ``+09:00`` second precision
    - canonicalize non-null ``in_person_at`` the same way
    - reject bad / naive / fractional timestamps (via WorldState-local time rules)
    - require ``in_person_at`` is null or the same instant as ``contact_at``

    Returns a new envelope whose ``payload`` timestamps are already canonical.
    """
    data = dict(effect)
    if "schema_version" not in data:
        data["schema_version"] = 1
    reject_binary_floats(data, path="$.effect")
    validate_instance(data, "effect")
    if data.get("effect_type") != "RELATION_TOUCH":
        _fail(f"expected RELATION_TOUCH, got {data.get('effect_type')!r}")
    raw_payload = data.get("payload")
    if not isinstance(raw_payload, Mapping):
        _fail("RELATION_TOUCH payload must be an object")

    person_id = raw_payload.get("person_id")
    if not isinstance(person_id, str) or not person_id:
        _fail("person_id: expected non-empty string")

    contact_at = canonicalize_timestamp(raw_payload["contact_at"], field="contact_at")
    raw_in_person = raw_payload["in_person_at"]
    if raw_in_person is None:
        in_person_at: str | None = None
    else:
        in_person_at = canonicalize_timestamp(raw_in_person, field="in_person_at")
        if parse_rfc3339(in_person_at, field="in_person_at") != parse_rfc3339(
            contact_at, field="contact_at"
        ):
            _fail("in_person_at must be null or the same instant as contact_at")

    data["payload"] = {
        "person_id": person_id,
        "contact_at": contact_at,
        "in_person_at": in_person_at,
    }
    return data


def reduce_relation_touch(
    relation_state: Mapping[str, Any],
    effect: Mapping[str, Any],
    *,
    source_event_id: str,
    known_person_ids: AbstractSet[str],
) -> dict[str, Any]:
    """Pure RelationState transition for one strict RELATION_TOUCH effect.

    Inputs are not mutated. No file/network I/O, wall clock, or PRNG.
    Timestamp / same-instant semantics are owned by
    ``normalize_relation_touch_effect``; this function consumes that result.
    """
    if not isinstance(source_event_id, str) or not source_event_id:
        _fail("source_event_id: expected non-empty string")

    known = frozenset(known_person_ids)
    prior = validate_relation_state(relation_state, known_person_ids=known)
    typed = normalize_relation_touch_effect(effect)
    payload = typed["payload"]

    person_id = payload["person_id"]
    contact_at = payload["contact_at"]
    in_person_at = payload["in_person_at"]

    if person_id not in known:
        _fail(f"relation unknown person_id: {person_id}")

    prior_as_of = prior["as_of"]
    if parse_rfc3339(contact_at, field="contact_at") < parse_rfc3339(
        prior_as_of, field="as_of"
    ):
        _fail(
            f"RELATION_TOUCH contact_at before state.as_of "
            f"(contact_at={contact_at}, as_of={prior_as_of})"
        )

    people: list[dict[str, Any]] = deepcopy(prior["people"])
    match_idx = next(
        (i for i, row in enumerate(people) if row["person_id"] == person_id),
        None,
    )

    if match_idx is None:
        people.append(
            {
                "person_id": person_id,
                "last_contact_at": contact_at,
                "last_in_person_at": in_person_at,
                "open_promises": [],
            }
        )
    else:
        row = people[match_idx]
        row["last_contact_at"] = contact_at
        if in_person_at is not None:
            row["last_in_person_at"] = in_person_at
        # else: preserve previous last_in_person_at and open_promises exactly

    out: dict[str, Any] = {
        "schema_version": prior["schema_version"],
        "domain": prior["domain"],
        "character_id": prior["character_id"],
        "as_of": contact_at,
        "provenance": {
            "origin": "ACTUAL_EVENT",
            "source_event_id": source_event_id,
        },
        "people": people,
        "revision": "",
        "state_hash": "0" * 64,
    }
    digest = compute_domain_state_hash(out)
    out["state_hash"] = digest
    out["revision"] = digest
    return validate_relation_state(out, known_person_ids=known)
