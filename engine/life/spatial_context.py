"""Spatial A — SpatialContext v1 contract: pure normalize / hash / validate / build.

Static main-side bootstrap/runtime configuration holding approved location
identities and existing RouteProfile instances. Coordinates are optional
non-authoritative metadata (integer microdegrees only). Existing RouteProfile
remains the sole v1 travel-duration authority.

No production geography, bootstrap binding, runtime projection, reverse-route
inference, graph reachability, or external map/transit APIs.
Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Sequence

from .canonical import canonical_hash, canonical_json
from .contracts import validate_route_profile
from .errors import ErrorCode, LifeEngineError
from .schema import reject_binary_floats, validate_instance

SPATIAL_CONTEXT_HASH_EXCLUSIONS = frozenset({"context_hash"})

# Concrete place/route instances are not generic mechanics or mutable history.
ALLOWED_SPATIAL_PROVENANCE_ORIGINS = frozenset(
    {"CANON", "CANON_DERIVED", "SIMULATION_BOOTSTRAP"}
)

_LOCATION_KINDS = frozenset(
    {
        "HOME",
        "CAMPUS",
        "WORKPLACE",
        "GYM",
        "LOCAL_AREA",
        "STATION",
        "PUBLIC_PLACE",
        "OTHER",
    }
)

_GEO_PRECISIONS = frozenset({"AREA", "PUBLIC_POINT", "SYNTHETIC_POINT"})

_LAT_E6_MIN = -90_000_000
_LAT_E6_MAX = 90_000_000
_LON_E6_MIN = -180_000_000
_LON_E6_MAX = 180_000_000


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_true_int(value: Any, *, label: str) -> int:
    """Reject bool-as-int / float / string; require a true integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: expected true integer (bool/float/string rejected)")
    return value


def _require_non_empty_str(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label}: expected non-empty string")
    return value


def _route_semantic_key(route: Mapping[str, Any]) -> tuple[Any, ...]:
    """Match Slice 2E / activity_materialization ambiguity semantics."""
    return (
        route["origin_location_id"],
        route["destination_location_id"],
        route["mode"],
        int(route["duration_min"]),
        int(route["duration_max"]),
        route["effort_class"],
    )


def semantic_spatial_context_view(data: Mapping[str, Any]) -> dict[str, Any]:
    """Deep copy excluding self-referential ``context_hash``."""
    return {
        k: deepcopy(v) for k, v in data.items() if k not in SPATIAL_CONTEXT_HASH_EXCLUSIONS
    }


def _sort_by_id(
    records: list[Any],
    *,
    id_key: str,
    label: str,
) -> list[Any]:
    if not isinstance(records, list):
        _fail(f"{label}: expected list")
    keyed: list[tuple[str, str, Any]] = []
    for i, item in enumerate(records):
        if not isinstance(item, dict):
            _fail(f"{label}[{i}]: expected object")
        primary = item.get(id_key)
        if not isinstance(primary, str) or not primary:
            _fail(f"{label}[{i}].{id_key}: expected non-empty string")
        keyed.append((primary, canonical_json(item), item))
    keyed.sort(key=lambda t: (t[0], t[1]))
    return [item for _, _, item in keyed]


