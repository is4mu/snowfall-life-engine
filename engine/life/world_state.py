"""Slice 3A world-state contracts and pure canon-projection boundary.

Defines strict deterministic snapshots for relation / home / wardrobe /
coarse consumables / finance. Projection accepts only caller-supplied
structured facts and approved IDs — no Bible prose scraping, no file or
network I/O, no effect application, no runtime persistence.

Projectors are a strict structured-fact boundary (not sanitizers):
unknown fields, type coercion, and speculative defaults are rejected.
"""

from __future__ import annotations

from copy import deepcopy
from typing import AbstractSet, Any, Mapping, Sequence

from .canonical import canonical_hash, canonical_json
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, parse_rfc3339

# Self-referential / operational identity fields excluded from semantic hash.
DOMAIN_STATE_HASH_EXCLUSIONS = frozenset({"state_hash", "revision"})

_SCHEMA_BY_DOMAIN = {
    "relation": "relation_state",
    "home": "home_state",
    "wardrobe": "wardrobe_state",
    "consumables": "consumables_state",
    "finance": "finance_state",
}

_PROJECTION_ORIGINS = frozenset(
    {
        "CANON",
        "CANON_DERIVED",
        "ENGINE_DEFAULT",
        "SIMULATION_BOOTSTRAP",
    }
)

# Projection input allowlists (fail closed on any other key).
_PERSON_ALLOWED = frozenset(
    {"person_id", "last_contact_at", "last_in_person_at", "open_promises"}
)
_PERSON_REQUIRED = frozenset({"person_id"})
_PROMISE_ALLOWED = frozenset({"promise_id"})
_PROMISE_REQUIRED = frozenset({"promise_id"})
_HOME_ENTITY_ALLOWED = frozenset({"entity_id", "status"})
_HOME_ENTITY_REQUIRED = frozenset({"entity_id"})
# Option A: lifecycle_state + wear_count_since_clean must be explicit.
# last_worn_at / location_ref may be omitted → null (formal unknown).
_WARDROBE_ITEM_ALLOWED = frozenset(
    {
        "item_id",
        "lifecycle_state",
        "last_worn_at",
        "wear_count_since_clean",
        "location_ref",
    }
)
_WARDROBE_ITEM_REQUIRED = frozenset(
    {"item_id", "lifecycle_state", "wear_count_since_clean"}
)
_STOCK_ALLOWED = frozenset({"consumable_id", "status"})
_STOCK_REQUIRED = frozenset({"consumable_id"})
_HOME_STATUS_VALUES = frozenset({"UNKNOWN", "NORMAL", "NEEDS_ATTENTION"})
_STOCK_STATUS_VALUES = frozenset({"UNKNOWN", "AVAILABLE", "LOW", "OUT"})
_WARDROBE_LIFECYCLE_VALUES = frozenset(
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


def _unique_ids(values: list[str], *, label: str) -> None:
    if len(values) != len(set(values)):
        _fail(f"{label}: duplicate ids")


def _require_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label}: expected object")
    return value


def _require_str(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label}: expected non-empty string")
    return value


def _require_nullable_str(value: Any, *, label: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, label=label)


def _require_int(value: Any, *, label: str) -> int:
    # bool is a subclass of int — reject explicitly; no string/float coercion.
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: expected integer (bool/float/string rejected)")
    return value


def _require_nullable_int(value: Any, *, label: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, label=label)


def _reject_binary_floats_in(value: Any, *, label: str) -> None:
    try:
        reject_binary_floats(value, path=label)
    except LifeEngineError:
        raise
    # Also catch float that somehow nested before reject helper walks.
    if isinstance(value, float):
        _fail(f"{label}: binary float forbidden")


def _pick_strict_fields(
    row: Mapping[str, Any],
    *,
    allowed: frozenset[str],
    required: frozenset[str],
    label: str,
) -> dict[str, Any]:
    """Allowlisted field pick; unknown keys and missing required keys fail closed."""
    _reject_binary_floats_in(dict(row), label=label)
    keys = set(row.keys())
    unknown = keys - allowed
    if unknown:
        _fail(f"{label}: unknown fields {sorted(unknown)}")
    missing = required - keys
    if missing:
        _fail(f"{label}: missing required fields {sorted(missing)}")
    return {k: row[k] for k in keys}


