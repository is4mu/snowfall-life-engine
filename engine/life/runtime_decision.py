"""Life Engine v2 Slice 5B2A — RuntimeDecisionFrame core (pure, one-decision).

Occurrence-unique decision identity + structural/routine snapshot composition.
Does not start/finalize activities, run social 2G/2H, reconcile wakeups,
capture, mutate RuntimeBundle, or write disk/Git/life branch.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .activity_runtime import build_active_decision_context
from .canonical import canonical_hash, canonical_json, persisted_hash
from .checkpoint import validate_checkpoint
from .decisions import (
    ActionCandidate,
    DecisionBoundaryFact,
    DecisionResolution,
    ResolvedOpportunity,
    SoftDecisionGuard,
    collect_action_candidates,
    opportunity_to_candidate,
    parse_decision_boundary_fact,
    parse_resolved_opportunity,
    parse_soft_decision_guard,
    resolve_action_decision,
)
from .derived import classify_physical_fatigue_band
from .domain_adapters import (
    AdapterResult,
    DomainAdapterContext,
    adapt_structural_domains,
)
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .ids import stable_id
from .policy import hash_policy
from .routine_adapters import (
    HouseholdTaskFact,
    KickboxingSessionFact,
    RoutineAdapterContext,
    RoutineAdapterResult,
    adapt_routine_domains,
    parse_household_task_fact,
    parse_kickboxing_session_fact,
)
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .schema import reject_binary_floats, validate_instance
from .timeutil import require_canonical_timestamp
from .wakeups import (
    KnownDecisionBoundary,
    TRIGGER_KINDS,
    assert_allowed_v2_threshold_tuple,
    validate_known_decision_boundary,
)

_TRIGGER_FIELDS = frozenset(
    {
        "trigger_id",
        "occurred_at",
        "trigger_kind",
        "semantic_decision_key",
        "reason_code",
        "metric",
        "target_band",
        "direction",
        "source_kind",
        "source_ref",
    }
)

_FACTS_FIELDS = frozenset(
    {
        "available_window_min",
        "route_profiles",
        "structural_physical_feasible_by_action",
        "household_tasks",
        "kickboxing_sessions",
        "routine_physical_feasible_by_action",
        "kickboxing_location_feasible",
        "kickboxing_self_generated_today",
        "kickboxing_equivalent_pending_plans",
        "continuation_feasible",
        "meal_feasible",
        "rest_feasible",
        "sleep_pressure",
        "sleep_opportunity",
        "last_meal_at",
        "meal_extent",
        "soft_guards",
        "free_window_key",
        "free_window_min",
        "feasible_leisure_categories",
    }
)

_SLEEP_OPPORTUNITIES = frozenset({"BLOCKED", "NORMAL", "FAVORABLE"})
_MEAL_EXTENTS = frozenset({"LIGHT", "STANDARD"})


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_nonempty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _require_optional_nonempty_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_nonempty_str(value, label)


def _require_true_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be a true int (no bool/float/string)")
    return value


def _require_optional_true_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _require_true_int(value, label)


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{label} must be a bool")
    return value


def _require_optional_bool(value: object, label: str) -> bool | None:
    if value is None:
        return None
    return _require_bool(value, label)


def _reject_unknown_fields(
    data: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = set(data) - allowed
    if unknown:
        _fail(f"{label} unknown fields: {sorted(unknown)}")


def _require_list_or_tuple(value: object, label: str) -> list[Any] | tuple[Any, ...]:
    """Approved public collection contract: exact list/tuple only (no str/bytes/other Sequence)."""
    if isinstance(value, (str, bytes)):
        _fail(f"{label} must be a list/tuple (bare string/bytes rejected)")
    if not isinstance(value, (list, tuple)):
        _fail(f"{label} must be a list/tuple")
    return value


def _require_mapping_item(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a mapping (no list-of-pairs / iterable coercion)")
    return value


def _require_bool_map(value: object, label: str) -> dict[str, bool]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a mapping")
    out: dict[str, bool] = {}
    for key, item in value.items():
        k = _require_nonempty_str(key, f"{label} key")
        out[k] = _require_bool(item, f"{label}[{k!r}]")
    return out


def _mapping_snapshot(obj: Any) -> Any:
    """Deep-copy mapping/list trees for non-mutation proofs; leave others intact."""
    if isinstance(obj, Mapping):
        return {k: _mapping_snapshot(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_mapping_snapshot(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_mapping_snapshot(v) for v in obj)
    return obj


@dataclass(frozen=True)
class RuntimeDecisionTrigger:
    trigger_id: str
    occurred_at: str
    trigger_kind: str
    semantic_decision_key: str
    reason_code: str
    metric: str | None
    target_band: str | None
    direction: str | None
    source_kind: str | None
    source_ref: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "trigger_id": self.trigger_id,
            "occurred_at": self.occurred_at,
            "trigger_kind": self.trigger_kind,
            "semantic_decision_key": self.semantic_decision_key,
            "reason_code": self.reason_code,
            "metric": self.metric,
            "target_band": self.target_band,
            "direction": self.direction,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class RuntimeDecisionFacts:
    available_window_min: int
    route_profiles: tuple[Mapping[str, Any], ...]
    structural_physical_feasible_by_action: Mapping[str, bool]
    household_tasks: tuple[HouseholdTaskFact | Mapping[str, Any], ...]
    kickboxing_sessions: tuple[KickboxingSessionFact | Mapping[str, Any], ...]
    routine_physical_feasible_by_action: Mapping[str, bool]
    kickboxing_location_feasible: bool
    kickboxing_self_generated_today: int
    kickboxing_equivalent_pending_plans: int
    continuation_feasible: bool | None
    meal_feasible: bool
    rest_feasible: bool
    sleep_pressure: int | None
    sleep_opportunity: str | None
    last_meal_at: str | None
    meal_extent: str | None
    soft_guards: tuple[SoftDecisionGuard | Mapping[str, Any], ...]
    free_window_key: str | None
    free_window_min: int | None
    feasible_leisure_categories: tuple[str, ...]


@dataclass(frozen=True)
class RuntimeDecisionFrame:
    runtime_decision_key: str
    trigger: RuntimeDecisionTrigger
    decision_snapshot_hash: str
    decision_facts_hash: str
    materialization_context_hash: str
    finalization_context_hash: str
    input_state_refs: tuple[str, ...]
    structural_result: AdapterResult
    routine_result: RoutineAdapterResult
    merged_opportunities: tuple[ResolvedOpportunity, ...]
    merged_known_boundaries: tuple[KnownDecisionBoundary, ...]
    raw_candidates: tuple[ActionCandidate, ...]
    resolution: DecisionResolution
    selected_candidate: ActionCandidate | None


def parse_runtime_decision_trigger(
    raw: RuntimeDecisionTrigger | Mapping[str, Any],
) -> RuntimeDecisionTrigger:
    if isinstance(raw, RuntimeDecisionTrigger):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeDecisionTrigger must be mapping or dataclass")
    reject_binary_floats(data)
    _reject_unknown_fields(data, _TRIGGER_FIELDS, "RuntimeDecisionTrigger")
    missing = _TRIGGER_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeDecisionTrigger missing fields: {sorted(missing)}")

    trigger_kind = _require_nonempty_str(data["trigger_kind"], "trigger_kind")
    if trigger_kind not in TRIGGER_KINDS:
        _fail(f"unknown trigger_kind: {trigger_kind}")

    metric = data["metric"]
    target_band = data["target_band"]
    direction = data["direction"]
    if trigger_kind == "THRESHOLD":
        metric_s = _require_nonempty_str(metric, "metric")
        band_s = _require_nonempty_str(target_band, "target_band")
        direction_s = _require_nonempty_str(direction, "direction")
        assert_allowed_v2_threshold_tuple(metric_s, band_s, direction_s)
    else:
        if metric is not None or target_band is not None or direction is not None:
            _fail(
                "non-THRESHOLD RuntimeDecisionTrigger must have null "
                "metric/target_band/direction"
            )
        metric_s = None
        band_s = None
        direction_s = None

    return RuntimeDecisionTrigger(
        trigger_id=_require_nonempty_str(data["trigger_id"], "trigger_id"),
        occurred_at=require_canonical_timestamp(data["occurred_at"], field="occurred_at"),
        trigger_kind=trigger_kind,
        semantic_decision_key=_require_nonempty_str(
            data["semantic_decision_key"], "semantic_decision_key"
        ),
        reason_code=_require_nonempty_str(data["reason_code"], "reason_code"),
        metric=metric_s,
        target_band=band_s,
        direction=direction_s,
        source_kind=_require_optional_nonempty_str(data["source_kind"], "source_kind"),
        source_ref=_require_optional_nonempty_str(data["source_ref"], "source_ref"),
    )


def parse_runtime_decision_facts(
    raw: RuntimeDecisionFacts | Mapping[str, Any],
    *,
    now: str | None = None,
) -> RuntimeDecisionFacts:
    """Strict explicit-facts parser. No hidden defaults."""
    if isinstance(raw, RuntimeDecisionFacts):
        data = {
            "available_window_min": raw.available_window_min,
            "route_profiles": list(raw.route_profiles),
            "structural_physical_feasible_by_action": dict(
                raw.structural_physical_feasible_by_action
            ),
            "household_tasks": list(raw.household_tasks),
            "kickboxing_sessions": list(raw.kickboxing_sessions),
            "routine_physical_feasible_by_action": dict(
                raw.routine_physical_feasible_by_action
            ),
            "kickboxing_location_feasible": raw.kickboxing_location_feasible,
            "kickboxing_self_generated_today": raw.kickboxing_self_generated_today,
            "kickboxing_equivalent_pending_plans": raw.kickboxing_equivalent_pending_plans,
            "continuation_feasible": raw.continuation_feasible,
            "meal_feasible": raw.meal_feasible,
            "rest_feasible": raw.rest_feasible,
            "sleep_pressure": raw.sleep_pressure,
            "sleep_opportunity": raw.sleep_opportunity,
            "last_meal_at": raw.last_meal_at,
            "meal_extent": raw.meal_extent,
            "soft_guards": list(raw.soft_guards),
            "free_window_key": raw.free_window_key,
            "free_window_min": raw.free_window_min,
            "feasible_leisure_categories": list(raw.feasible_leisure_categories),
        }
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeDecisionFacts must be mapping or dataclass")

    reject_binary_floats(data)
    _reject_unknown_fields(data, _FACTS_FIELDS, "RuntimeDecisionFacts")
    missing = _FACTS_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeDecisionFacts missing fields: {sorted(missing)}")

    now_c = None if now is None else require_canonical_timestamp(now, field="now")

    routes_raw = _require_list_or_tuple(data["route_profiles"], "route_profiles")
    route_profiles: list[dict[str, Any]] = []
    for i, item in enumerate(routes_raw):
        route_map = _require_mapping_item(item, f"route_profiles[{i}]")
        route_profiles.append(deepcopy(dict(route_map)))
    route_profiles_t = tuple(route_profiles)

    household_raw = _require_list_or_tuple(data["household_tasks"], "household_tasks")
    household_tasks = tuple(parse_household_task_fact(item) for item in household_raw)

    sessions_raw = _require_list_or_tuple(data["kickboxing_sessions"], "kickboxing_sessions")
    if now_c is None and sessions_raw:
        _fail("now required to validate kickboxing_sessions")
    kickboxing_sessions = tuple(
        parse_kickboxing_session_fact(item, now=now_c) for item in sessions_raw
    )

    soft_raw = _require_list_or_tuple(data["soft_guards"], "soft_guards")
    soft_guards = tuple(
        parse_soft_decision_guard(item, now=now_c) for item in soft_raw
    )

    leisure_raw = _require_list_or_tuple(
        data["feasible_leisure_categories"], "feasible_leisure_categories"
    )
    leisure = tuple(
        _require_nonempty_str(item, "feasible_leisure_categories item")
        for item in leisure_raw
    )

    sleep_pressure = _require_optional_true_int(data["sleep_pressure"], "sleep_pressure")
    sleep_opportunity = data["sleep_opportunity"]
    if sleep_pressure is not None:
        sleep_opp_s = _require_nonempty_str(sleep_opportunity, "sleep_opportunity")
        if sleep_opp_s not in _SLEEP_OPPORTUNITIES:
            _fail(f"invalid sleep_opportunity: {sleep_opp_s}")
    else:
        if sleep_opportunity is not None:
            _fail("sleep_opportunity must be null when sleep_pressure is null")
        sleep_opp_s = None

    last_meal_at = data["last_meal_at"]
    meal_extent = data["meal_extent"]
    if last_meal_at is not None:
        last_meal_at_s = require_canonical_timestamp(last_meal_at, field="last_meal_at")
        meal_extent_s = _require_nonempty_str(meal_extent, "meal_extent")
        if meal_extent_s not in _MEAL_EXTENTS:
            _fail(f"invalid meal_extent: {meal_extent_s}")
        if now_c is not None and last_meal_at_s > now_c:
            _fail("last_meal_at after now")
    else:
        if meal_extent is not None:
            _fail("meal_extent must be null when last_meal_at is null")
        last_meal_at_s = None
        meal_extent_s = None

    free_window_key = data["free_window_key"]
    free_window_min = data["free_window_min"]
    if free_window_key is None and free_window_min is None:
        free_key_s = None
        free_min_i = None
    elif free_window_key is None or free_window_min is None:
        _fail("free_window_key and free_window_min must both be set or both null")
    else:
        free_key_s = _require_nonempty_str(free_window_key, "free_window_key")
        free_min_i = _require_true_int(free_window_min, "free_window_min")
        if free_min_i < 0:
            _fail("free_window_min must be >= 0")

    available = _require_true_int(data["available_window_min"], "available_window_min")
    if available < 0:
        _fail("available_window_min must be >= 0")

    return RuntimeDecisionFacts(
        available_window_min=available,
        route_profiles=route_profiles_t,
        structural_physical_feasible_by_action=_require_bool_map(
            data["structural_physical_feasible_by_action"],
            "structural_physical_feasible_by_action",
        ),
        household_tasks=household_tasks,
        kickboxing_sessions=kickboxing_sessions,
        routine_physical_feasible_by_action=_require_bool_map(
            data["routine_physical_feasible_by_action"],
            "routine_physical_feasible_by_action",
        ),
        kickboxing_location_feasible=_require_bool(
            data["kickboxing_location_feasible"], "kickboxing_location_feasible"
        ),
        kickboxing_self_generated_today=_require_true_int(
            data["kickboxing_self_generated_today"], "kickboxing_self_generated_today"
        ),
        kickboxing_equivalent_pending_plans=_require_true_int(
            data["kickboxing_equivalent_pending_plans"],
            "kickboxing_equivalent_pending_plans",
        ),
        continuation_feasible=_require_optional_bool(
            data["continuation_feasible"], "continuation_feasible"
        ),
        meal_feasible=_require_bool(data["meal_feasible"], "meal_feasible"),
        rest_feasible=_require_bool(data["rest_feasible"], "rest_feasible"),
        sleep_pressure=sleep_pressure,
        sleep_opportunity=sleep_opp_s,
        last_meal_at=last_meal_at_s,
        meal_extent=meal_extent_s,
        soft_guards=soft_guards,
        free_window_key=free_key_s,
        free_window_min=free_min_i,
        feasible_leisure_categories=leisure,
    )


def derive_managed_v2_decision_wakeup_event_id(
    *,
    character_id: str,
    decision_key: str,
    due_at: str,
) -> str:
    """Exact managed-v2 DECISION_WAKEUP event_id (mirrors Slice 2C builder).

    Identical formula to ``build_v2_decision_wakeup_event``:
    ``stable_id("v2-decision-wakeup", character_id, decision_key, due_at)``.

    Kept here (not in wakeups.py) so older Slice file-pin goldens remain intact
    while runtime occurrence binding reuses the same identity contract.
    """
    return stable_id(
        "v2-decision-wakeup",
        _require_nonempty_str(character_id, "character_id"),
        _require_nonempty_str(decision_key, "decision_key"),
        require_canonical_timestamp(due_at, field="due_at"),
    )


def runtime_trigger_from_v2_wakeup(
    current_state: Mapping[str, Any],
    queued_event: Mapping[str, Any],
) -> RuntimeDecisionTrigger:
    """Build RuntimeDecisionTrigger from a due v2 DECISION_WAKEUP already in queue.

    Does not pop the queue. Requires the exact managed-v2 event_id identity from
    Slice 2C (``derive_v2_decision_wakeup_event_id``).
    """
    if not isinstance(current_state, Mapping):
        _fail("current_state must be a mapping")
    if not isinstance(queued_event, Mapping):
        _fail("queued_event must be a mapping")

    state = validate_checkpoint(deepcopy(dict(current_state)))
    event = dict(queued_event)
    reject_binary_floats(event)
    validate_instance(event, "queued_internal_event")

    if int(event.get("schema_version", 0)) != 2:
        _fail("runtime_trigger_from_v2_wakeup requires schema_version == 2")
    if event.get("event_kind") != "DECISION_WAKEUP":
        _fail("runtime_trigger_from_v2_wakeup requires event_kind == DECISION_WAKEUP")

    event_id = _require_nonempty_str(event.get("event_id"), "event_id")
    due_at = require_canonical_timestamp(event.get("due_at"), field="due_at")
    processed = require_canonical_timestamp(
        state["processed_through"], field="processed_through"
    )
    if due_at != processed:
        _fail("queued DECISION_WAKEUP due_at must equal CurrentState.processed_through")

    matches = [
        e for e in state["pending_queue"]["events"] if e.get("event_id") == event_id
    ]
    if len(matches) != 1:
        _fail(
            f"queued event_id must occur exactly once in pending_queue: "
            f"event_id={event_id!r} count={len(matches)}"
        )
    queued_row = matches[0]
    if canonical_json(queued_row) != canonical_json(event):
        _fail("supplied queued_event must be semantically equal to the queued row")

    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        _fail("DECISION_WAKEUP payload must be a mapping")

    semantic_decision_key = _require_nonempty_str(
        payload.get("decision_key"), "payload.decision_key"
    )
    expected_event_id = derive_managed_v2_decision_wakeup_event_id(
        character_id=state["character_id"],
        decision_key=semantic_decision_key,
        due_at=due_at,
    )
    if event_id != expected_event_id:
        _fail(
            "queued DECISION_WAKEUP event_id must equal managed v2 identity: "
            f"got={event_id!r} expected={expected_event_id!r}"
        )

    return parse_runtime_decision_trigger(
        {
            "trigger_id": event_id,
            "occurred_at": due_at,
            "trigger_kind": payload.get("trigger_kind"),
            "semantic_decision_key": semantic_decision_key,
            "reason_code": payload.get("reason_code"),
            "metric": payload.get("metric"),
            "target_band": payload.get("target_band"),
            "direction": payload.get("direction"),
            "source_kind": payload.get("source_kind"),
            "source_ref": payload.get("source_ref"),
        }
    )


def bind_runtime_trigger_to_queued_wakeup(
    current_state: Mapping[str, Any],
    trigger: RuntimeDecisionTrigger | Mapping[str, Any],
) -> RuntimeDecisionTrigger:
    """Bind a supplied trigger to the authoritative queued v2 DECISION_WAKEUP row.

    Locates the exact pending_queue row by trigger_id, reconstructs via
    ``runtime_trigger_from_v2_wakeup``, and requires semantic equality with the
    supplied trigger. Does not pop the queue.
    """
    if not isinstance(current_state, Mapping):
        _fail("current_state must be a mapping")
    state = validate_checkpoint(deepcopy(dict(current_state)))
    supplied = parse_runtime_decision_trigger(trigger)
    matches = [
        e
        for e in state["pending_queue"]["events"]
        if e.get("event_id") == supplied.trigger_id
    ]
    if len(matches) != 1:
        _fail(
            "RuntimeDecisionTrigger.trigger_id must identify exactly one queued "
            f"DECISION_WAKEUP: trigger_id={supplied.trigger_id!r} count={len(matches)}"
        )
    reconstructed = runtime_trigger_from_v2_wakeup(state, matches[0])
    if canonical_json(reconstructed.as_dict()) != canonical_json(supplied.as_dict()):
        _fail(
            "supplied RuntimeDecisionTrigger must equal trigger reconstructed "
            "from the authoritative queued v2 DECISION_WAKEUP"
        )
    return reconstructed


def derive_runtime_decision_key(*, character_id: str, trigger_id: str) -> str:
    """Occurrence-unique decision identity for Slice 2D resolver / candidates."""
    return stable_id(
        "runtime-decision",
        _require_nonempty_str(character_id, "character_id"),
        _require_nonempty_str(trigger_id, "trigger_id"),
    )


def decision_boundary_from_runtime_trigger(
    trigger: RuntimeDecisionTrigger | Mapping[str, Any],
    *,
    runtime_decision_key: str,
) -> DecisionBoundaryFact:
    """Bridge RuntimeDecisionTrigger → Slice 2D DecisionBoundaryFact.

    Uses occurrence-unique runtime_decision_key (not semantic scheduler key).
    Direction remains on the trigger only.
    """
    trig = parse_runtime_decision_trigger(trigger)
    return parse_decision_boundary_fact(
        {
            "trigger_kind": trig.trigger_kind,
            "decision_key": _require_nonempty_str(
                runtime_decision_key, "runtime_decision_key"
            ),
            "reason_code": trig.reason_code,
            "metric": trig.metric,
            "target_band": trig.target_band,
            "source_ref": trig.source_ref,
        }
    )


def merge_resolved_opportunities(
    *groups: Sequence[ResolvedOpportunity | Mapping[str, Any]],
) -> tuple[ResolvedOpportunity, ...]:
    """Strict merge: duplicate opportunity_key fails closed; sort by opportunity_key."""
    items: list[ResolvedOpportunity] = []
    for group in groups:
        if isinstance(group, (str, bytes)) or not isinstance(group, (list, tuple)):
            _fail("opportunity group must be a list/tuple")
        for opp in group:
            items.append(parse_resolved_opportunity(opp))
    keys = [o.opportunity_key for o in items]
    if len(keys) != len(set(keys)):
        dups = sorted({k for k in keys if keys.count(k) > 1})
        _fail(f"ambiguous duplicate opportunity_key: {dups}")
    return tuple(sorted(items, key=lambda o: o.opportunity_key))


def merge_known_decision_boundaries(
    *groups: Sequence[KnownDecisionBoundary | Mapping[str, Any]],
    now: str,
) -> tuple[KnownDecisionBoundary, ...]:
    """Strict merge at current now; duplicate semantic decision_key fails closed."""
    now_c = require_canonical_timestamp(now, field="now")
    items: list[KnownDecisionBoundary] = []
    for group in groups:
        if isinstance(group, (str, bytes)) or not isinstance(group, (list, tuple)):
            _fail("boundary group must be a list/tuple")
        for boundary in group:
            items.append(validate_known_decision_boundary(boundary, now=now_c))
    by_key: dict[str, KnownDecisionBoundary] = {}
    for boundary in items:
        if boundary.decision_key in by_key:
            _fail(
                "duplicate decision_key in merged boundaries: "
                f"{boundary.decision_key}"
            )
        by_key[boundary.decision_key] = boundary
    ordered = sorted(
        by_key.values(),
        key=lambda b: (b.due_at, b.trigger_kind, b.decision_key),
    )
    return tuple(ordered)


def _facts_as_normalized_dict(facts: RuntimeDecisionFacts) -> dict[str, Any]:
    """Order-independent normalized view of RuntimeDecisionFacts for hashing."""

    def _route_dict(route: Mapping[str, Any]) -> dict[str, Any]:
        return dict(sorted((k, route[k]) for k in route))

    def _household_dict(task: HouseholdTaskFact | Mapping[str, Any]) -> dict[str, Any]:
        parsed = parse_household_task_fact(task)
        return {
            "household_task_id": parsed.household_task_id,
            "created_at": parsed.created_at,
            "status": parsed.status,
            "location_id": parsed.location_id,
        }

    def _session_dict(
        session: KickboxingSessionFact | Mapping[str, Any],
    ) -> dict[str, Any]:
        if isinstance(session, KickboxingSessionFact):
            return {
                "session_id": session.session_id,
                "actual_start": session.actual_start,
                "actual_end": session.actual_end,
                "finalized_at": session.finalized_at,
            }
        return dict(session)

    def _guard_dict(guard: SoftDecisionGuard | Mapping[str, Any]) -> dict[str, Any]:
        parsed = parse_soft_decision_guard(guard)
        return {
            "candidate_key": parsed.candidate_key,
            "decided_at": parsed.decided_at,
            "prior_outcome": parsed.prior_outcome,
            "reason_class": parsed.reason_class,
            "meaningful_input_changed": parsed.meaningful_input_changed,
        }

    return {
        "available_window_min": facts.available_window_min,
        "route_profiles": sorted(
            (_route_dict(r) for r in facts.route_profiles),
            key=lambda r: r.get("route_profile_id", canonical_json(r)),
        ),
        "structural_physical_feasible_by_action": dict(
            sorted(facts.structural_physical_feasible_by_action.items())
        ),
        "household_tasks": sorted(
            (_household_dict(t) for t in facts.household_tasks),
            key=lambda t: t["household_task_id"],
        ),
        "kickboxing_sessions": sorted(
            (_session_dict(s) for s in facts.kickboxing_sessions),
            key=lambda s: s["session_id"],
        ),
        "routine_physical_feasible_by_action": dict(
            sorted(facts.routine_physical_feasible_by_action.items())
        ),
        "kickboxing_location_feasible": facts.kickboxing_location_feasible,
        "kickboxing_self_generated_today": facts.kickboxing_self_generated_today,
        "kickboxing_equivalent_pending_plans": facts.kickboxing_equivalent_pending_plans,
        "continuation_feasible": facts.continuation_feasible,
        "meal_feasible": facts.meal_feasible,
        "rest_feasible": facts.rest_feasible,
        "sleep_pressure": facts.sleep_pressure,
        "sleep_opportunity": facts.sleep_opportunity,
        "last_meal_at": facts.last_meal_at,
        "meal_extent": facts.meal_extent,
        "soft_guards": sorted(
            (_guard_dict(g) for g in facts.soft_guards),
            key=lambda g: (g["candidate_key"], g["decided_at"]),
        ),
        "free_window_key": facts.free_window_key,
        "free_window_min": facts.free_window_min,
        "feasible_leisure_categories": sorted(facts.feasible_leisure_categories),
    }


def build_decision_facts_hash(facts: RuntimeDecisionFacts) -> str:
    """Deterministic hash of normalized RuntimeDecisionFacts (subset of snapshot)."""
    return canonical_hash(_facts_as_normalized_dict(facts))


def build_materialization_context_hash(
    *,
    current_state: Mapping[str, Any],
    schedule_state: Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    decision_facts_hash: str,
) -> str:
    """Exact bind of C1-relevant inputs to a resolved RuntimeDecisionFrame.

    Covers character/time/location/active identity, ScheduleState revision/hash,
    Behavior Policy version + canonical policy hash, and decision_facts_hash.
    Does not expand to world-domain hashes C1 does not read.
    """
    if not isinstance(current_state, Mapping):
        _fail("current_state must be a mapping")
    if not isinstance(schedule_state, Mapping):
        _fail("schedule_state must be a mapping")
    if not isinstance(behavior_policy, Mapping):
        _fail("behavior_policy must be a mapping")
    facts_hash = _require_nonempty_str(decision_facts_hash, "decision_facts_hash")
    if len(facts_hash) != 64 or any(c not in "0123456789abcdef" for c in facts_hash):
        _fail("decision_facts_hash must be lowercase canonical hex [0-9a-f]{64}")

    location_id = current_state.get("context", {}).get("location_id")
    if not isinstance(location_id, str) or not location_id:
        _fail("current_state.context.location_id required for materialization bind")
    active_raw = None
    ctx = current_state.get("context")
    if isinstance(ctx, Mapping):
        active_raw = ctx.get("active_activity")

    policy = require_v2_production_format(behavior_policy)
    schedule_revision = _require_nonempty_str(
        schedule_state.get("revision"), "schedule_state.revision"
    )
    schedule_hash = _require_nonempty_str(
        schedule_state.get("state_hash"), "schedule_state.state_hash"
    )
    normalized: dict[str, Any] = {
        "character_id": _require_nonempty_str(
            current_state.get("character_id"), "character_id"
        ),
        "processed_through": require_canonical_timestamp(
            current_state.get("processed_through"), field="processed_through"
        ),
        "state_revision": _require_true_int(
            current_state.get("state_revision"), "state_revision"
        ),
        "location_id": location_id,
        "active_activity": _active_snapshot(
            active_raw if isinstance(active_raw, Mapping) else None
        ),
        "schedule_revision": schedule_revision,
        "schedule_state_hash": schedule_hash,
        "behavior_policy_version": policy["behavior_policy_version"],
        "behavior_policy_hash": hash_policy(dict(behavior_policy)),
        "decision_facts_hash": facts_hash,
    }
    return canonical_hash(normalized)


def build_finalization_context_hash(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    decision_facts_hash: str,
) -> str:
    """Exact bind of C2B finish-relevant inputs to a resolved RuntimeDecisionFrame.

    Binds canonical full validated CurrentState (including pending_queue and full
    active_activity), ScheduleState revision/hash, all RuntimeBundle domain
    revisions/hashes, Behavior Policy version + canonical policy hash, and
    decision_facts_hash. Broader than C1 materialization_context_hash.
    """
    facts_hash = _require_nonempty_str(decision_facts_hash, "decision_facts_hash")
    if len(facts_hash) != 64 or any(c not in "0123456789abcdef" for c in facts_hash):
        _fail("decision_facts_hash must be lowercase canonical hex [0-9a-f]{64}")
    if not isinstance(behavior_policy, Mapping):
        _fail("behavior_policy must be a mapping")

    validated = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current = validated.current_state
    schedule = validated.schedule_state
    policy = require_v2_production_format(behavior_policy)

    def _domain_pair(domain: Mapping[str, Any], *, label: str) -> dict[str, str]:
        return {
            "revision": _require_nonempty_str(domain.get("revision"), f"{label}.revision"),
            "state_hash": _require_nonempty_str(
                domain.get("state_hash"), f"{label}.state_hash"
            ),
        }

    normalized: dict[str, Any] = {
        "current_state_hash": persisted_hash(dict(current)),
        "schedule_revision": _require_nonempty_str(
            schedule.get("revision"), "schedule_state.revision"
        ),
        "schedule_state_hash": _require_nonempty_str(
            schedule.get("state_hash"), "schedule_state.state_hash"
        ),
        "relation_state": _domain_pair(validated.relation_state, label="relation_state"),
        "home_state": _domain_pair(validated.home_state, label="home_state"),
        "consumables_state": _domain_pair(
            validated.consumables_state, label="consumables_state"
        ),
        "wardrobe_state": _domain_pair(validated.wardrobe_state, label="wardrobe_state"),
        "finance_state": _domain_pair(validated.finance_state, label="finance_state"),
        "behavior_policy_version": policy["behavior_policy_version"],
        "behavior_policy_hash": hash_policy(dict(behavior_policy)),
        "decision_facts_hash": facts_hash,
    }
    return canonical_hash(normalized)


def _opportunity_dict(opp: ResolvedOpportunity) -> dict[str, Any]:
    return {
        "opportunity_key": opp.opportunity_key,
        "opportunity_class": opp.opportunity_class,
        "action_kind": opp.action_kind,
        "source_kind": opp.source_kind,
        "source_ref": opp.source_ref,
        "soft_candidate": opp.soft_candidate,
        "time_feasible": opp.time_feasible,
        "location_feasible": opp.location_feasible,
        "physical_feasible": opp.physical_feasible,
        "domain_guard_satisfied": opp.domain_guard_satisfied,
        "local_preference_permille": opp.local_preference_permille,
        "rule_ids": list(opp.rule_ids),
    }


def _boundary_dict(boundary: KnownDecisionBoundary) -> dict[str, Any]:
    return {
        "due_at": boundary.due_at,
        "trigger_kind": boundary.trigger_kind,
        "decision_key": boundary.decision_key,
        "reason_code": boundary.reason_code,
        "source_kind": boundary.source_kind,
        "source_ref": boundary.source_ref,
    }


def _active_snapshot(active: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if active is None:
        return None
    out: dict[str, Any] = {
        "activity_instance_id": active.get("activity_instance_id"),
        "activity_type": active.get("activity_type"),
        "actual_start": active.get("actual_start"),
    }
    ctx = active.get("runtime_context")
    if isinstance(ctx, Mapping):
        out["runtime_context"] = dict(sorted((k, ctx[k]) for k in ctx))
    return out


def build_decision_snapshot_hash(
    *,
    trigger: RuntimeDecisionTrigger,
    runtime_decision_key: str,
    current_state: Mapping[str, Any],
    schedule_state: Mapping[str, Any],
    domain_revisions: Mapping[str, str],
    behavior_policy: Mapping[str, Any],
    facts: RuntimeDecisionFacts,
    merged_opportunities: Sequence[ResolvedOpportunity],
    merged_boundaries: Sequence[KnownDecisionBoundary],
    snapshot_extensions: Mapping[str, Any] | None = None,
) -> str:
    """Deterministic hash over normalized semantic decision inputs (not outputs)."""
    world_seed = current_state["world_seed"]
    normalized: dict[str, Any] = {
        "trigger": trigger.as_dict(),
        "runtime_decision_key": runtime_decision_key,
        "character_id": current_state["character_id"],
        "processed_through": current_state["processed_through"],
        "state_revision": current_state["state_revision"],
        "human_state": dict(sorted(current_state["human_state"].items())),
        "current_location_id": current_state["context"]["location_id"],
        "active_activity": _active_snapshot(current_state["context"].get("active_activity")),
        "schedule_revision": schedule_state["revision"],
        "schedule_state_hash": schedule_state["state_hash"],
        "domain_revisions": dict(sorted(domain_revisions.items())),
        "world_seed_hash": canonical_hash(world_seed),
        "behavior_policy_version": behavior_policy["behavior_policy_version"],
        "behavior_policy_hash": hash_policy(dict(behavior_policy)),
        "facts": _facts_as_normalized_dict(facts),
        "merged_opportunities": [_opportunity_dict(o) for o in merged_opportunities],
        "merged_known_boundaries": [_boundary_dict(b) for b in merged_boundaries],
    }
    if snapshot_extensions is not None:
        if not isinstance(snapshot_extensions, Mapping):
            _fail("snapshot_extensions must be a mapping")
        # Bind caller-normalized extras without hashing diagnostics/prose.
        normalized["snapshot_extensions"] = _mapping_snapshot(dict(snapshot_extensions))
    return canonical_hash(normalized)


def build_decision_input_state_refs(
    *,
    trigger_id: str,
    decision_snapshot_hash: str,
) -> tuple[str, ...]:
    refs = (
        f"trigger:{_require_nonempty_str(trigger_id, 'trigger_id')}",
        f"decision-snapshot:{_require_nonempty_str(decision_snapshot_hash, 'decision_snapshot_hash')}",
    )
    unique = tuple(sorted(set(refs)))
    if len(unique) != 2:
        _fail("decision input_state_refs must be exactly two unique refs")
    return unique


def _recover_selected_candidate(
    *,
    resolution: DecisionResolution,
    raw_candidates: Sequence[ActionCandidate],
) -> ActionCandidate | None:
    raw_ids = tuple(sorted(c.candidate_id for c in raw_candidates))
    if raw_ids != resolution.considered_candidate_ids:
        _fail(
            "raw candidate IDs must equal resolution.considered_candidate_ids: "
            f"raw={raw_ids!r} considered={resolution.considered_candidate_ids!r}"
        )

    if resolution.result_kind == "NO_ELIGIBLE_ACTION":
        if resolution.selected_candidate_id is not None:
            _fail("NO_ELIGIBLE_ACTION must have null selected_candidate_id")
        return None

    if resolution.selected_candidate_id is None:
        _fail(f"{resolution.result_kind} requires selected_candidate_id")

    matches = [
        c for c in raw_candidates if c.candidate_id == resolution.selected_candidate_id
    ]
    if len(matches) != 1:
        _fail(
            "selected_candidate_id must match exactly one raw candidate: "
            f"id={resolution.selected_candidate_id!r} count={len(matches)}"
        )
    selected = matches[0]

    if resolution.result_kind == "CONTINUE_CURRENT":
        if selected.action_kind != "CONTINUE_CURRENT":
            _fail("CONTINUE_CURRENT selected candidate action_kind mismatch")
    elif resolution.result_kind == "SELECT_CANDIDATE":
        if selected.action_kind == "CONTINUE_CURRENT":
            _fail("SELECT_CANDIDATE must not recover CONTINUE_CURRENT candidate")
    else:
        _fail(f"unknown resolution result_kind: {resolution.result_kind!r}")

    if selected.candidate_key != resolution.selected_candidate_key:
        _fail("selected candidate_key mismatch vs resolution")
    if selected.action_kind != resolution.selected_action_kind:
        _fail("selected action_kind mismatch vs resolution")
    if selected.priority_tier != resolution.selected_priority_tier:
        _fail("selected priority_tier mismatch vs resolution")
    if selected.activity_instance_id != resolution.activity_instance_id:
        _fail("selected activity_instance_id mismatch vs resolution")
    return selected


def build_runtime_decision_frame(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    trigger: RuntimeDecisionTrigger | Mapping[str, Any],
    facts: RuntimeDecisionFacts | Mapping[str, Any],
) -> RuntimeDecisionFrame:
    """Compose one pure RuntimeDecisionFrame. No RuntimeBundle / queue mutation."""
    return build_runtime_decision_frame_with_extras(
        bundle=bundle,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        trigger=trigger,
        facts=facts,
        extra_opportunity_groups=(),
        snapshot_extensions=None,
    )


def build_runtime_decision_frame_with_extras(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    trigger: RuntimeDecisionTrigger | Mapping[str, Any],
    facts: RuntimeDecisionFacts | Mapping[str, Any],
    extra_opportunity_groups: Sequence[
        Sequence[ResolvedOpportunity | Mapping[str, Any]]
    ] = (),
    snapshot_extensions: Mapping[str, Any] | None = None,
) -> RuntimeDecisionFrame:
    """Shared frame builder. Optional extra opportunity groups merge before resolve.

    Does not import domain-specific generators. Extra groups are caller-supplied
    ResolvedOpportunity sequences (e.g. immediate social candidates from 5B2B).
    """
    # Snapshot caller inputs for non-mutation guarantee.
    policy_before = canonical_json(dict(behavior_policy))
    trigger_before = (
        None
        if isinstance(trigger, RuntimeDecisionTrigger)
        else canonical_json(dict(trigger))
    )
    facts_before = (
        None
        if isinstance(facts, RuntimeDecisionFacts)
        else canonical_json(_mapping_snapshot(dict(facts)))
    )

    validated = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current = validated.current_state
    schedule = validated.schedule_state
    character_id = current["character_id"]
    processed_through = require_canonical_timestamp(
        current["processed_through"], field="processed_through"
    )
    queue_before = canonical_json(current["pending_queue"])
    state_revision_before = current["state_revision"]
    schedule_before = canonical_json(schedule)

    policy = require_v2_production_format(behavior_policy)
    if policy["behavior_policy_version"] != current["behavior_policy_version"]:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current['behavior_policy_version']!r}",
        )

    trig = bind_runtime_trigger_to_queued_wakeup(current, trigger)
    if trig.occurred_at != processed_through:
        _fail("trigger.occurred_at must equal CurrentState.processed_through")

    parsed_facts = parse_runtime_decision_facts(facts, now=processed_through)

    active_raw = current["context"].get("active_activity")
    if active_raw is None:
        if parsed_facts.continuation_feasible is not None:
            _fail("continuation_feasible must be null when no active_activity")
        active_ctx = None
    else:
        if parsed_facts.continuation_feasible is None:
            _fail("continuation_feasible must be a bool when active_activity exists")
        active_ctx = build_active_decision_context(
            active_activity=active_raw,
            continuation_feasible=parsed_facts.continuation_feasible,
        )

    runtime_decision_key = derive_runtime_decision_key(
        character_id=character_id,
        trigger_id=trig.trigger_id,
    )
    boundary = decision_boundary_from_runtime_trigger(
        trig, runtime_decision_key=runtime_decision_key
    )

    fatigue_band = classify_physical_fatigue_band(
        _require_true_int(
            current["human_state"]["physical_fatigue"], "physical_fatigue"
        ),
        policy,
    )

    structural = adapt_structural_domains(
        context=DomainAdapterContext(
            now=processed_through,
            current_location_id=current["context"]["location_id"],
            available_window_min=parsed_facts.available_window_min,
            physical_feasible_by_action=dict(
                parsed_facts.structural_physical_feasible_by_action
            ),
        ),
        commitments=list(schedule.get("commitments") or ()),
        obligation_tasks=list(schedule.get("obligation_tasks") or ()),
        route_profiles=[dict(r) for r in parsed_facts.route_profiles],
    )

    routine = adapt_routine_domains(
        context=RoutineAdapterContext(
            now=processed_through,
            current_location_id=current["context"]["location_id"],
            available_window_min=parsed_facts.available_window_min,
            physical_feasible_by_action=dict(
                parsed_facts.routine_physical_feasible_by_action
            ),
            kickboxing_location_feasible=parsed_facts.kickboxing_location_feasible,
            kickboxing_self_generated_today=parsed_facts.kickboxing_self_generated_today,
            kickboxing_equivalent_pending_plans=(
                parsed_facts.kickboxing_equivalent_pending_plans
            ),
            physical_fatigue_band=fatigue_band,
        ),
        behavior_policy=policy,
        household_tasks=list(parsed_facts.household_tasks),
        kickboxing_sessions=list(parsed_facts.kickboxing_sessions),
    )

    if isinstance(extra_opportunity_groups, (str, bytes)) or not isinstance(
        extra_opportunity_groups, (list, tuple)
    ):
        _fail("extra_opportunity_groups must be a list/tuple of groups")

    merge_groups: list[Sequence[ResolvedOpportunity | Mapping[str, Any]]] = [
        structural.opportunities,
        routine.opportunities,
    ]
    for group in extra_opportunity_groups:
        merge_groups.append(group)

    merged_opps = merge_resolved_opportunities(*merge_groups)
    merged_bounds = merge_known_decision_boundaries(
        structural.decision_boundaries,
        routine.decision_boundaries,
        now=processed_through,
    )

    for opp in merged_opps:
        opportunity_to_candidate(
            character_id=character_id,
            decision_key=runtime_decision_key,
            opportunity=opp,
        )

    resolve_kwargs: dict[str, Any] = {
        "world_seed": current["world_seed"],
        "character_id": character_id,
        "policy": policy,
        "boundary": boundary,
        "now": processed_through,
        "human_state": dict(current["human_state"]),
        "active": active_ctx,
        "opportunities": merged_opps,
        "soft_guards": list(parsed_facts.soft_guards),
        "meal_feasible": parsed_facts.meal_feasible,
        "rest_feasible": parsed_facts.rest_feasible,
        "sleep_pressure": parsed_facts.sleep_pressure,
        "last_meal_at": parsed_facts.last_meal_at,
        "meal_extent": parsed_facts.meal_extent,
        "free_window_key": parsed_facts.free_window_key,
        "free_window_min": parsed_facts.free_window_min,
        "feasible_leisure_categories": list(parsed_facts.feasible_leisure_categories),
    }
    if parsed_facts.sleep_opportunity is not None:
        resolve_kwargs["sleep_opportunity"] = parsed_facts.sleep_opportunity

    # Raw recovery uses identical normalized inputs; selection owned only by resolve.
    collect_kwargs = {
        "character_id": character_id,
        "decision_key": runtime_decision_key,
        "policy": policy,
        "human_state": resolve_kwargs["human_state"],
        "active": active_ctx,
        "opportunities": merged_opps,
        "meal_feasible": parsed_facts.meal_feasible,
        "rest_feasible": parsed_facts.rest_feasible,
        "sleep_pressure": parsed_facts.sleep_pressure,
        "last_meal_at": parsed_facts.last_meal_at,
        "meal_extent": parsed_facts.meal_extent,
        "now": processed_through,
        "free_window_key": parsed_facts.free_window_key,
        "free_window_min": parsed_facts.free_window_min,
        "feasible_leisure_categories": list(parsed_facts.feasible_leisure_categories),
    }
    if parsed_facts.sleep_opportunity is not None:
        collect_kwargs["sleep_opportunity"] = parsed_facts.sleep_opportunity

    raw_candidates, _early = collect_action_candidates(**collect_kwargs)
    resolution = resolve_action_decision(**resolve_kwargs)
    selected = _recover_selected_candidate(
        resolution=resolution,
        raw_candidates=raw_candidates,
    )

    domain_revisions = {
        "relation_state_revision": validated.relation_state["revision"],
        "home_revision": validated.home_state["revision"],
        "consumables_revision": validated.consumables_state["revision"],
        "wardrobe_revision": validated.wardrobe_state["revision"],
        "finance_revision": validated.finance_state["revision"],
    }
    snapshot_hash = build_decision_snapshot_hash(
        trigger=trig,
        runtime_decision_key=runtime_decision_key,
        current_state=current,
        schedule_state=schedule,
        domain_revisions=domain_revisions,
        behavior_policy=policy,
        facts=parsed_facts,
        merged_opportunities=merged_opps,
        merged_boundaries=merged_bounds,
        snapshot_extensions=snapshot_extensions,
    )
    refs = build_decision_input_state_refs(
        trigger_id=trig.trigger_id,
        decision_snapshot_hash=snapshot_hash,
    )

    # Non-mutation proofs for persisted/transient caller inputs.
    if canonical_json(current["pending_queue"]) != queue_before:
        _fail("RuntimeDecisionFrame must not mutate pending_queue")
    if current["state_revision"] != state_revision_before:
        _fail("RuntimeDecisionFrame must not mutate state_revision")
    if canonical_json(schedule) != schedule_before:
        _fail("RuntimeDecisionFrame must not mutate ScheduleState")
    if canonical_json(dict(behavior_policy)) != policy_before:
        _fail("RuntimeDecisionFrame must not mutate behavior_policy")
    if trigger_before is not None and canonical_json(dict(trigger)) != trigger_before:
        _fail("RuntimeDecisionFrame must not mutate trigger mapping")
    if facts_before is not None and canonical_json(dict(facts)) != facts_before:
        _fail("RuntimeDecisionFrame must not mutate facts mapping")

    # Evidence must never embed the raw world seed.
    for ref in refs:
        if isinstance(current["world_seed"], str) and current["world_seed"] in ref:
            _fail("evidence refs must not contain raw world_seed")

    facts_hash = build_decision_facts_hash(parsed_facts)
    materialization_context_hash = build_materialization_context_hash(
        current_state=current,
        schedule_state=schedule,
        behavior_policy=behavior_policy,
        decision_facts_hash=facts_hash,
    )
    finalization_context_hash = build_finalization_context_hash(
        bundle=validated,
        reference_sets=reference_sets,
        behavior_policy=behavior_policy,
        decision_facts_hash=facts_hash,
    )

    return RuntimeDecisionFrame(
        runtime_decision_key=runtime_decision_key,
        trigger=trig,
        decision_snapshot_hash=snapshot_hash,
        decision_facts_hash=facts_hash,
        materialization_context_hash=materialization_context_hash,
        finalization_context_hash=finalization_context_hash,
        input_state_refs=refs,
        structural_result=structural,
        routine_result=routine,
        merged_opportunities=merged_opps,
        merged_known_boundaries=merged_bounds,
        raw_candidates=raw_candidates,
        resolution=resolution,
        selected_candidate=selected,
    )
