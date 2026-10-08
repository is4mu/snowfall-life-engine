"""Life Engine v2 Slice 2E — structural domain adapters (pure / synthetic).

Converts Commitment / ObligationTask / RouteProfile facts into Slice 2C
KnownDecisionBoundary and Slice 2D ResolvedOpportunity structures.

Does not call the Slice 2D resolver, Slice 2C queue reconciliation, or clock.
No file I/O, network, queue/disk write, runtime mutation, or global PRNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .contracts import (
    validate_commitment,
    validate_obligation_task,
    validate_route_profile,
)
from .decisions import ResolvedOpportunity, parse_resolved_opportunity
from .errors import ErrorCode, LifeEngineError
from .timeutil import (
    add_minutes,
    format_rfc3339,
    parse_rfc3339,
    require_canonical_timestamp,
)
from .wakeups import KnownDecisionBoundary, validate_known_decision_boundary

# ---------------------------------------------------------------------------
# Closed diagnostic codes
# ---------------------------------------------------------------------------

ADAPTER_DIAGNOSTIC_CODES = frozenset(
    {
        "NOT_YET_VISIBLE",
        "TERMINAL_STATUS",
        "ROUTE_NOT_REQUIRED",
        "ROUTE_NOT_FOUND",
        "ROUTE_DIRECTION_MISMATCH",
        "UNSUPPORTED_SOFT_COMMITMENT_KIND",
        "UNSUPPORTED_TASK_DOMAIN",
        "NOT_YET_ELIGIBLE",
        "INSUFFICIENT_WORK_WINDOW",
        "LOCATION_CONSTRAINT_BLOCKED",
        "HARD_COMMITMENT_CONFLICT",
    }
)

_TERMINAL_COMMITMENT_STATUSES = frozenset(
    {"CANCELLED", "COMPLETED", "MISSED", "DEFERRED"}
)

_SUPPORTED_SOFT_KINDS = frozenset({"WORK", "SOCIAL", "ERRAND"})
_UNSUPPORTED_SOFT_KINDS = frozenset({"CLASS", "EXERCISE", "APPOINTMENT", "SELF_TASK"})

_SUPPORTED_TASK_DOMAINS = frozenset({"ACADEMIC", "ERRAND"})
_UNSUPPORTED_TASK_DOMAINS = frozenset({"WORK", "PERSONAL", "ADMIN"})

_HARD_KIND_TO_ACTION: dict[str, str] = {
    "WORK": "WORK",
    "SOCIAL": "HARD_COMMITMENT_ACTIVITY",
    "ERRAND": "HARD_COMMITMENT_ACTIVITY",
    "CLASS": "HARD_COMMITMENT_ACTIVITY",
    "EXERCISE": "HARD_COMMITMENT_ACTIVITY",
    "APPOINTMENT": "HARD_COMMITMENT_ACTIVITY",
    "SELF_TASK": "HARD_COMMITMENT_ACTIVITY",
}

# SOFT kind → (action_kind, opportunity_class)
_SOFT_KIND_MAPPING: dict[str, tuple[str, str]] = {
    "WORK": ("WORK", "ROUTINE_HABIT"),
    "SOCIAL": ("SOCIAL_PROMISE", "SOCIAL_PROMISE"),
    "ERRAND": ("ERRAND", "ROUTINE_HABIT"),
}

_TASK_DOMAIN_TO_ACTION: dict[str, str] = {
    "ACADEMIC": "STUDY",
    "ERRAND": "ERRAND",
}


# ---------------------------------------------------------------------------
# Transient structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdapterDiagnostic:
    source_kind: str
    source_ref: str
    code: str
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "code": self.code,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class AdapterResult:
    opportunities: tuple[ResolvedOpportunity, ...]
    decision_boundaries: tuple[KnownDecisionBoundary, ...]
    diagnostics: tuple[AdapterDiagnostic, ...]


@dataclass(frozen=True)
class DomainAdapterContext:
    now: str
    current_location_id: str
    available_window_min: int
    physical_feasible_by_action: Mapping[str, bool]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_nonempty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be non-empty str")
    return value


def _require_int_ge0(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be int (no bool/float)")
    if value < 0:
        _fail(f"{label} must be >= 0")
    return value


def _canonical_sorted_strs(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def _parse_context(context: DomainAdapterContext | Mapping[str, Any]) -> DomainAdapterContext:
    if isinstance(context, DomainAdapterContext):
        data = {
            "now": context.now,
            "current_location_id": context.current_location_id,
            "available_window_min": context.available_window_min,
            "physical_feasible_by_action": dict(context.physical_feasible_by_action),
        }
    elif isinstance(context, Mapping):
        data = dict(context)
    else:
        _fail("DomainAdapterContext must be mapping or dataclass")

    allowed = {
        "now",
        "current_location_id",
        "available_window_min",
        "physical_feasible_by_action",
    }
    unknown = set(data) - allowed
    if unknown:
        _fail(f"DomainAdapterContext unknown fields: {sorted(unknown)}")
    missing = allowed - set(data)
    if missing:
        _fail(f"DomainAdapterContext missing fields: {sorted(missing)}")

    now = require_canonical_timestamp(data["now"], field="now")
    loc = _require_nonempty_str(data["current_location_id"], "current_location_id")
    window = _require_int_ge0(data["available_window_min"], "available_window_min")
    phys_raw = data["physical_feasible_by_action"]
    if not isinstance(phys_raw, Mapping):
        _fail("physical_feasible_by_action must be a mapping")
    phys: dict[str, bool] = {}
    for k, v in phys_raw.items():
        key = _require_nonempty_str(k, "physical_feasible_by_action key")
        if isinstance(v, bool):
            phys[key] = v
        else:
            _fail(f"physical_feasible_by_action[{key!r}] must be bool")
    return DomainAdapterContext(
        now=now,
        current_location_id=loc,
        available_window_min=window,
        physical_feasible_by_action=phys,
    )


def _make_diagnostic(
    *,
    source_kind: str,
    source_ref: str,
    code: str,
    rule_ids: Sequence[str] = (),
) -> AdapterDiagnostic:
    if code not in ADAPTER_DIAGNOSTIC_CODES:
        _fail(f"unknown AdapterDiagnostic code: {code}")
    return AdapterDiagnostic(
        source_kind=_require_nonempty_str(source_kind, "source_kind"),
        source_ref=_require_nonempty_str(source_ref, "source_ref"),
        code=code,
        rule_ids=_canonical_sorted_strs([_require_nonempty_str(r, "rule_id") for r in rule_ids]),
    )


def _physical_for(ctx: DomainAdapterContext, action_kind: str) -> bool:
    if action_kind not in ctx.physical_feasible_by_action:
        _fail(
            f"missing physical feasibility fact for action_kind={action_kind!r} "
            "(no implicit true)"
        )
    return bool(ctx.physical_feasible_by_action[action_kind])


def _subtract_minutes(ts: str, minutes: int, *, field: str) -> str:
    dt = parse_rfc3339(ts, field=field)
    return format_rfc3339(dt - timedelta(minutes=minutes))


def _add_minutes_ts(ts: str, minutes: int, *, field: str) -> str:
    dt = parse_rfc3339(ts, field=field)
    return format_rfc3339(add_minutes(dt, minutes))


def _dt(ts: str, *, field: str) -> datetime:
    return parse_rfc3339(ts, field=field)


def _unique_ids(ids: Sequence[str], *, label: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            _fail(f"ambiguous duplicate {label}: {i}")
        seen.add(i)


def _route_semantic_key(route: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        route["origin_location_id"],
        route["destination_location_id"],
        route["mode"],
        int(route["duration_min"]),
        int(route["duration_max"]),
        route["effort_class"],
    )


def _find_forward_route(
    *,
    routes: Sequence[Mapping[str, Any]],
    origin: str,
    destination: str,
    source_ref: str,
    diagnostics: list[AdapterDiagnostic],
) -> Mapping[str, Any] | None:
    forward = [
        r
        for r in routes
        if r["origin_location_id"] == origin and r["destination_location_id"] == destination
    ]
    if not forward:
        reverse = [
            r
            for r in routes
            if r["origin_location_id"] == destination and r["destination_location_id"] == origin
        ]
        if reverse:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="COMMITMENT",
                    source_ref=source_ref,
                    code="ROUTE_DIRECTION_MISMATCH",
                    rule_ids=("slice2e.route.direction",),
                )
            )
        else:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="COMMITMENT",
                    source_ref=source_ref,
                    code="ROUTE_NOT_FOUND",
                    rule_ids=("slice2e.route.missing",),
                )
            )
        return None

    # Multiple exact matches: fail closed unless semantically identical.
    keys = {_route_semantic_key(r) for r in forward}
    if len(keys) > 1:
        _fail(
            f"ambiguous non-identical forward routes for {origin}->{destination}: "
            f"{sorted(r['route_profile_id'] for r in forward)}"
        )
    # Deterministic pick among identical semantics.
    return sorted(forward, key=lambda r: r["route_profile_id"])[0]


# ---------------------------------------------------------------------------
# Commitment effective status (no future leakage)
# ---------------------------------------------------------------------------


def effective_commitment_status_at(
    commitment: Mapping[str, Any],
    *,
    now: str,
) -> str | None:
    """Rebuild effective status at `now` from status_history.

    Returns None when the commitment is not yet visible at `now`.
    """
    now_c = require_canonical_timestamp(now, field="now")
    now_dt = _dt(now_c, field="now")
    created = _dt(commitment["created_at"], field="created_at")
    if created > now_dt:
        return None
    visible: list[Mapping[str, Any]] = []
    for i, entry in enumerate(commitment["status_history"]):
        changed = _dt(entry["changed_at"], field=f"status_history[{i}].changed_at")
        if changed <= now_dt:
            visible.append(entry)
    if not visible:
        return None
    return str(visible[-1]["to"])


# ---------------------------------------------------------------------------
# Commitment mapping
# ---------------------------------------------------------------------------


def _commitment_tier_and_action(
    commitment: Mapping[str, Any],
    *,
    diagnostics: list[AdapterDiagnostic],
) -> tuple[str, str] | None:
    """Return (opportunity_class, action_kind) or None when unsupported/soft-blocked."""
    cid = commitment["commitment_id"]
    hardness = commitment["hardness"]
    kind = commitment["kind"]
    if hardness == "HARD":
        action = _HARD_KIND_TO_ACTION.get(kind)
        if action is None:
            _fail(f"unknown HARD commitment kind: {kind}")
        return ("HARD_COMMITMENT", action)
    if hardness == "SOFT":
        if kind in _UNSUPPORTED_SOFT_KINDS:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="COMMITMENT",
                    source_ref=cid,
                    code="UNSUPPORTED_SOFT_COMMITMENT_KIND",
                    rule_ids=("slice2e.commitment.soft_kind",),
                )
            )
            return None
        mapped = _SOFT_KIND_MAPPING.get(kind)
        if mapped is None:
            _fail(f"unknown SOFT commitment kind: {kind}")
        action, opp_class = mapped
        return (opp_class, action)
    _fail(f"unknown hardness: {hardness}")
    return None  # pragma: no cover


def _commitment_travel_needed(commitment: Mapping[str, Any], current_location_id: str) -> bool:
    loc = commitment["location_id"]
    if loc is None:
        return False
    return loc != current_location_id


def _latest_safe_departure(
    commitment: Mapping[str, Any],
    *,
    duration_max: int,
) -> str:
    timing = commitment["timing"]
    if timing["timing_kind"] == "EXACT":
        return _subtract_minutes(
            timing["planned_start"], duration_max, field="timing.planned_start"
        )
    return _subtract_minutes(timing["latest_start"], duration_max, field="timing.latest_start")


def _travel_arrival_feasible(
    commitment: Mapping[str, Any],
    *,
    now: str,
    duration_max: int,
) -> bool:
    arrival = _dt(_add_minutes_ts(now, duration_max, field="now"), field="arrival")
    timing = commitment["timing"]
    if timing["timing_kind"] == "EXACT":
        return arrival <= _dt(timing["planned_end"], field="timing.planned_end")
    return arrival <= _dt(timing["latest_start"], field="timing.latest_start")


def _activity_window_open(commitment: Mapping[str, Any], *, now: str) -> bool:
    now_dt = _dt(now, field="now")
    timing = commitment["timing"]
    if timing["timing_kind"] == "EXACT":
        start = _dt(timing["planned_start"], field="timing.planned_start")
        end = _dt(timing["planned_end"], field="timing.planned_end")
        return start <= now_dt < end
    earliest = _dt(timing["earliest_start"], field="timing.earliest_start")
    latest = _dt(timing["latest_start"], field="timing.latest_start")
    return earliest <= now_dt <= latest


def _activity_window_past(commitment: Mapping[str, Any], *, now: str) -> bool:
    now_dt = _dt(now, field="now")
    timing = commitment["timing"]
    if timing["timing_kind"] == "EXACT":
        return now_dt >= _dt(timing["planned_end"], field="timing.planned_end")
    return now_dt > _dt(timing["latest_start"], field="timing.latest_start")


def _make_opportunity(
    *,
    opportunity_key: str,
    opportunity_class: str,
    action_kind: str,
    source_kind: str,
    source_ref: str,
    soft_candidate: bool,
    time_feasible: bool,
    location_feasible: bool,
    physical_feasible: bool,
    domain_guard_satisfied: bool,
    rule_ids: Sequence[str],
) -> ResolvedOpportunity:
    return parse_resolved_opportunity(
        {
            "opportunity_key": opportunity_key,
            "opportunity_class": opportunity_class,
            "action_kind": action_kind,
            "source_kind": source_kind,
            "source_ref": source_ref,
            "soft_candidate": soft_candidate,
            "time_feasible": time_feasible,
            "location_feasible": location_feasible,
            "physical_feasible": physical_feasible,
            "domain_guard_satisfied": domain_guard_satisfied,
            "local_preference_permille": None,
            "rule_ids": list(rule_ids),
        }
    )


def _make_boundary(
    *,
    due_at: str,
    trigger_kind: str,
    decision_key: str,
    reason_code: str,
    source_kind: str | None,
    source_ref: str | None,
    now: str,
) -> KnownDecisionBoundary | None:
    due = require_canonical_timestamp(due_at, field="due_at")
    if _dt(due, field="due_at") <= _dt(now, field="now"):
        return None  # past / present: not queued as future boundary
    return validate_known_decision_boundary(
        {
            "due_at": due,
            "trigger_kind": trigger_kind,
            "decision_key": decision_key,
            "reason_code": reason_code,
            "source_kind": source_kind,
            "source_ref": source_ref,
        },
        now=now,
    )


def _adapt_one_commitment(
    commitment: Mapping[str, Any],
    *,
    ctx: DomainAdapterContext,
    routes: Sequence[Mapping[str, Any]],
    opportunities: list[ResolvedOpportunity],
    boundaries: list[KnownDecisionBoundary],
    diagnostics: list[AdapterDiagnostic],
    hard_active_ids: list[str],
) -> None:
    cid = commitment["commitment_id"]
    effective = effective_commitment_status_at(commitment, now=ctx.now)
    if effective is None:
        diagnostics.append(
            _make_diagnostic(
                source_kind="COMMITMENT",
                source_ref=cid,
                code="NOT_YET_VISIBLE",
                rule_ids=("slice2e.commitment.visibility",),
            )
        )
        return
    if effective in _TERMINAL_COMMITMENT_STATUSES:
        diagnostics.append(
            _make_diagnostic(
                source_kind="COMMITMENT",
                source_ref=cid,
                code="TERMINAL_STATUS",
                rule_ids=("slice2e.commitment.terminal",),
            )
        )
        return
    if effective != "PLANNED":
        _fail(f"unexpected effective commitment status: {effective}")

    mapped = _commitment_tier_and_action(commitment, diagnostics=diagnostics)
    # Still emit future PLANNED_WINDOW boundaries even when soft kind unsupported?
    # Contract: unsupported → no candidate; boundaries for schedule awareness still useful.
    timing = commitment["timing"]
    if timing["timing_kind"] == "EXACT":
        b = _make_boundary(
            due_at=timing["planned_start"],
            trigger_kind="PLANNED_WINDOW",
            decision_key=f"commitment:{cid}:PLANNED_WINDOW:{timing['planned_start']}",
            reason_code="COMMITMENT_PLANNED_START",
            source_kind="COMMITMENT",
            source_ref=cid,
            now=ctx.now,
        )
        if b is not None:
            boundaries.append(b)
    else:
        # Include semantic edge in decision_key so zero-width WINDOW
        # (earliest_start == latest_start) does not collide.
        for field_name, reason in (
            ("earliest_start", "COMMITMENT_EARLIEST_START"),
            ("latest_start", "COMMITMENT_LATEST_START"),
        ):
            due = timing[field_name]
            b = _make_boundary(
                due_at=due,
                trigger_kind="PLANNED_WINDOW",
                decision_key=f"commitment:{cid}:PLANNED_WINDOW:{field_name}:{due}",
                reason_code=reason,
                source_kind="COMMITMENT",
                source_ref=cid,
                now=ctx.now,
            )
            if b is not None:
                boundaries.append(b)

    travel_needed = _commitment_travel_needed(commitment, ctx.current_location_id)
    route: Mapping[str, Any] | None = None
    duration_max: int | None = None
    if not travel_needed:
        diagnostics.append(
            _make_diagnostic(
                source_kind="COMMITMENT",
                source_ref=cid,
                code="ROUTE_NOT_REQUIRED",
                rule_ids=("slice2e.route.not_required",),
            )
        )
    else:
        dest = commitment["location_id"]
        assert dest is not None
        route = _find_forward_route(
            routes=routes,
            origin=ctx.current_location_id,
            destination=str(dest),
            source_ref=cid,
            diagnostics=diagnostics,
        )
        if route is not None:
            duration_max = int(route["duration_max"])

    # HARD departure boundary when travel + route available.
    if (
        commitment["hardness"] == "HARD"
        and travel_needed
        and route is not None
        and duration_max is not None
    ):
        dep = _latest_safe_departure(commitment, duration_max=duration_max)
        b = _make_boundary(
            due_at=dep,
            trigger_kind="COMMITMENT_DEPARTURE",
            decision_key=f"commitment:{cid}:COMMITMENT_DEPARTURE:{dep}",
            reason_code="COMMITMENT_DEPARTURE",
            source_kind="COMMITMENT",
            source_ref=cid,
            now=ctx.now,
        )
        if b is not None:
            boundaries.append(b)

    if mapped is None:
        return
    if _activity_window_past(commitment, now=ctx.now):
        return

    opp_class, action_kind = mapped
    soft = commitment["hardness"] == "SOFT"
    emitted_for_hard = False

    # Travel opportunity (may precede activity window).
    # Emit when now >= latest_safe_departure and commitment not expired;
    # time_feasible is conservative arrival feasibility (late travel → false).
    if travel_needed and route is not None and duration_max is not None:
        dep = _latest_safe_departure(commitment, duration_max=duration_max)
        now_dt = _dt(ctx.now, field="now")
        dep_dt = _dt(dep, field="latest_safe_departure")
        if now_dt < dep_dt:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="COMMITMENT",
                    source_ref=cid,
                    code="NOT_YET_ELIGIBLE",
                    rule_ids=("slice2e.commitment.travel_early",),
                )
            )
        else:
            time_ok = _travel_arrival_feasible(
                commitment, now=ctx.now, duration_max=duration_max
            )
            opportunities.append(
                _make_opportunity(
                    opportunity_key=f"commitment:{cid}:travel",
                    opportunity_class=opp_class,
                    action_kind="TRAVEL_TO_COMMITMENT",
                    source_kind="COMMITMENT",
                    source_ref=cid,
                    soft_candidate=soft,
                    time_feasible=time_ok,
                    location_feasible=True,
                    physical_feasible=_physical_for(ctx, "TRAVEL_TO_COMMITMENT"),
                    domain_guard_satisfied=True,
                    rule_ids=("slice2e.commitment.travel",),
                )
            )
            emitted_for_hard = True

    # Activity opportunity only inside open window.
    if _activity_window_open(commitment, now=ctx.now):
        loc = commitment["location_id"]
        loc_ok = loc is None or loc == ctx.current_location_id
        opportunities.append(
            _make_opportunity(
                opportunity_key=f"commitment:{cid}:activity",
                opportunity_class=opp_class,
                action_kind=action_kind,
                source_kind="COMMITMENT",
                source_ref=cid,
                soft_candidate=soft,
                time_feasible=True,
                location_feasible=loc_ok,
                physical_feasible=_physical_for(ctx, action_kind),
                domain_guard_satisfied=True,
                rule_ids=("slice2e.commitment.activity",),
            )
        )
        emitted_for_hard = True

    if commitment["hardness"] == "HARD" and emitted_for_hard:
        hard_active_ids.append(cid)


# ---------------------------------------------------------------------------
# ObligationTask
# ---------------------------------------------------------------------------


def _task_deadline_at(task: Mapping[str, Any]) -> str | None:
    if task.get("due_at") is not None:
        return require_canonical_timestamp(task["due_at"], field="due_at")
    due_window = task.get("due_window")
    if due_window is not None:
        return require_canonical_timestamp(due_window["latest_due"], field="due_window.latest_due")
    return None


def _latest_required_work_start_at(task: Mapping[str, Any]) -> str | None:
    deadline = _task_deadline_at(task)
    if deadline is None:
        return None
    effort = int(task["effort_remaining_min"])
    return _subtract_minutes(deadline, effort, field="deadline_at")


def _task_tier(task: Mapping[str, Any], *, now: str) -> str:
    if task["priority"] == "URGENT":
        return "DEADLINE_TASK"
    latest_req = _latest_required_work_start_at(task)
    if latest_req is not None and _dt(now, field="now") >= _dt(latest_req, field="latest_required"):
        return "DEADLINE_TASK"
    return "ROUTINE_HABIT"


def _adapt_one_task(
    task: Mapping[str, Any],
    *,
    ctx: DomainAdapterContext,
    opportunities: list[ResolvedOpportunity],
    boundaries: list[KnownDecisionBoundary],
    diagnostics: list[AdapterDiagnostic],
) -> None:
    tid = task["task_id"]
    status = task["status"]
    domain = task["domain"]

    # Fail closed: task created in the future must not become actionable.
    # ObligationTask has no status_history; do not reconstruct past visibility.
    created_dt = _dt(task["created_at"], field="created_at")
    now_dt = _dt(ctx.now, field="now")
    if created_dt > now_dt:
        _fail(
            f"obligation_task {tid}: created_at after now "
            "(future-created task cannot be adapted)"
        )

    # Terminal tasks suppress both opportunities and future OBLIGATION boundaries.
    if status in {"DONE", "CANCELLED"}:
        return
    if status not in {"OPEN", "IN_PROGRESS"}:
        _fail(f"unexpected obligation_task status: {status}")

    # Future boundaries for open/in-progress tasks only.
    earliest = task.get("earliest_start")
    if earliest is not None:
        b = _make_boundary(
            due_at=earliest,
            trigger_kind="OBLIGATION",
            decision_key=f"task:{tid}:OBLIGATION:earliest_start:{earliest}",
            reason_code="TASK_EARLIEST_START",
            source_kind="TASK",
            source_ref=tid,
            now=ctx.now,
        )
        if b is not None:
            boundaries.append(b)

    latest_req = _latest_required_work_start_at(task)
    if latest_req is not None:
        b = _make_boundary(
            due_at=latest_req,
            trigger_kind="OBLIGATION",
            decision_key=f"task:{tid}:OBLIGATION:latest_required_work_start:{latest_req}",
            reason_code="TASK_LATEST_REQUIRED_WORK_START",
            source_kind="TASK",
            source_ref=tid,
            now=ctx.now,
        )
        if b is not None:
            boundaries.append(b)

    if task.get("due_at") is not None:
        due = require_canonical_timestamp(task["due_at"], field="due_at")
        b = _make_boundary(
            due_at=due,
            trigger_kind="OBLIGATION",
            decision_key=f"task:{tid}:OBLIGATION:due_at:{due}",
            reason_code="TASK_DUE_AT",
            source_kind="TASK",
            source_ref=tid,
            now=ctx.now,
        )
        if b is not None:
            boundaries.append(b)
    due_window = task.get("due_window")
    if due_window is not None:
        for field_name, reason in (
            ("earliest_due", "TASK_EARLIEST_DUE"),
            ("latest_due", "TASK_LATEST_DUE"),
        ):
            due = require_canonical_timestamp(due_window[field_name], field=f"due_window.{field_name}")
            b = _make_boundary(
                due_at=due,
                trigger_kind="OBLIGATION",
                decision_key=f"task:{tid}:OBLIGATION:{field_name}:{due}",
                reason_code=reason,
                source_kind="TASK",
                source_ref=tid,
                now=ctx.now,
            )
            if b is not None:
                boundaries.append(b)

    effort = int(task["effort_remaining_min"])
    if effort <= 0:
        return

    if earliest is not None and _dt(earliest, field="earliest_start") > _dt(ctx.now, field="now"):
        diagnostics.append(
            _make_diagnostic(
                source_kind="TASK",
                source_ref=tid,
                code="NOT_YET_ELIGIBLE",
                rule_ids=("slice2e.task.earliest",),
            )
        )
        return

    if domain in _UNSUPPORTED_TASK_DOMAINS:
        diagnostics.append(
            _make_diagnostic(
                source_kind="TASK",
                source_ref=tid,
                code="UNSUPPORTED_TASK_DOMAIN",
                rule_ids=("slice2e.task.domain",),
            )
        )
        return
    if domain not in _SUPPORTED_TASK_DOMAINS:
        _fail(f"unknown obligation_task domain: {domain}")

    action_kind = _TASK_DOMAIN_TO_ACTION[domain]
    opp_class = _task_tier(task, now=ctx.now)
    soft = opp_class != "DEADLINE_TASK"

    chunk = int(task["min_work_chunk_min"])
    time_ok = ctx.available_window_min >= chunk
    if not time_ok:
        diagnostics.append(
            _make_diagnostic(
                source_kind="TASK",
                source_ref=tid,
                code="INSUFFICIENT_WORK_WINDOW",
                rule_ids=("slice2e.task.window",),
            )
        )

    constraints = list(task["location_constraints"])
    if not constraints:
        loc_ok = True
    else:
        loc_ok = ctx.current_location_id in constraints
        if not loc_ok:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="TASK",
                    source_ref=tid,
                    code="LOCATION_CONSTRAINT_BLOCKED",
                    rule_ids=("slice2e.task.location",),
                )
            )

    domain_ok = status in {"OPEN", "IN_PROGRESS"} and effort > 0 and (
        earliest is None or _dt(earliest, field="earliest_start") <= _dt(ctx.now, field="now")
    )

    opportunities.append(
        _make_opportunity(
            opportunity_key=f"task:{tid}:work",
            opportunity_class=opp_class,
            action_kind=action_kind,
            source_kind="TASK",
            source_ref=tid,
            soft_candidate=soft,
            time_feasible=time_ok,
            location_feasible=loc_ok,
            physical_feasible=_physical_for(ctx, action_kind),
            domain_guard_satisfied=domain_ok,
            rule_ids=("slice2e.task.work",),
        )
    )


# ---------------------------------------------------------------------------
# Canonicalization / public entry
# ---------------------------------------------------------------------------


def _sort_opportunities(
    items: Sequence[ResolvedOpportunity],
) -> tuple[ResolvedOpportunity, ...]:
    keys = [o.opportunity_key for o in items]
    if len(keys) != len(set(keys)):
        _fail(f"ambiguous duplicate opportunity_key: {sorted(k for k in keys if keys.count(k) > 1)}")
    return tuple(sorted(items, key=lambda o: o.opportunity_key))


def _sort_boundaries(
    items: Sequence[KnownDecisionBoundary],
) -> tuple[KnownDecisionBoundary, ...]:
    # Deterministic dedupe of identical boundaries; conflict on same decision_key.
    by_key: dict[str, KnownDecisionBoundary] = {}
    for b in items:
        prev = by_key.get(b.decision_key)
        if prev is None:
            by_key[b.decision_key] = b
            continue
        if prev != b:
            _fail(f"ambiguous duplicate decision_key with differing boundary: {b.decision_key}")
    ordered = sorted(
        by_key.values(),
        key=lambda b: (b.due_at, b.trigger_kind, b.decision_key, b.reason_code),
    )
    return tuple(ordered)


def _sort_diagnostics(
    items: Sequence[AdapterDiagnostic],
) -> tuple[AdapterDiagnostic, ...]:
    return tuple(
        sorted(
            items,
            key=lambda d: (d.source_kind, d.source_ref, d.code, d.rule_ids),
        )
    )


def adapt_structural_domains(
    *,
    context: DomainAdapterContext | Mapping[str, Any],
    commitments: Sequence[Mapping[str, Any]] = (),
    obligation_tasks: Sequence[Mapping[str, Any]] = (),
    route_profiles: Sequence[Mapping[str, Any]] = (),
) -> AdapterResult:
    """Pure structural adaptation of schedule/task/route facts.

    Input-order independent. Does not mutate inputs or call resolver/clock.
    """
    ctx = _parse_context(context)

    validated_commitments = [validate_commitment(c) for c in commitments]
    validated_tasks = [validate_obligation_task(t) for t in obligation_tasks]
    validated_routes = [validate_route_profile(r) for r in route_profiles]

    _unique_ids([c["commitment_id"] for c in validated_commitments], label="commitment_id")
    _unique_ids([t["task_id"] for t in validated_tasks], label="task_id")
    _unique_ids([r["route_profile_id"] for r in validated_routes], label="route_profile_id")

    # Order-independent processing via sorted ids.
    validated_commitments = sorted(validated_commitments, key=lambda c: c["commitment_id"])
    validated_tasks = sorted(validated_tasks, key=lambda t: t["task_id"])
    validated_routes = sorted(validated_routes, key=lambda r: r["route_profile_id"])

    opportunities: list[ResolvedOpportunity] = []
    boundaries: list[KnownDecisionBoundary] = []
    diagnostics: list[AdapterDiagnostic] = []
    hard_active_ids: list[str] = []

    for c in validated_commitments:
        _adapt_one_commitment(
            c,
            ctx=ctx,
            routes=validated_routes,
            opportunities=opportunities,
            boundaries=boundaries,
            diagnostics=diagnostics,
            hard_active_ids=hard_active_ids,
        )

    if len(set(hard_active_ids)) > 1:
        for cid in sorted(set(hard_active_ids)):
            diagnostics.append(
                _make_diagnostic(
                    source_kind="COMMITMENT",
                    source_ref=cid,
                    code="HARD_COMMITMENT_CONFLICT",
                    rule_ids=("slice2e.commitment.hard_conflict",),
                )
            )
        _fail(
            "HARD_COMMITMENT_CONFLICT: distinct HARD commitments active at same decision instant: "
            + ",".join(sorted(set(hard_active_ids)))
        )

    for t in validated_tasks:
        _adapt_one_task(
            t,
            ctx=ctx,
            opportunities=opportunities,
            boundaries=boundaries,
            diagnostics=diagnostics,
        )

    return AdapterResult(
        opportunities=_sort_opportunities(opportunities),
        decision_boundaries=_sort_boundaries(boundaries),
        diagnostics=_sort_diagnostics(diagnostics),
    )
