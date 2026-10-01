"""Life Engine v2 Slice 2F — household / kickboxing routine adapters (pure).

Converts explicit HouseholdTaskFact backlog and finalized KickboxingSessionFact
history into Slice 2D ROUTINE_HABIT ResolvedOpportunity (+ household OBLIGATION
boundaries). Does not call the Slice 2D resolver, Slice 2C queue reconciliation,
or clock. No file I/O, network, queue/disk write, runtime mutation, or global PRNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from .decisions import ResolvedOpportunity, parse_resolved_opportunity
from .dynamics import require_v2_production_format
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

ROUTINE_DIAGNOSTIC_CODES = frozenset(
    {
        "HOUSEHOLD_FRESH",
        "HOUSEHOLD_TERMINAL",
        "HOUSEHOLD_LOWER_BAND_SUPPRESSED",
        "HOUSEHOLD_WINDOW_INSUFFICIENT",
        "HOUSEHOLD_LOCATION_BLOCKED",
        "EXERCISE_LOW_OPPORTUNITY",
        "EXERCISE_DAILY_CAP_REACHED",
        "EXERCISE_PENDING_PLAN_BLOCKED",
        "EXERCISE_FATIGUE_BLOCKED",
        "EXERCISE_WINDOW_INSUFFICIENT",
        "EXERCISE_LOCATION_BLOCKED",
        "EXERCISE_PHYSICAL_BLOCKED",
    }
)

_HOUSEHOLD_STATUSES = frozenset({"OPEN", "DONE", "CANCELLED"})
_TERMINAL_HOUSEHOLD = frozenset({"DONE", "CANCELLED"})
_FATIGUE_BANDS = ("LOW", "MODERATE", "HIGH", "VERY_HIGH")
_FATIGUE_RANK = {name: i for i, name in enumerate(_FATIGUE_BANDS)}
_AGE_BAND_RANK = {"AGING": 1, "PRESSING": 2, "OVERDUE": 3}
_HOUSEHOLD_FACT_FIELDS = frozenset(
    {"household_task_id", "created_at", "status", "location_id"}
)
_KICKBOXING_FACT_FIELDS = frozenset(
    {"session_id", "actual_start", "actual_end", "finalized_at"}
)
_FORBIDDEN_HOUSEHOLD_FIELDS = frozenset(
    {
        "severity",
        "messiness",
        "cleanliness_score",
        "clutter_score",
        "metadata",
        "home_mutation",
        "HOME_TRANSITION",
    }
)


# ---------------------------------------------------------------------------
# Transient structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoutineDiagnostic:
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
class RoutineAdapterResult:
    opportunities: tuple[ResolvedOpportunity, ...]
    decision_boundaries: tuple[KnownDecisionBoundary, ...]
    diagnostics: tuple[RoutineDiagnostic, ...]


@dataclass(frozen=True)
class RoutineAdapterContext:
    now: str
    current_location_id: str
    available_window_min: int
    physical_feasible_by_action: Mapping[str, bool]
    kickboxing_location_feasible: bool
    kickboxing_self_generated_today: int
    kickboxing_equivalent_pending_plans: int
    physical_fatigue_band: str


@dataclass(frozen=True)
class HouseholdTaskFact:
    household_task_id: str
    created_at: str
    status: str
    location_id: str


@dataclass(frozen=True)
class KickboxingSessionFact:
    session_id: str
    actual_start: str
    actual_end: str
    finalized_at: str


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


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{label} must be bool")
    return value


def _canonical_sorted_strs(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def _dt(ts: str, *, field: str) -> datetime:
    return parse_rfc3339(ts, field=field)


def _age_seconds(*, now: str, created_at: str) -> int:
    """Exact integer age in seconds — no float / total_seconds()."""
    now_dt = _dt(now, field="now")
    created_dt = _dt(created_at, field="created_at")
    if now_dt.microsecond != 0 or created_dt.microsecond != 0:
        _fail("household age math rejects sub-second timestamps")
    delta = now_dt - created_dt
    if delta.microseconds != 0:
        _fail("household age delta has nonzero microseconds")
    # timedelta.days / .seconds are ints; 86400 is integer constant (not float math).
    seconds = delta.days * 86400 + delta.seconds
    if seconds < 0:
        _fail("household created_at after now (future leakage)")
    return seconds


def _add_minutes_ts(ts: str, minutes: int, *, field: str) -> str:
    return format_rfc3339(add_minutes(_dt(ts, field=field), minutes))


def _tokyo_local_date(now: str) -> str:
    return _dt(now, field="now").date().isoformat()


def _make_diagnostic(
    *,
    source_kind: str,
    source_ref: str,
    code: str,
    rule_ids: Sequence[str] = (),
) -> RoutineDiagnostic:
    if code not in ROUTINE_DIAGNOSTIC_CODES:
        _fail(f"unknown RoutineDiagnostic code: {code}")
    return RoutineDiagnostic(
        source_kind=_require_nonempty_str(source_kind, "source_kind"),
        source_ref=_require_nonempty_str(source_ref, "source_ref"),
        code=code,
        rule_ids=_canonical_sorted_strs(
            [_require_nonempty_str(r, "rule_id") for r in rule_ids]
        ),
    )


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
        return None
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


def _physical_for(ctx: RoutineAdapterContext, action_kind: str) -> bool:
    if action_kind not in ctx.physical_feasible_by_action:
        _fail(
            f"missing physical feasibility fact for action_kind={action_kind!r} "
            "(no implicit true)"
        )
    return bool(ctx.physical_feasible_by_action[action_kind])


def _parse_context(
    context: RoutineAdapterContext | Mapping[str, Any],
) -> RoutineAdapterContext:
    if isinstance(context, RoutineAdapterContext):
        data = {
            "now": context.now,
            "current_location_id": context.current_location_id,
            "available_window_min": context.available_window_min,
            "physical_feasible_by_action": dict(context.physical_feasible_by_action),
            "kickboxing_location_feasible": context.kickboxing_location_feasible,
            "kickboxing_self_generated_today": context.kickboxing_self_generated_today,
            "kickboxing_equivalent_pending_plans": context.kickboxing_equivalent_pending_plans,
            "physical_fatigue_band": context.physical_fatigue_band,
        }
    elif isinstance(context, Mapping):
        data = dict(context)
    else:
        _fail("RoutineAdapterContext must be mapping or dataclass")

    allowed = {
        "now",
        "current_location_id",
        "available_window_min",
        "physical_feasible_by_action",
        "kickboxing_location_feasible",
        "kickboxing_self_generated_today",
        "kickboxing_equivalent_pending_plans",
        "physical_fatigue_band",
    }
    unknown = set(data) - allowed
    if unknown:
        _fail(f"RoutineAdapterContext unknown fields: {sorted(unknown)}")
    missing = allowed - set(data)
    if missing:
        _fail(f"RoutineAdapterContext missing fields: {sorted(missing)}")

    now = require_canonical_timestamp(data["now"], field="now")
    loc = _require_nonempty_str(data["current_location_id"], "current_location_id")
    window = _require_int_ge0(data["available_window_min"], "available_window_min")
    phys_raw = data["physical_feasible_by_action"]
    if not isinstance(phys_raw, Mapping):
        _fail("physical_feasible_by_action must be a mapping")
    phys: dict[str, bool] = {}
    for k, v in phys_raw.items():
        key = _require_nonempty_str(k, "physical_feasible_by_action key")
        phys[key] = _require_bool(v, f"physical_feasible_by_action[{key!r}]")
    # Physical facts are required lazily via _physical_for() only when emitting
    # that action's opportunity — not eagerly for every domain.
    fatigue = _require_nonempty_str(data["physical_fatigue_band"], "physical_fatigue_band")
    if fatigue not in _FATIGUE_RANK:
        _fail(f"physical_fatigue_band must be one of {list(_FATIGUE_BANDS)}")
    return RoutineAdapterContext(
        now=now,
        current_location_id=loc,
        available_window_min=window,
        physical_feasible_by_action=phys,
        kickboxing_location_feasible=_require_bool(
            data["kickboxing_location_feasible"], "kickboxing_location_feasible"
        ),
        kickboxing_self_generated_today=_require_int_ge0(
            data["kickboxing_self_generated_today"], "kickboxing_self_generated_today"
        ),
        kickboxing_equivalent_pending_plans=_require_int_ge0(
            data["kickboxing_equivalent_pending_plans"],
            "kickboxing_equivalent_pending_plans",
        ),
        physical_fatigue_band=fatigue,
    )


def parse_household_task_fact(
    data: HouseholdTaskFact | Mapping[str, Any],
) -> HouseholdTaskFact:
    if isinstance(data, HouseholdTaskFact):
        data = {
            "household_task_id": data.household_task_id,
            "created_at": data.created_at,
            "status": data.status,
            "location_id": data.location_id,
        }
    if not isinstance(data, Mapping):
        _fail("HouseholdTaskFact must be mapping or dataclass")
    forbidden = set(data) & _FORBIDDEN_HOUSEHOLD_FIELDS
    if forbidden:
        _fail(f"HouseholdTaskFact forbidden fields: {sorted(forbidden)}")
    unknown = set(data) - _HOUSEHOLD_FACT_FIELDS
    if unknown:
        _fail(f"HouseholdTaskFact unknown fields: {sorted(unknown)}")
    missing = _HOUSEHOLD_FACT_FIELDS - set(data)
    if missing:
        _fail(f"HouseholdTaskFact missing fields: {sorted(missing)}")
    status = _require_nonempty_str(data["status"], "status")
    if status not in _HOUSEHOLD_STATUSES:
        _fail(f"HouseholdTaskFact status must be OPEN|DONE|CANCELLED, got {status!r}")
    return HouseholdTaskFact(
        household_task_id=_require_nonempty_str(
            data["household_task_id"], "household_task_id"
        ),
        created_at=require_canonical_timestamp(data["created_at"], field="created_at"),
        status=status,
        location_id=_require_nonempty_str(data["location_id"], "location_id"),
    )


def parse_kickboxing_session_fact(
    data: KickboxingSessionFact | Mapping[str, Any],
    *,
    now: str,
) -> KickboxingSessionFact:
    if isinstance(data, KickboxingSessionFact):
        data = {
            "session_id": data.session_id,
            "actual_start": data.actual_start,
            "actual_end": data.actual_end,
            "finalized_at": data.finalized_at,
        }
    if not isinstance(data, Mapping):
        _fail("KickboxingSessionFact must be mapping or dataclass")
    unknown = set(data) - _KICKBOXING_FACT_FIELDS
    if unknown:
        _fail(f"KickboxingSessionFact unknown fields: {sorted(unknown)}")
    missing = _KICKBOXING_FACT_FIELDS - set(data)
    if missing:
        _fail(f"KickboxingSessionFact missing fields: {sorted(missing)}")

    now_c = require_canonical_timestamp(now, field="now")
    now_dt = _dt(now_c, field="now")
    sid = _require_nonempty_str(data["session_id"], "session_id")
    start = require_canonical_timestamp(data["actual_start"], field="actual_start")
    end = require_canonical_timestamp(data["actual_end"], field="actual_end")
    finalized = require_canonical_timestamp(data["finalized_at"], field="finalized_at")
    start_dt = _dt(start, field="actual_start")
    end_dt = _dt(end, field="actual_end")
    finalized_dt = _dt(finalized, field="finalized_at")
    if start_dt > end_dt:
        _fail(f"kickboxing session {sid}: actual_start after actual_end")
    if finalized_dt < end_dt:
        _fail(f"kickboxing session {sid}: finalized_at before actual_end")
    if start_dt > now_dt or end_dt > now_dt or finalized_dt > now_dt:
        _fail(f"kickboxing session {sid}: future timestamp leakage relative to now")
    return KickboxingSessionFact(
        session_id=sid,
        actual_start=start,
        actual_end=end,
        finalized_at=finalized,
    )


def household_age_band(
    *,
    now: str,
    created_at: str,
    household_age_policy: Mapping[str, Any],
) -> str:
    """Classify FRESH/AGING/PRESSING/OVERDUE from policy thresholds (no hardcode)."""
    aging = _require_int_ge0(household_age_policy["aging_start_min"], "aging_start_min")
    pressing = _require_int_ge0(
        household_age_policy["pressing_start_min"], "pressing_start_min"
    )
    overdue = _require_int_ge0(
        household_age_policy["overdue_start_min"], "overdue_start_min"
    )
    age_sec = _age_seconds(now=now, created_at=created_at)
    aging_sec = aging * 60
    pressing_sec = pressing * 60
    overdue_sec = overdue * 60
    if age_sec < aging_sec:
        return "FRESH"
    if age_sec < pressing_sec:
        return "AGING"
    if age_sec < overdue_sec:
        return "PRESSING"
    return "OVERDUE"


def count_kickboxing_sessions_in_window(
    sessions: Sequence[KickboxingSessionFact],
    *,
    now: str,
    history_window_days: int,
) -> int:
    """Count sessions with lower_bound < actual_end <= now (exact bound expired)."""
    days = history_window_days
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        _fail("history_window_days must be int >= 1")
    now_dt = _dt(now, field="now")
    lower = now_dt - timedelta(days=days)
    count = 0
    for s in sessions:
        end_dt = _dt(s.actual_end, field="actual_end")
        if lower < end_dt <= now_dt:
            count += 1
    return count


def kickboxing_opportunity_level(
    count_in_window: int,
    opportunity_by_count: Mapping[str, Any],
) -> str:
    if count_in_window < 0:
        _fail("count_in_window must be >= 0")
    if count_in_window == 0:
        key = "0"
    elif count_in_window == 1:
        key = "1"
    else:
        key = "2_or_more"
    if key not in opportunity_by_count:
        _fail(f"opportunity_by_count_in_window missing key {key!r}")
    level = opportunity_by_count[key]
    if level not in {"HIGH", "NORMAL", "LOW"}:
        _fail(f"invalid opportunity level: {level!r}")
    return str(level)


# ---------------------------------------------------------------------------
# Household adaptation
# ---------------------------------------------------------------------------


def _emit_household_boundaries(
    task: HouseholdTaskFact,
    *,
    now: str,
    household_age: Mapping[str, Any],
    boundaries: list[KnownDecisionBoundary],
) -> None:
    tid = task.household_task_id
    for edge, minutes_key, reason in (
        ("aging_start", "aging_start_min", "HOUSEHOLD_AGING_START"),
        ("pressing_start", "pressing_start_min", "HOUSEHOLD_PRESSING_START"),
        ("overdue_start", "overdue_start_min", "HOUSEHOLD_OVERDUE_START"),
    ):
        minutes = int(household_age[minutes_key])
        due = _add_minutes_ts(task.created_at, minutes, field="created_at")
        b = _make_boundary(
            due_at=due,
            trigger_kind="OBLIGATION",
            decision_key=f"household:{tid}:OBLIGATION:{edge}:{due}",
            reason_code=reason,
            source_kind="ROUTINE",
            source_ref=tid,
            now=now,
        )
        if b is not None:
            boundaries.append(b)


def _adapt_household(
    *,
    ctx: RoutineAdapterContext,
    tasks: Sequence[HouseholdTaskFact],
    household_age: Mapping[str, Any],
    opportunities: list[ResolvedOpportunity],
    boundaries: list[KnownDecisionBoundary],
    diagnostics: list[RoutineDiagnostic],
) -> None:
    duration_min = household_age["duration_min"]
    if isinstance(duration_min, bool) or not isinstance(duration_min, int) or duration_min < 1:
        _fail("household_age.duration_min must be int >= 1")
    tiebreak = household_age["overdue_uses_exact_age_tiebreak"]
    if not isinstance(tiebreak, bool):
        _fail("overdue_uses_exact_age_tiebreak must be bool")
    # Slice 2F never synthesizes household tasks from HOME/room state.
    # requires_external_task_source must be true when adapting household facts.
    requires_external = household_age.get("requires_external_task_source")
    if not isinstance(requires_external, bool):
        _fail("requires_external_task_source must be bool")
    if tasks and requires_external is not True:
        _fail(
            "Slice 2F requires domain_policy.household_age.requires_external_task_source="
            "true (do not synthesize household tasks; fail closed)"
        )

    open_annotated: list[tuple[HouseholdTaskFact, str, int]] = []

    for task in tasks:
        tid = task.household_task_id
        # ALL facts: created_at <= now before terminal/open branching.
        age_sec = _age_seconds(now=ctx.now, created_at=task.created_at)

        if task.status in _TERMINAL_HOUSEHOLD:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="ROUTINE",
                    source_ref=tid,
                    code="HOUSEHOLD_TERMINAL",
                    rule_ids=("slice2f.household.terminal",),
                )
            )
            continue

        band = household_age_band(
            now=ctx.now,
            created_at=task.created_at,
            household_age_policy=household_age,
        )
        _emit_household_boundaries(
            task, now=ctx.now, household_age=household_age, boundaries=boundaries
        )

        if band == "FRESH":
            diagnostics.append(
                _make_diagnostic(
                    source_kind="ROUTINE",
                    source_ref=tid,
                    code="HOUSEHOLD_FRESH",
                    rule_ids=("slice2f.household.fresh",),
                )
            )
            continue

        open_annotated.append((task, band, age_sec))

    if not open_annotated:
        return

    highest_rank = max(_AGE_BAND_RANK[b] for _, b, _ in open_annotated)
    highest_band = next(k for k, v in _AGE_BAND_RANK.items() if v == highest_rank)
    selected: list[tuple[HouseholdTaskFact, str, int]] = [
        row for row in open_annotated if row[1] == highest_band
    ]

    for task, band, _age in open_annotated:
        if band != highest_band:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="ROUTINE",
                    source_ref=task.household_task_id,
                    code="HOUSEHOLD_LOWER_BAND_SUPPRESSED",
                    rule_ids=("slice2f.household.band_suppress",),
                )
            )

    if highest_band == "OVERDUE" and tiebreak:
        max_age = max(age for _, _, age in selected)
        selected = [row for row in selected if row[2] == max_age]
        # Exact-age ties kept; later canonicalized by task id via opportunity sort.

    for task, band, _age in selected:
        tid = task.household_task_id
        time_ok = ctx.available_window_min >= duration_min
        loc_ok = ctx.current_location_id == task.location_id
        phys_ok = _physical_for(ctx, "HOUSEHOLD")
        if not time_ok:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="ROUTINE",
                    source_ref=tid,
                    code="HOUSEHOLD_WINDOW_INSUFFICIENT",
                    rule_ids=("slice2f.household.window",),
                )
            )
        if not loc_ok:
            diagnostics.append(
                _make_diagnostic(
                    source_kind="ROUTINE",
                    source_ref=tid,
                    code="HOUSEHOLD_LOCATION_BLOCKED",
                    rule_ids=("slice2f.household.location",),
                )
            )
        opportunities.append(
            _make_opportunity(
                opportunity_key=f"household:{tid}:work",
                opportunity_class="ROUTINE_HABIT",
                action_kind="HOUSEHOLD",
                source_kind="ROUTINE",
                source_ref=tid,
                soft_candidate=True,
                time_feasible=time_ok,
                location_feasible=loc_ok,
                physical_feasible=phys_ok,
                domain_guard_satisfied=True,
                rule_ids=(f"slice2f.household.work.{band}",),
            )
        )


# ---------------------------------------------------------------------------
# Kickboxing adaptation
# ---------------------------------------------------------------------------


def _adapt_kickboxing(
    *,
    ctx: RoutineAdapterContext,
    sessions: Sequence[KickboxingSessionFact],
    exercise_policy: Mapping[str, Any],
    opportunities: list[ResolvedOpportunity],
    diagnostics: list[RoutineDiagnostic],
) -> None:
    local_date = _tokyo_local_date(ctx.now)
    source_ref = f"exercise-history:kickboxing:{local_date}"
    count = count_kickboxing_sessions_in_window(
        sessions,
        now=ctx.now,
        history_window_days=int(exercise_policy["history_window_days"]),
    )
    level = kickboxing_opportunity_level(
        count, exercise_policy["opportunity_by_count_in_window"]
    )

    if level == "LOW":
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_LOW_OPPORTUNITY",
                rule_ids=("slice2f.exercise.low_opportunity",),
            )
        )
        return

    max_per_day = _require_int_ge0(
        exercise_policy["max_self_generated_per_day"], "max_self_generated_per_day"
    )
    max_pending = _require_int_ge0(
        exercise_policy["max_equivalent_pending_plans"],
        "max_equivalent_pending_plans",
    )
    if ctx.kickboxing_self_generated_today >= max_per_day:
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_DAILY_CAP_REACHED",
                rule_ids=("slice2f.exercise.daily_cap",),
            )
        )
        return
    if ctx.kickboxing_equivalent_pending_plans >= max_pending:
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_PENDING_PLAN_BLOCKED",
                rule_ids=("slice2f.exercise.pending_plans",),
            )
        )
        return

    block_band = _require_nonempty_str(
        exercise_policy["block_self_generated_at_fatigue_band"],
        "block_self_generated_at_fatigue_band",
    )
    if block_band not in _FATIGUE_RANK:
        _fail(f"invalid block_self_generated_at_fatigue_band: {block_band!r}")
    if _FATIGUE_RANK[ctx.physical_fatigue_band] >= _FATIGUE_RANK[block_band]:
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_FATIGUE_BLOCKED",
                rule_ids=("slice2f.exercise.fatigue",),
            )
        )
        return

    duration_min = exercise_policy["duration_min"]
    if isinstance(duration_min, bool) or not isinstance(duration_min, int) or duration_min < 1:
        _fail("exercise_kickboxing.duration_min must be int >= 1")

    time_ok = ctx.available_window_min >= duration_min
    loc_ok = ctx.kickboxing_location_feasible
    phys_ok = _physical_for(ctx, "KICKBOXING")
    if not time_ok:
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_WINDOW_INSUFFICIENT",
                rule_ids=("slice2f.exercise.window",),
            )
        )
    if not loc_ok:
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_LOCATION_BLOCKED",
                rule_ids=("slice2f.exercise.location",),
            )
        )
    if not phys_ok:
        diagnostics.append(
            _make_diagnostic(
                source_kind="ROUTINE",
                source_ref=source_ref,
                code="EXERCISE_PHYSICAL_BLOCKED",
                rule_ids=("slice2f.exercise.physical",),
            )
        )

    if level not in {"HIGH", "NORMAL"}:
        _fail(f"kickboxing self-gen emit requires HIGH|NORMAL, got {level!r}")
    opportunities.append(
        _make_opportunity(
            opportunity_key=f"routine:kickboxing:self:{local_date}",
            opportunity_class="ROUTINE_HABIT",
            action_kind="KICKBOXING",
            source_kind="ROUTINE",
            source_ref=source_ref,
            soft_candidate=True,
            time_feasible=time_ok,
            location_feasible=loc_ok,
            physical_feasible=phys_ok,
            domain_guard_satisfied=True,
            rule_ids=(f"slice2f.exercise.self.{level}",),
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
        dups = sorted({k for k in keys if keys.count(k) > 1})
        _fail(f"ambiguous duplicate opportunity_key: {dups}")
    return tuple(sorted(items, key=lambda o: o.opportunity_key))


def _sort_boundaries(
    items: Sequence[KnownDecisionBoundary],
) -> tuple[KnownDecisionBoundary, ...]:
    by_key: dict[str, KnownDecisionBoundary] = {}
    for b in items:
        prev = by_key.get(b.decision_key)
        if prev is None:
            by_key[b.decision_key] = b
            continue
        if prev != b:
            _fail(
                f"ambiguous duplicate decision_key with differing boundary: "
                f"{b.decision_key}"
            )
    ordered = sorted(
        by_key.values(),
        key=lambda b: (b.due_at, b.trigger_kind, b.decision_key, b.reason_code),
    )
    return tuple(ordered)


def _sort_diagnostics(
    items: Sequence[RoutineDiagnostic],
) -> tuple[RoutineDiagnostic, ...]:
    return tuple(
        sorted(
            items,
            key=lambda d: (d.source_kind, d.source_ref, d.code, d.rule_ids),
        )
    )


def adapt_routine_domains(
    *,
    context: RoutineAdapterContext | Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    household_tasks: Sequence[HouseholdTaskFact | Mapping[str, Any]] = (),
    kickboxing_sessions: Sequence[KickboxingSessionFact | Mapping[str, Any]] = (),
) -> RoutineAdapterResult:
    """Pure routine adaptation of household backlog + kickboxing history.

    Input-order independent. Does not mutate inputs or call resolver/clock.
    Does not invent household tasks from HOME/room state.
    """
    ctx = _parse_context(context)
    policy = require_v2_production_format(behavior_policy)
    domain = policy["domain_policy"]
    household_age = domain["household_age"]
    exercise = domain["exercise_kickboxing"]

    validated_tasks = [parse_household_task_fact(t) for t in household_tasks]
    validated_sessions = [
        parse_kickboxing_session_fact(s, now=ctx.now) for s in kickboxing_sessions
    ]

    task_ids = [t.household_task_id for t in validated_tasks]
    if len(task_ids) != len(set(task_ids)):
        _fail(f"ambiguous duplicate household_task_id: {sorted(task_ids)}")
    session_ids = [s.session_id for s in validated_sessions]
    if len(session_ids) != len(set(session_ids)):
        _fail(f"ambiguous duplicate session_id: {sorted(session_ids)}")

    validated_tasks = sorted(validated_tasks, key=lambda t: t.household_task_id)
    validated_sessions = sorted(validated_sessions, key=lambda s: s.session_id)

    opportunities: list[ResolvedOpportunity] = []
    boundaries: list[KnownDecisionBoundary] = []
    diagnostics: list[RoutineDiagnostic] = []

    _adapt_household(
        ctx=ctx,
        tasks=validated_tasks,
        household_age=household_age,
        opportunities=opportunities,
        boundaries=boundaries,
        diagnostics=diagnostics,
    )
    _adapt_kickboxing(
        ctx=ctx,
        sessions=validated_sessions,
        exercise_policy=exercise,
        opportunities=opportunities,
        diagnostics=diagnostics,
    )

    return RoutineAdapterResult(
        opportunities=_sort_opportunities(opportunities),
        decision_boundaries=_sort_boundaries(boundaries),
        diagnostics=_sort_diagnostics(diagnostics),
    )