def _normalize_location_entry(row: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize an existing LocationEntry only (no invent-missing)."""
    if not isinstance(row, Mapping):
        _fail("location: expected object")
    # Preserve presence/absence of optional fields; do not invent defaults.
    return dict(row)


def _normalize_route_row(row: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        _fail("route_profile: expected object")
    return dict(row)


def normalize_spatial_context(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize existing SpatialContext values only.

    - sort locations by ``location_id``
    - sort route_profiles by ``route_profile_id``
    - no string/int/list coercion, no invent-missing, no input mutation
    """
    if not isinstance(data, Mapping):
        _fail("spatial_context: expected object")
    reject_binary_floats(dict(data), path="$.spatial_context")
    obj = deepcopy(dict(data))

    if "locations" in obj and obj["locations"] is not None:
        locations = obj["locations"]
        if not isinstance(locations, list):
            _fail("locations: expected list")
        normalized: list[dict[str, Any]] = []
        for i, item in enumerate(locations):
            if not isinstance(item, Mapping):
                _fail(f"locations[{i}]: expected object")
            normalized.append(_normalize_location_entry(item))
        obj["locations"] = _sort_by_id(
            normalized, id_key="location_id", label="locations"
        )

    if "route_profiles" in obj and obj["route_profiles"] is not None:
        routes = obj["route_profiles"]
        if not isinstance(routes, list):
            _fail("route_profiles: expected list")
        normalized_routes: list[dict[str, Any]] = []
        for i, item in enumerate(routes):
            if not isinstance(item, Mapping):
                _fail(f"route_profiles[{i}]: expected object")
            normalized_routes.append(_normalize_route_row(item))
        obj["route_profiles"] = _sort_by_id(
            normalized_routes, id_key="route_profile_id", label="route_profiles"
        )

    return obj


def compute_spatial_context_hash(data: Mapping[str, Any]) -> str:
    """SHA-256 over normalized semantic payload (excludes ``context_hash`` only)."""
    normalized = normalize_spatial_context(data)
    view = semantic_spatial_context_view(normalized)
    return canonical_hash(view)


def _unique_ids(values: list[str], *, label: str) -> None:
    if len(values) != len(set(values)):
        _fail(f"{label}: duplicate ids")


def _validate_provenance(provenance: Any, *, label: str) -> None:
    if not isinstance(provenance, Mapping):
        _fail(f"{label}: expected object")
    origin = provenance.get("origin")
    if origin not in ALLOWED_SPATIAL_PROVENANCE_ORIGINS:
        _fail(
            f"{label}.origin: SpatialContext v1 allows only "
            f"{sorted(ALLOWED_SPATIAL_PROVENANCE_ORIGINS)}; got {origin!r}"
        )


def _validate_geo_anchor(anchor: Any, *, label: str) -> None:
    if anchor is None:
        _fail(f"{label}: must not be null (omit field for absence)")
    if not isinstance(anchor, Mapping):
        _fail(f"{label}: expected object")
    allowed = frozenset({"precision", "lat_e6", "lon_e6", "radius_m"})
    unknown = set(anchor.keys()) - allowed
    if unknown:
        _fail(f"{label}: unknown fields {sorted(unknown)}")
    missing = allowed - set(anchor.keys())
    if missing:
        _fail(f"{label}: missing required fields {sorted(missing)}")

    precision = anchor["precision"]
    if precision not in _GEO_PRECISIONS:
        _fail(f"{label}.precision: invalid value {precision!r}")

    lat = _require_true_int(anchor["lat_e6"], label=f"{label}.lat_e6")
    lon = _require_true_int(anchor["lon_e6"], label=f"{label}.lon_e6")
    radius = _require_true_int(anchor["radius_m"], label=f"{label}.radius_m")

    if lat < _LAT_E6_MIN or lat > _LAT_E6_MAX:
        _fail(f"{label}.lat_e6: out of range [{_LAT_E6_MIN}, {_LAT_E6_MAX}]")
    if lon < _LON_E6_MIN or lon > _LON_E6_MAX:
        _fail(f"{label}.lon_e6: out of range [{_LON_E6_MIN}, {_LON_E6_MAX}]")

    if precision == "AREA":
        if radius < 1:
            _fail(f"{label}.radius_m: AREA requires radius_m >= 1")
    elif precision in {"PUBLIC_POINT", "SYNTHETIC_POINT"}:
        if radius != 0:
            _fail(f"{label}.radius_m: {precision} requires radius_m == 0")


def _validate_location_geo_authority(
    *,
    kind: str,
    geo_anchor: Mapping[str, Any],
    provenance: Mapping[str, Any],
    label: str,
) -> None:
    """Enforce #94 v1.2 HOME / SYNTHETIC_POINT privacy-authority boundary.

    - HOME geo_anchor may be AREA or SYNTHETIC_POINT only (not PUBLIC_POINT).
    - SYNTHETIC_POINT requires provenance.origin == SIMULATION_BOOTSTRAP.
    """
    precision = geo_anchor["precision"]
    if kind == "HOME" and precision == "PUBLIC_POINT":
        _fail(
            f"{label}.geo_anchor.precision: HOME forbids PUBLIC_POINT "
            "(use AREA or SYNTHETIC_POINT)"
        )
    if precision == "SYNTHETIC_POINT":
        origin = provenance.get("origin")
        if origin != "SIMULATION_BOOTSTRAP":
            _fail(
                f"{label}: SYNTHETIC_POINT requires provenance.origin="
                f"SIMULATION_BOOTSTRAP (got {origin!r})"
            )


def _validate_location_entry(row: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    label = f"locations[{index}]"
    if not isinstance(row, Mapping):
        _fail(f"{label}: expected object")

    allowed = frozenset({"location_id", "kind", "label", "geo_anchor", "provenance"})
    unknown = set(row.keys()) - allowed
    if unknown:
        _fail(f"{label}: unknown fields {sorted(unknown)}")
    for required in ("location_id", "kind", "provenance"):
        if required not in row:
            _fail(f"{label}: missing required field {required}")

    location_id = _require_non_empty_str(row["location_id"], label=f"{label}.location_id")
    kind = row["kind"]
    if kind not in _LOCATION_KINDS:
        _fail(f"{label}.kind: invalid value {kind!r}")

    out: dict[str, Any] = {
        "location_id": location_id,
        "kind": kind,
        "provenance": dict(row["provenance"]),
    }
    _validate_provenance(out["provenance"], label=f"{label}.provenance")

    if "label" in row:
        if row["label"] is None:
            _fail(f"{label}.label: must not be null (omit field for absence)")
        out["label"] = _require_non_empty_str(row["label"], label=f"{label}.label")

    if "geo_anchor" in row:
        _validate_geo_anchor(row["geo_anchor"], label=f"{label}.geo_anchor")
        out["geo_anchor"] = dict(row["geo_anchor"])
        _validate_location_geo_authority(
            kind=kind,
            geo_anchor=out["geo_anchor"],
            provenance=out["provenance"],
            label=label,
        )

    return out


def _reject_ambiguous_routes(routes: Sequence[Mapping[str, Any]]) -> None:
    """Fail closed on non-identical same-direction routes (Slice 2E semantics).

    Semantically identical duplicates (different route_profile_id, same OD +
    mode/duration/effort) remain compatibility-valid.
    """
    by_pair: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for route in routes:
        pair = (route["origin_location_id"], route["destination_location_id"])
        by_pair.setdefault(pair, []).append(route)

    for (origin, destination), forward in by_pair.items():
        if len(forward) < 2:
            continue
        keys = {_route_semantic_key(r) for r in forward}
        if len(keys) > 1:
            _fail(
                f"ambiguous non-identical forward routes for {origin}->{destination}: "
                f"{sorted(r['route_profile_id'] for r in forward)}"
            )


def validate_spatial_context(data: Mapping[str, Any]) -> dict[str, Any]:
    """Strict SpatialContext validation. Does not mutate the caller's mapping."""
    if not isinstance(data, Mapping):
        _fail("spatial_context: expected object")
    reject_binary_floats(dict(data), path="$.spatial_context")
    obj = normalize_spatial_context(data)

    # True-integer check for schema_version before schema (bool would pass as 1).
    if "schema_version" in obj:
        _require_true_int(obj["schema_version"], label="schema_version")

    validate_instance(obj, "spatial_context")

    _require_non_empty_str(obj.get("character_id"), label="character_id")
    _require_non_empty_str(
        obj.get("spatial_context_version"), label="spatial_context_version"
    )

    locations_in = obj["locations"]
    routes_in = obj["route_profiles"]
    if not isinstance(locations_in, list):
        _fail("locations: expected list")
    if not isinstance(routes_in, list):
        _fail("route_profiles: expected list")

    validated_locations: list[dict[str, Any]] = []
    for i, loc in enumerate(locations_in):
        if not isinstance(loc, Mapping):
            _fail(f"locations[{i}]: expected object")
        validated_locations.append(_validate_location_entry(loc, index=i))

    location_ids = [loc["location_id"] for loc in validated_locations]
    _unique_ids(location_ids, label="spatial_context.locations")
    declared = set(location_ids)

    validated_routes: list[dict[str, Any]] = []
    for i, route in enumerate(routes_in):
        if not isinstance(route, Mapping):
            _fail(f"route_profiles[{i}]: expected object")
        # Reuse existing RouteProfile semantics (self-route, duration, schema).
        validated = validate_route_profile(route)
        _validate_provenance(
            validated["provenance"],
            label=f"route_profiles[{i}].provenance",
        )
        origin = validated["origin_location_id"]
        destination = validated["destination_location_id"]
        if origin not in declared:
            _fail(
                f"route_profiles[{i}].origin_location_id: unknown location {origin!r}"
            )
        if destination not in declared:
            _fail(
                f"route_profiles[{i}].destination_location_id: "
                f"unknown location {destination!r}"
            )
        validated_routes.append(validated)

    route_ids = [r["route_profile_id"] for r in validated_routes]
    _unique_ids(route_ids, label="spatial_context.route_profiles")
    _reject_ambiguous_routes(validated_routes)

    obj["locations"] = _sort_by_id(
        validated_locations, id_key="location_id", label="locations"
    )
    obj["route_profiles"] = _sort_by_id(
        validated_routes, id_key="route_profile_id", label="route_profiles"
    )

    digest = compute_spatial_context_hash(obj)
    if obj.get("context_hash") != digest:
        _fail("spatial_context context_hash does not match semantic content")
    return obj


def build_spatial_context(
    *,
    character_id: str,
    spatial_context_version: str,
    locations: Sequence[Mapping[str, Any]] | None = None,
    route_profiles: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a sealed SpatialContext: draft -> hash -> set hash -> validate.

    Does not invent missing locations/routes or reverse routes.
    """
    if not isinstance(character_id, str) or not character_id:
        _fail("character_id: expected non-empty string")
    if not isinstance(spatial_context_version, str) or not spatial_context_version:
        _fail("spatial_context_version: expected non-empty string")
    if locations is None:
        locations_in: list[Mapping[str, Any]] = []
    elif isinstance(locations, (list, tuple)):
        locations_in = list(locations)
    else:
        _fail("locations: expected list")
    if route_profiles is None:
        routes_in: list[Mapping[str, Any]] = []
    elif isinstance(route_profiles, (list, tuple)):
        routes_in = list(route_profiles)
    else:
        _fail("route_profiles: expected list")

    draft: dict[str, Any] = {
        "schema_version": 1,
        "character_id": character_id,
        "spatial_context_version": spatial_context_version,
        "locations": [dict(loc) for loc in locations_in],
        "route_profiles": [dict(r) for r in routes_in],
        # Placeholder replaced after hash.
        "context_hash": "0" * 64,
    }
    digest = compute_spatial_context_hash(draft)
    draft["context_hash"] = digest
    return validate_spatial_context(draft)