def semantic_domain_state_view(state: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deep copy of domain state excluding self-referential identity fields."""
    return {k: deepcopy(v) for k, v in state.items() if k not in DOMAIN_STATE_HASH_EXCLUSIONS}


def _world_ts(value: Any, *, field: str) -> str | None:
    """Canonicalize a WorldState timestamp without Foundation deep key scanning."""
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(f"{field}: timestamp must be string or null")
    return canonicalize_timestamp(value, field=field)


def _sort_world_records(
    records: list[Any],
    *,
    id_key: str,
    label: str,
) -> list[Any]:
    """Total order: primary id key, then full canonical JSON tie-break."""
    if not isinstance(records, list):
        _fail(f"{label}: expected list")
    keyed: list[tuple[Any, str, Any]] = []
    for i, item in enumerate(records):
        if not isinstance(item, dict):
            _fail(f"{label}[{i}]: expected object")
        primary = item.get(id_key) or ""
        keyed.append((primary, canonical_json(item), item))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in keyed]


def normalize_world_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """World-domain-only normalization (path-explicit; no Foundation global scanners).

    Canonicalize/sort **existing** values only. Does **not** invent missing
    required structures (no ``or []``, no null→empty, no missing-key fill).
    Incomplete snapshots remain incomplete so schema validation fail-closes.

    Unordered collections (when present as lists):
    - relation.people → person_id
    - relation.people[].open_promises → promise_id
    - home.entities → entity_id
    - wardrobe.items → item_id
    - consumables.stocks → consumable_id

    Timestamps (when present as keys):
    - snapshot.as_of
    - relation people[].last_contact_at / last_in_person_at
    - wardrobe items[].last_worn_at
    """
    obj = deepcopy(dict(state))
    domain = obj.get("domain")
    if domain not in _SCHEMA_BY_DOMAIN:
        _fail(f"normalize_world_state: unknown domain {domain!r}")

    if "as_of" in obj:
        obj["as_of"] = _world_ts(obj["as_of"], field="as_of")

    if domain == "relation":
        if "people" in obj and obj["people"] is not None:
            people = obj["people"]
            if not isinstance(people, list):
                _fail("people: expected list")
            normalized_people: list[dict[str, Any]] = []
            for i, person in enumerate(people):
                if not isinstance(person, dict):
                    _fail(f"people[{i}]: expected object")
                row = dict(person)
                if "last_contact_at" in row:
                    row["last_contact_at"] = _world_ts(
                        row["last_contact_at"],
                        field=f"people[{i}].last_contact_at",
                    )
                if "last_in_person_at" in row:
                    row["last_in_person_at"] = _world_ts(
                        row["last_in_person_at"],
                        field=f"people[{i}].last_in_person_at",
                    )
                if "open_promises" in row and row["open_promises"] is not None:
                    promises = row["open_promises"]
                    if not isinstance(promises, list):
                        _fail(f"people[{i}].open_promises: expected list")
                    row["open_promises"] = _sort_world_records(
                        promises,
                        id_key="promise_id",
                        label=f"people[{i}].open_promises",
                    )
                normalized_people.append(row)
            obj["people"] = _sort_world_records(
                normalized_people, id_key="person_id", label="people"
            )
    elif domain == "home":
        if "entities" in obj and obj["entities"] is not None:
            entities = obj["entities"]
            if not isinstance(entities, list):
                _fail("entities: expected list")
            obj["entities"] = _sort_world_records(
                entities, id_key="entity_id", label="entities"
            )
    elif domain == "wardrobe":
        if "items" in obj and obj["items"] is not None:
            items = obj["items"]
            if not isinstance(items, list):
                _fail("items: expected list")
            normalized_items: list[dict[str, Any]] = []
            for i, item in enumerate(items):
                if not isinstance(item, dict):
                    _fail(f"items[{i}]: expected object")
                row = dict(item)
                if "last_worn_at" in row:
                    row["last_worn_at"] = _world_ts(
                        row["last_worn_at"], field=f"items[{i}].last_worn_at"
                    )
                normalized_items.append(row)
            obj["items"] = _sort_world_records(
                normalized_items, id_key="item_id", label="items"
            )
    elif domain == "consumables":
        if "stocks" in obj and obj["stocks"] is not None:
            stocks = obj["stocks"]
            if not isinstance(stocks, list):
                _fail("stocks: expected list")
            obj["stocks"] = _sort_world_records(
                stocks, id_key="consumable_id", label="stocks"
            )
    elif domain == "finance":
        pass

    return obj


def compute_domain_state_hash(state: Mapping[str, Any]) -> str:
    """Content-derived SHA-256 over WorldState-normalized semantic payload.

    Uses existing ``canonical_hash`` (NFC, sorted keys, no floats) after
    WorldState-local timestamp/unordered normalization — not Foundation
    ``normalize_persisted_object`` / global key scanners.
    Excludes ``state_hash`` and ``revision`` so identity is not circular.
    """
    normalized = normalize_world_state(state)
    view = semantic_domain_state_view(normalized)
    return canonical_hash(view)


def _seal_identity(state: dict[str, Any]) -> dict[str, Any]:
    digest = compute_domain_state_hash(state)
    state["state_hash"] = digest
    state["revision"] = digest
    return state


def _validate_provenance(provenance: Mapping[str, Any]) -> dict[str, Any]:
    prov_map = _require_mapping(provenance, label="provenance")
    reject_binary_floats(dict(prov_map), path="$.provenance")
    validate_instance(dict(prov_map), "provenance")
    return dict(prov_map)


def _validate_envelope_hash(obj: dict[str, Any]) -> None:
    expected = compute_domain_state_hash(obj)
    if obj.get("state_hash") != expected:
        _fail("domain state_hash does not match semantic content")
    if obj.get("revision") != expected:
        _fail("domain revision does not match semantic content")


def _require_not_after_as_of(ts: str | None, *, as_of: str, field: str) -> None:
    if ts is None:
        return
    instant = parse_rfc3339(ts, field=field)
    boundary = parse_rfc3339(as_of, field="as_of")
    if instant > boundary:
        _fail(f"{field} after as_of")


def _normalize_base(data: Mapping[str, Any]) -> dict[str, Any]:
    # Reject floats before WorldState normalize (canonical_json forbids floats).
    obj = dict(data)
    reject_binary_floats(obj)
    return normalize_world_state(obj)


def validate_relation_state(
    data: Mapping[str, Any],
    *,
    known_person_ids: AbstractSet[str],
) -> dict[str, Any]:
    obj = _normalize_base(data)
    validate_instance(obj, "relation_state")
    people = obj["people"]
    person_ids = [p["person_id"] for p in people]
    _unique_ids(person_ids, label="relation.people")
    as_of = obj["as_of"]
    for i, person in enumerate(people):
        pid = person["person_id"]
        if pid not in known_person_ids:
            _fail(f"relation unknown person_id: {pid}")
        _require_not_after_as_of(
            person.get("last_contact_at"),
            as_of=as_of,
            field=f"people[{i}].last_contact_at",
        )
        _require_not_after_as_of(
            person.get("last_in_person_at"),
            as_of=as_of,
            field=f"people[{i}].last_in_person_at",
        )
        promise_ids = [p["promise_id"] for p in person["open_promises"]]
        _unique_ids(promise_ids, label=f"relation.people[{i}].open_promises")
    _validate_envelope_hash(obj)
    return obj


def validate_home_state(
    data: Mapping[str, Any],
    *,
    known_entity_ids: AbstractSet[str],
) -> dict[str, Any]:
    obj = _normalize_base(data)
    validate_instance(obj, "home_state")
    entity_ids = [e["entity_id"] for e in obj["entities"]]
    _unique_ids(entity_ids, label="home.entities")
    for eid in entity_ids:
        if eid not in known_entity_ids:
            _fail(f"home unknown entity_id: {eid}")
    _validate_envelope_hash(obj)
    return obj


def validate_wardrobe_state(
    data: Mapping[str, Any],
    *,
    approved_item_ids: AbstractSet[str],
) -> dict[str, Any]:
    obj = _normalize_base(data)
    validate_instance(obj, "wardrobe_state")
    item_ids = [it["item_id"] for it in obj["items"]]
    _unique_ids(item_ids, label="wardrobe.items")
    as_of = obj["as_of"]
    for i, item in enumerate(obj["items"]):
        iid = item["item_id"]
        if iid not in approved_item_ids:
            _fail(f"wardrobe unknown item_id: {iid}")
        _require_not_after_as_of(
            item.get("last_worn_at"),
            as_of=as_of,
            field=f"items[{i}].last_worn_at",
        )
    _validate_envelope_hash(obj)
    return obj


def validate_consumables_state(
    data: Mapping[str, Any],
    *,
    approved_consumable_ids: AbstractSet[str],
) -> dict[str, Any]:
    obj = _normalize_base(data)
    validate_instance(obj, "consumables_state")
    stock_ids = [s["consumable_id"] for s in obj["stocks"]]
    _unique_ids(stock_ids, label="consumables.stocks")
    for cid in stock_ids:
        if cid not in approved_consumable_ids:
            _fail(f"consumables unknown consumable_id: {cid}")
    _validate_envelope_hash(obj)
    return obj


def validate_finance_state(data: Mapping[str, Any]) -> dict[str, Any]:
    obj = _normalize_base(data)
    validate_instance(obj, "finance_state")
    balance = obj["balance_minor"]
    if balance is not None and (
        isinstance(balance, bool) or not isinstance(balance, int)
    ):
        _fail("finance balance_minor must be integer or null")
    _validate_envelope_hash(obj)
    return obj


def canonicalize_relation_state(
    data: Mapping[str, Any],
    *,
    known_person_ids: AbstractSet[str],
) -> dict[str, Any]:
    return validate_relation_state(data, known_person_ids=known_person_ids)


def canonicalize_home_state(
    data: Mapping[str, Any],
    *,
    known_entity_ids: AbstractSet[str],
) -> dict[str, Any]:
    return validate_home_state(data, known_entity_ids=known_entity_ids)


def canonicalize_wardrobe_state(
    data: Mapping[str, Any],
    *,
    approved_item_ids: AbstractSet[str],
) -> dict[str, Any]:
    return validate_wardrobe_state(data, approved_item_ids=approved_item_ids)


def canonicalize_consumables_state(
    data: Mapping[str, Any],
    *,
    approved_consumable_ids: AbstractSet[str],
) -> dict[str, Any]:
    return validate_consumables_state(
        data, approved_consumable_ids=approved_consumable_ids
    )


def canonicalize_finance_state(data: Mapping[str, Any]) -> dict[str, Any]:
    return validate_finance_state(data)


def _projection_provenance(provenance: Mapping[str, Any]) -> dict[str, Any]:
    prov = _validate_provenance(provenance)
    origin = prov["origin"]
    if origin not in _PROJECTION_ORIGINS:
        _fail(
            "projection provenance.origin must be CANON / CANON_DERIVED / "
            f"ENGINE_DEFAULT / SIMULATION_BOOTSTRAP (got {origin!r})"
        )
    return prov


def _base_snapshot(
    *,
    domain: str,
    character_id: str,
    as_of: str,
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    _require_str(character_id, label="character_id")
    _require_str(as_of, label="as_of")
    as_of_canon = canonicalize_timestamp(as_of, field="as_of")
    return {
        "schema_version": 1,
        "domain": domain,
        "character_id": character_id,
        "as_of": as_of_canon,
        "provenance": _projection_provenance(provenance),
        # Placeholders replaced by _seal_identity after payload assembly.
        "revision": "",
        "state_hash": "0" * 64,
    }


def _parse_open_promises(raw: Any, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        _fail(f"{label}: open_promises must be a list")
    out: list[dict[str, Any]] = []
    for i, entry in enumerate(raw):
        row = _pick_strict_fields(
            _require_mapping(entry, label=f"{label}[{i}]"),
            allowed=_PROMISE_ALLOWED,
            required=_PROMISE_REQUIRED,
            label=f"{label}[{i}]",
        )
        out.append({"promise_id": _require_str(row["promise_id"], label=f"{label}[{i}].promise_id")})
    return out


def _parse_person_fact(entry: Any, *, index: int) -> dict[str, Any]:
    label = f"people[{index}]"
    row = _pick_strict_fields(
        _require_mapping(entry, label=label),
        allowed=_PERSON_ALLOWED,
        required=_PERSON_REQUIRED,
        label=label,
    )
    # Omitted contact timestamps → null (formal unknown). Omitted promises → [].
    if "open_promises" in row:
        promises = _parse_open_promises(row["open_promises"], label=f"{label}.open_promises")
    else:
        promises = []
    return {
        "person_id": _require_str(row["person_id"], label=f"{label}.person_id"),
        "last_contact_at": _require_nullable_str(
            row.get("last_contact_at", None), label=f"{label}.last_contact_at"
        ),
        "last_in_person_at": _require_nullable_str(
            row.get("last_in_person_at", None), label=f"{label}.last_in_person_at"
        ),
        "open_promises": promises,
    }


def _parse_home_entity(entry: Any, *, index: int) -> dict[str, Any]:
    label = f"entities[{index}]"
    row = _pick_strict_fields(
        _require_mapping(entry, label=label),
        allowed=_HOME_ENTITY_ALLOWED,
        required=_HOME_ENTITY_REQUIRED,
        label=label,
    )
    # Omitted status → UNKNOWN (sparse/unknown-safe; not a known physical fact).
    if "status" in row:
        status = _require_str(row["status"], label=f"{label}.status")
    else:
        status = "UNKNOWN"
    if status not in _HOME_STATUS_VALUES:
        _fail(f"{label}.status: invalid value {status!r}")
    return {
        "entity_id": _require_str(row["entity_id"], label=f"{label}.entity_id"),
        "status": status,
    }


def _parse_wardrobe_item(entry: Any, *, index: int) -> dict[str, Any]:
    label = f"items[{index}]"
    row = _pick_strict_fields(
        _require_mapping(entry, label=label),
        allowed=_WARDROBE_ITEM_ALLOWED,
        required=_WARDROBE_ITEM_REQUIRED,
        label=label,
    )
    lifecycle = _require_str(row["lifecycle_state"], label=f"{label}.lifecycle_state")
    if lifecycle not in _WARDROBE_LIFECYCLE_VALUES:
        _fail(f"{label}.lifecycle_state: invalid value {lifecycle!r}")
    # No default CLEAN_AVAILABLE / 0 — caller must supply explicit facts.
    wear_count = _require_int(
        row["wear_count_since_clean"], label=f"{label}.wear_count_since_clean"
    )
    if wear_count < 0:
        _fail(f"{label}.wear_count_since_clean: must be >= 0")
    # Omitted nullable fields → null (formal unknown). Present keys must type-check.
    if "last_worn_at" in row:
        last_worn = _require_nullable_str(row["last_worn_at"], label=f"{label}.last_worn_at")
    else:
        last_worn = None
    if "location_ref" in row:
        location_ref = _require_nullable_str(row["location_ref"], label=f"{label}.location_ref")
    else:
        location_ref = None
    return {
        "item_id": _require_str(row["item_id"], label=f"{label}.item_id"),
        "lifecycle_state": lifecycle,
        "last_worn_at": last_worn,
        "wear_count_since_clean": wear_count,
        "location_ref": location_ref,
    }


def _parse_stock(entry: Any, *, index: int) -> dict[str, Any]:
    label = f"stocks[{index}]"
    row = _pick_strict_fields(
        _require_mapping(entry, label=label),
        allowed=_STOCK_ALLOWED,
        required=_STOCK_REQUIRED,
        label=label,
    )
    # Omitted status → UNKNOWN (explicit sparse unknown; not an invented quantity).
    if "status" in row:
        status = _require_str(row["status"], label=f"{label}.status")
    else:
        status = "UNKNOWN"
    if status not in _STOCK_STATUS_VALUES:
        _fail(f"{label}.status: invalid value {status!r}")
    return {
        "consumable_id": _require_str(row["consumable_id"], label=f"{label}.consumable_id"),
        "status": status,
    }


def project_initial_relation_state(
    *,
    character_id: str,
    as_of: str,
    provenance: Mapping[str, Any],
    known_person_ids: AbstractSet[str],
    people: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure initial RelationState from explicit structured people facts only."""
    known = frozenset(known_person_ids)
    if people is None:
        people_in: list[Any] = []
    elif not isinstance(people, (list, tuple)):
        _fail("people: expected list")
    else:
        people_in = list(people)
    people_payload = [_parse_person_fact(entry, index=i) for i, entry in enumerate(people_in)]
    snap = _base_snapshot(
        domain="relation",
        character_id=character_id,
        as_of=as_of,
        provenance=provenance,
    )
    snap["people"] = people_payload
    sealed = _seal_identity(snap)
    return validate_relation_state(sealed, known_person_ids=known)


def project_initial_home_state(
    *,
    character_id: str,
    as_of: str,
    provenance: Mapping[str, Any],
    known_entity_ids: AbstractSet[str],
    entities: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure initial HomeState; empty when no approved trackable entities supplied."""
    known = frozenset(known_entity_ids)
    if entities is None:
        entities_in: list[Any] = []
    elif not isinstance(entities, (list, tuple)):
        _fail("entities: expected list")
    else:
        entities_in = list(entities)
    entities_payload = [
        _parse_home_entity(entry, index=i) for i, entry in enumerate(entities_in)
    ]
    snap = _base_snapshot(
        domain="home",
        character_id=character_id,
        as_of=as_of,
        provenance=provenance,
    )
    snap["entities"] = entities_payload
    sealed = _seal_identity(snap)
    return validate_home_state(sealed, known_entity_ids=known)


def project_initial_wardrobe_state(
    *,
    character_id: str,
    as_of: str,
    provenance: Mapping[str, Any],
    approved_item_ids: AbstractSet[str],
    items: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure initial WardrobeState; empty when no approved inventory facts supplied.

    ``lifecycle_state`` and ``wear_count_since_clean`` are required per item
    (Option A — no CLEAN_AVAILABLE / 0 invention). ``last_worn_at`` /
    ``location_ref`` may be omitted and become null (formal unknown).
    """
    approved = frozenset(approved_item_ids)
    if items is None:
        items_in: list[Any] = []
    elif not isinstance(items, (list, tuple)):
        _fail("items: expected list")
    else:
        items_in = list(items)
    items_payload = [_parse_wardrobe_item(entry, index=i) for i, entry in enumerate(items_in)]
    snap = _base_snapshot(
        domain="wardrobe",
        character_id=character_id,
        as_of=as_of,
        provenance=provenance,
    )
    snap["items"] = items_payload
    sealed = _seal_identity(snap)
    return validate_wardrobe_state(sealed, approved_item_ids=approved)


def project_initial_consumables_state(
    *,
    character_id: str,
    as_of: str,
    provenance: Mapping[str, Any],
    approved_consumable_ids: AbstractSet[str],
    stocks: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure initial ConsumablesState; coarse status only."""
    approved = frozenset(approved_consumable_ids)
    if stocks is None:
        stocks_in: list[Any] = []
    elif not isinstance(stocks, (list, tuple)):
        _fail("stocks: expected list")
    else:
        stocks_in = list(stocks)
    stocks_payload = [_parse_stock(entry, index=i) for i, entry in enumerate(stocks_in)]
    snap = _base_snapshot(
        domain="consumables",
        character_id=character_id,
        as_of=as_of,
        provenance=provenance,
    )
    snap["stocks"] = stocks_payload
    sealed = _seal_identity(snap)
    return validate_consumables_state(sealed, approved_consumable_ids=approved)


def project_initial_finance_state(
    *,
    character_id: str,
    as_of: str,
    provenance: Mapping[str, Any],
    balance_minor: int | None = None,
) -> dict[str, Any]:
    """Pure initial FinanceState. Unapproved balance remains null (never inferred).

    Slice 3A v1 is currency=JPY + balance_minor only (no transactions_cursor).
    """
    balance = _require_nullable_int(balance_minor, label="balance_minor")
    snap = _base_snapshot(
        domain="finance",
        character_id=character_id,
        as_of=as_of,
        provenance=provenance,
    )
    snap["currency"] = "JPY"
    snap["balance_minor"] = balance
    sealed = _seal_identity(snap)
    return validate_finance_state(sealed)


def domain_ref_values(state: Mapping[str, Any]) -> dict[str, str]:
    """Revision/hash pair suitable for later CurrentState.domain_refs wiring.

    Does not mutate CurrentState. Slice 3A exposes helpers only.
    """
    validated_domain = state.get("domain")
    if validated_domain not in _SCHEMA_BY_DOMAIN:
        _fail(f"unknown domain for domain_ref_values: {validated_domain!r}")
    return {
        "revision": str(state["revision"]),
        "state_hash": str(state["state_hash"]),
    }
