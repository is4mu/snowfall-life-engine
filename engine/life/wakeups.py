"""Life Engine v2 Slice 2C — semantic decision wakeups (scheduler layer only).

Pure deterministic: when to reassess, not what to do next.
Does not wire into clock.py::advance() or Foundation reconcile_threshold_wakeups().
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Mapping, NamedTuple, Sequence

from .derived import (
    MINUTES_PER_DAY,
    SyntheticSleepProfile,
    build_circadian_anchors,
    circadian_bias_at_minute,
    compute_sleep_pressure_breakdown,
    validate_synthetic_sleep_profile,
)
from .dynamics import integrate_v2_interval, require_v2_production_format, resolve_v2_rates
from .errors import ErrorCode, LifeEngineError
from .fixed_point import (
    BOUNDS,
    SECONDS_PER_HOUR,
    to_scaled,
    validate_human_state,
    validate_rate_remainders,
)
from .ids import stable_id
from .invariants import queue_event_sort_key
from .schema import reject_binary_floats, validate_instance
from .timeutil import canonicalize_timestamp, format_rfc3339, parse_rfc3339, require_canonical_timestamp

TRIGGER_KINDS = frozenset(
    {
        "THRESHOLD",
        "ACTIVE_REASSESSMENT",
        "COMMITMENT_DEPARTURE",
        "PLANNED_WINDOW",
        "OBLIGATION",
        "EXOGENOUS",
        "DAY_PLANNING",
        "CIRCADIAN_REASSESSMENT",
        "IDLE",
        # C3-owned same-time bridges only (not caller KnownDecisionBoundary).
        "POST_FINALIZE",
        "IMMEDIATE_REASSESSMENT",
    }
)

KNOWN_BOUNDARY_TRIGGERS = frozenset(
    {
        "ACTIVE_REASSESSMENT",
        "COMMITMENT_DEPARTURE",
        "PLANNED_WINDOW",
        "OBLIGATION",
        "EXOGENOUS",
        "DAY_PLANNING",
    }
)

METRICS = frozenset(
    {"HUNGER", "PHYSICAL_FATIGUE", "STRESS", "SOCIAL_BATTERY", "SLEEP_PRESSURE"}
)
DIRECTIONS = frozenset({"UP", "DOWN"})
INTERRUPTIBILITY = frozenset({"NONE", "REASSESSABLE", "NON_INTERRUPTIBLE"})

V2_DECISION_WAKEUP_PRIORITY = 50

# Architectural same-time precedence (§34). Lower rank = higher precedence.
_PRECEDENCE_RANK: dict[str, int] = {
    "COMMITMENT_DEPARTURE": 1,
    "OBLIGATION": 3,
    "EXOGENOUS": 4,
    "PLANNED_WINDOW": 5,
    "ACTIVE_REASSESSMENT": 7,
    "DAY_PLANNING": 8,
    "CIRCADIAN_REASSESSMENT": 9,
    "IDLE": 10,
}

_URGENT_THRESHOLD = frozenset(
    {
        ("HUNGER", "URGENT"),
        ("PHYSICAL_FATIGUE", "VERY_HIGH"),
        ("SLEEP_PRESSURE", "CRITICAL"),
    }
)

_NONURGENT_THRESHOLD = frozenset(
    {
        ("HUNGER", "MEAL_NEEDED"),
        ("PHYSICAL_FATIGUE", "HIGH"),
        ("STRESS", "HIGH"),
        ("STRESS", "VERY_HIGH"),
        ("SOCIAL_BATTERY", "LOW"),
        ("SLEEP_PRESSURE", "HIGH"),
    }
)

# Closed semantic set for v2 THRESHOLD payload (schema + invariant).
# Re-exported / mirrored in invariants for queue fail-closed checks.
ALLOWED_V2_THRESHOLD_TUPLES: frozenset[tuple[str, str, str]] = frozenset(
    {
        ("HUNGER", "MEAL_NEEDED", "UP"),
        ("HUNGER", "URGENT", "UP"),
        ("PHYSICAL_FATIGUE", "HIGH", "UP"),
        ("PHYSICAL_FATIGUE", "VERY_HIGH", "UP"),
        ("STRESS", "HIGH", "UP"),
        ("STRESS", "VERY_HIGH", "UP"),
        ("SOCIAL_BATTERY", "LOW", "DOWN"),
        ("SLEEP_PRESSURE", "HIGH", "UP"),
        ("SLEEP_PRESSURE", "CRITICAL", "UP"),
    }
)


def assert_allowed_v2_threshold_tuple(metric: object, target_band: object, direction: object) -> None:
    key = (metric, target_band, direction)
    if key not in ALLOWED_V2_THRESHOLD_TUPLES:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"invalid v2 THRESHOLD tuple: metric={metric!r} target_band={target_band!r} "
            f"direction={direction!r}",
        )


_CONTEXT_KEYS = frozenset(
    {
        "is_sleeping",
        "active_interruptibility",
        "next_legal_reassessment_at",
        "fatigue_high_relevant",
        "stress_relevant",
        "social_low_relevant",
    }
)

_BOUNDARY_KEYS = frozenset(
    {
        "due_at",
        "trigger_kind",
        "decision_key",
        "reason_code",
        "source_kind",
        "source_ref",
    }
)

# Human-state threshold families from Behavior Policy derived_bands (no hard-coded values).
_UP_THRESHOLD_SPECS: tuple[tuple[str, str, str], ...] = (
    ("HUNGER", "hunger", "MEAL_NEEDED"),
    ("HUNGER", "hunger", "URGENT"),
    ("PHYSICAL_FATIGUE", "physical_fatigue", "HIGH"),
    ("PHYSICAL_FATIGUE", "physical_fatigue", "VERY_HIGH"),
    ("STRESS", "stress", "HIGH"),
    ("STRESS", "stress", "VERY_HIGH"),
)

Direction = Literal["UP", "DOWN"]
CrossingKind = Literal["IMMEDIATE", "FUTURE", "NONE"]


class ThresholdCandidate(NamedTuple):
    metric: str
    target_band: str
    direction: str
    due_at: str
    decision_key: str
    reason_code: str


class KnownDecisionBoundary(NamedTuple):
    due_at: str
    trigger_kind: str
    decision_key: str
    reason_code: str
    source_kind: str | None
    source_ref: str | None


class DecisionWakeupContext(NamedTuple):
    is_sleeping: bool
    active_interruptibility: str
    next_legal_reassessment_at: str | None
    fatigue_high_relevant: bool
    stress_relevant: bool
    social_low_relevant: bool


@dataclass(frozen=True)
class FutureCandidate:
    due_at: str
    trigger_kind: str
    decision_key: str
    reason_code: str
    metric: str | None
    target_band: str | None
    direction: str | None
    source_kind: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True)
class ProjectionResult:
    immediate_reassessment_reasons: tuple[str, ...]
    future_candidates: tuple[FutureCandidate, ...]
    selected: FutureCandidate | None
    sleep_projection_mode: str | None  # EXACT | CIRCADIAN_FALLBACK | IDLE_FALLBACK | None


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} must be int (no bool/float)")
    return value


def _as_dt(value: datetime | str, *, field: str) -> datetime:
    if isinstance(value, datetime):
        return parse_rfc3339(format_rfc3339(value), field=field)
    return parse_rfc3339(str(value), field=field)


def _as_canon(value: datetime | str, *, field: str) -> str:
    return canonicalize_timestamp(
        value if isinstance(value, str) else format_rfc3339(value),
        field=field,
    )


def is_managed_v2_decision_wakeup(event: Mapping[str, Any]) -> bool:
    """Identify Slice-2C-managed events via schema_version + event_kind only."""
    return (
        int(event.get("schema_version", 0)) == 2
        and event.get("event_kind") == "DECISION_WAKEUP"
    )


def exact_threshold_crossing_eta_seconds(
    *,
    field_value: int,
    remainder: int,
    rate_per_hour: int,
    threshold: int,
    direction: str,
) -> tuple[CrossingKind, int | None]:
    """Exact scaled UP/DOWN crossing ETA.

    Latent: scaled = field_value * 3600 + remainder.

    UP: already when scaled >= threshold*3600; else ceil((target-scaled)/rate) for rate>0.
    DOWN: already when scaled <= threshold*3600; else ceil((scaled-target)/(-rate)) for rate<0.

    Returns (kind, seconds). FUTURE seconds are always > 0. IMMEDIATE → seconds=0.
    NONE → no future crossing (wrong-sign rate or zero rate).
    """
    field_value = _require_int(field_value, "field_value")
    remainder = _require_int(remainder, "remainder")
    rate_per_hour = _require_int(rate_per_hour, "rate_per_hour")
    threshold = _require_int(threshold, "threshold")
    if direction not in DIRECTIONS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid direction: {direction!r}")
    if remainder < 0 or remainder >= SECONDS_PER_HOUR:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"bad remainder {remainder}")

    scaled = to_scaled(field_value, remainder)
    target = threshold * SECONDS_PER_HOUR

    if direction == "UP":
        if scaled >= target:
            return "IMMEDIATE", 0
        if rate_per_hour <= 0:
            return "NONE", None
        needed = target - scaled
        # Exact ceil division: earliest second where scaled + rate*t >= target.
        secs = (needed + rate_per_hour - 1) // rate_per_hour
        if secs <= 0:
            return "IMMEDIATE", 0
        return "FUTURE", secs

    # DOWN
    if scaled <= target:
        return "IMMEDIATE", 0
    if rate_per_hour >= 0:
        return "NONE", None
    need = scaled - target
    neg_rate = -rate_per_hour
    secs = (need + neg_rate - 1) // neg_rate
    if secs <= 0:
        return "IMMEDIATE", 0
    return "FUTURE", secs


def social_low_down_threshold(policy: Mapping[str, Any]) -> int:
    """DOWN target for SOCIAL_BATTERY LOW: first scaled value in LOW band.

    LOW ⇔ value < MODERATE. Earliest DOWN entry is scaled == MODERATE*3600 - 1,
    encoded as threshold such that scaled <= threshold*3600 is wrong for rem≠0.
    Callers use :func:`exact_scaled_down_crossing_eta_seconds` with target_scaled.
    """
    pol = require_v2_production_format(policy)
    moderate = _require_int(pol["derived_bands"]["social_battery"]["MODERATE"], "MODERATE")
    if moderate < 1:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "social MODERATE must be >= 1")
    return moderate


def exact_scaled_down_crossing_eta_seconds(
    *,
    field_value: int,
    remainder: int,
    rate_per_hour: int,
    target_scaled: int,
) -> tuple[CrossingKind, int | None]:
    """DOWN crossing against an arbitrary scaled target (inclusive)."""
    field_value = _require_int(field_value, "field_value")
    remainder = _require_int(remainder, "remainder")
    rate_per_hour = _require_int(rate_per_hour, "rate_per_hour")
    target_scaled = _require_int(target_scaled, "target_scaled")
    scaled = to_scaled(field_value, remainder)
    if scaled <= target_scaled:
        return "IMMEDIATE", 0
    if rate_per_hour >= 0:
        return "NONE", None
    need = scaled - target_scaled
    neg_rate = -rate_per_hour
    secs = (need + neg_rate - 1) // neg_rate
    if secs <= 0:
        return "IMMEDIATE", 0
    return "FUTURE", secs


def validate_decision_wakeup_context(ctx: DecisionWakeupContext | Mapping[str, Any]) -> DecisionWakeupContext:
    if isinstance(ctx, DecisionWakeupContext):
        data = ctx._asdict()
    elif isinstance(ctx, Mapping):
        unknown = set(ctx.keys()) - _CONTEXT_KEYS
        if unknown:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"DecisionWakeupContext unknown fields: {sorted(unknown)}",
            )
        missing = _CONTEXT_KEYS - set(ctx.keys())
        if missing:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"DecisionWakeupContext missing fields: {sorted(missing)}",
            )
        data = dict(ctx)
    else:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "DecisionWakeupContext must be mapping or NamedTuple")
    reject_binary_floats(data)
    if not isinstance(data["is_sleeping"], bool):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "is_sleeping must be bool")
    inter = data["active_interruptibility"]
    if inter not in INTERRUPTIBILITY:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid interruptibility: {inter!r}")
    nxt = data["next_legal_reassessment_at"]
    if nxt is not None:
        nxt = require_canonical_timestamp(nxt, field="next_legal_reassessment_at")
    for flag_key in ("fatigue_high_relevant", "stress_relevant", "social_low_relevant"):
        if not isinstance(data[flag_key], bool):
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"{flag_key} must be bool")
    return DecisionWakeupContext(
        is_sleeping=bool(data["is_sleeping"]),
        active_interruptibility=str(inter),
        next_legal_reassessment_at=nxt,
        fatigue_high_relevant=bool(data["fatigue_high_relevant"]),
        stress_relevant=bool(data["stress_relevant"]),
        social_low_relevant=bool(data["social_low_relevant"]),
    )


def validate_known_decision_boundary(
    boundary: KnownDecisionBoundary | Mapping[str, Any],
    *,
    now: datetime | str,
) -> KnownDecisionBoundary:
    if isinstance(boundary, KnownDecisionBoundary):
        data = boundary._asdict()
    elif isinstance(boundary, Mapping):
        unknown = set(boundary.keys()) - _BOUNDARY_KEYS
        if unknown:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"KnownDecisionBoundary unknown fields: {sorted(unknown)}",
            )
        missing = _BOUNDARY_KEYS - set(boundary.keys())
        if missing:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"KnownDecisionBoundary missing fields: {sorted(missing)}",
            )
        data = dict(boundary)
    else:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "KnownDecisionBoundary must be mapping or NamedTuple")
    reject_binary_floats(data)
    for key in ("due_at", "trigger_kind", "decision_key", "reason_code"):
        if data[key] in (None, ""):
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"KnownDecisionBoundary missing {key}")
    trigger = data["trigger_kind"]
    if trigger not in KNOWN_BOUNDARY_TRIGGERS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid known trigger_kind: {trigger!r}")
    due = require_canonical_timestamp(data["due_at"], field="due_at")
    now_dt = _as_dt(now, field="now")
    if parse_rfc3339(due, field="due_at") < now_dt:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "known boundary due_at before now")
    sk = data["source_kind"]
    sr = data["source_ref"]
    if sk is not None and (not isinstance(sk, str) or not sk):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "source_kind must be non-empty str or null")
    if sr is not None and (not isinstance(sr, str) or not sr):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "source_ref must be non-empty str or null")
    if not isinstance(data["decision_key"], str) or not data["decision_key"]:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "decision_key must be non-empty str")
    if not isinstance(data["reason_code"], str) or not data["reason_code"]:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "reason_code must be non-empty str")
    return KnownDecisionBoundary(
        due_at=due,
        trigger_kind=str(trigger),
        decision_key=str(data["decision_key"]),
        reason_code=str(data["reason_code"]),
        source_kind=sk,
        source_ref=sr,
    )


def _threshold_decision_key(metric: str, band: str, direction: str) -> str:
    return f"threshold:{metric}:{band}:{direction}"


def _threshold_reason_code(metric: str, band: str, direction: str) -> str:
    return f"THRESHOLD_{metric}_{band}_{direction}"


def _threshold_relevant(
    metric: str,
    band: str,
    ctx: DecisionWakeupContext,
) -> bool:
    if ctx.is_sleeping:
        return False
    if metric == "HUNGER":
        return True
    if metric == "SLEEP_PRESSURE":
        return True
    if metric == "PHYSICAL_FATIGUE":
        if band == "HIGH":
            return ctx.fatigue_high_relevant
        if band == "VERY_HIGH":
            return True
        return False
    if metric == "STRESS":
        return ctx.stress_relevant
    if metric == "SOCIAL_BATTERY":
        return ctx.social_low_relevant
    return False


def project_human_state_threshold_candidates(
    *,
    policy: Mapping[str, Any],
    human_state: Mapping[str, Any],
    rate_remainders: Mapping[str, Any],
    activity_context: Mapping[str, Any],
    now: datetime | str,
    wakeup_context: DecisionWakeupContext | Mapping[str, Any],
) -> tuple[list[str], list[FutureCandidate]]:
    """Project HUNGER/FATIGUE/STRESS/SOCIAL threshold candidates (not sleep pressure)."""
    pol = require_v2_production_format(policy)
    hs = validate_human_state(human_state)
    rem = validate_rate_remainders(rate_remainders)
    rates = resolve_v2_rates(pol, activity_context)
    ctx = validate_decision_wakeup_context(wakeup_context)
    now_dt = _as_dt(now, field="now")
    now_s = format_rfc3339(now_dt)

    immediate: list[str] = []
    future: list[FutureCandidate] = []

    bands = pol["derived_bands"]
    for metric, field, band in _UP_THRESHOLD_SPECS:
        thr = _require_int(bands[field][band], f"{field}.{band}")
        kind, secs = exact_threshold_crossing_eta_seconds(
            field_value=hs[field],
            remainder=rem[field],
            rate_per_hour=rates[field],
            threshold=thr,
            direction="UP",
        )
        if kind == "NONE":
            continue
        if not _threshold_relevant(metric, band, ctx):
            continue
        dkey = _threshold_decision_key(metric, band, "UP")
        rcode = _threshold_reason_code(metric, band, "UP")
        if kind == "IMMEDIATE":
            immediate.append(rcode)
            continue
        assert secs is not None and secs > 0
        due = format_rfc3339(now_dt + timedelta(seconds=secs))
        future.append(
            FutureCandidate(
                due_at=due,
                trigger_kind="THRESHOLD",
                decision_key=dkey,
                reason_code=rcode,
                metric=metric,
                target_band=band,
                direction="UP",
            )
        )

    # SOCIAL_BATTERY LOW (DOWN): target_scaled = MODERATE*3600 - 1
    moderate = social_low_down_threshold(pol)
    target_scaled = moderate * SECONDS_PER_HOUR - 1
    kind, secs = exact_scaled_down_crossing_eta_seconds(
        field_value=hs["social_battery"],
        remainder=rem["social_battery"],
        rate_per_hour=rates["social_battery"],
        target_scaled=target_scaled,
    )
    if kind != "NONE" and _threshold_relevant("SOCIAL_BATTERY", "LOW", ctx):
        dkey = _threshold_decision_key("SOCIAL_BATTERY", "LOW", "DOWN")
        rcode = _threshold_reason_code("SOCIAL_BATTERY", "LOW", "DOWN")
        if kind == "IMMEDIATE":
            immediate.append(rcode)
        else:
            assert secs is not None and secs > 0
            due = format_rfc3339(now_dt + timedelta(seconds=secs))
            future.append(
                FutureCandidate(
                    due_at=due,
                    trigger_kind="THRESHOLD",
                    decision_key=dkey,
                    reason_code=rcode,
                    metric="SOCIAL_BATTERY",
                    target_band="LOW",
                    direction="DOWN",
                )
            )

    # Suppress due_at == now (should not happen for FUTURE, but fail closed).
    future = [c for c in future if c.due_at != now_s]
    return immediate, future


def next_circadian_anchor_after(
    *,
    now: datetime | str,
    profile: Mapping[str, Any] | SyntheticSleepProfile,
    policy: Mapping[str, Any],
) -> str | None:
    """Return canonical timestamp of the next circadian anchor strictly after now."""
    pol = require_v2_production_format(policy)
    prof = validate_synthetic_sleep_profile(profile)
    now_dt = _as_dt(now, field="now")
    anchors = build_circadian_anchors(prof, pol)
    minutes: list[int] = []
    seen: set[int] = set()
    for a in anchors[:-1]:
        if a.minute_local not in seen:
            seen.add(a.minute_local)
            minutes.append(a.minute_local)
    day_start = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    best: datetime | None = None
    for day_offset in range(0, 3):
        for m in minutes:
            candidate = day_start + timedelta(days=day_offset, minutes=m)
            if candidate <= now_dt:
                continue
            if best is None or candidate < best:
                best = candidate
    if best is None:
        return None
    return format_rfc3339(best)


def _sleep_pressure_at_offset(
    *,
    policy: Mapping[str, Any],
    profile: SyntheticSleepProfile,
    human_state: Mapping[str, int],
    rate_remainders: Mapping[str, int],
    activity_context: Mapping[str, Any],
    elapsed_since_main_end_seconds: int,
    total_nap_awake_credit_min: int,
    now_dt: datetime,
    offset_seconds: int,
) -> int:
    offset_seconds = _require_int(offset_seconds, "offset_seconds")
    if offset_seconds < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "negative offset_seconds")
    hs = dict(human_state)
    rem = dict(rate_remainders)
    if offset_seconds > 0:
        hs, rem = integrate_v2_interval(
            hs,
            rem,
            policy,
            activity_context,
            offset_seconds,
        )
    elapsed = elapsed_since_main_end_seconds + offset_seconds
    local_dt = now_dt + timedelta(seconds=offset_seconds)
    minute_local = local_dt.hour * 60 + local_dt.minute
    breakdown = compute_sleep_pressure_breakdown(
        policy=policy,
        profile=profile,
        sleep_debt_min=hs["sleep_debt_min"],
        physical_fatigue=hs["physical_fatigue"],
        minute_local=minute_local,
        elapsed_since_last_main_sleep_end_min=elapsed // 60,
        total_nap_awake_credit_min=total_nap_awake_credit_min,
    )
    return breakdown.total


def _homeostatic_knot_offsets(
    *,
    policy: Mapping[str, Any],
    profile: SyntheticSleepProfile,
    elapsed_since_main_end_seconds: int,
    total_nap_awake_credit_min: int,
    horizon_seconds: int,
) -> list[int]:
    """Offsets where awake_fraction_permille crosses homeostatic knot x values."""
    pol = require_v2_production_format(policy)
    knots = pol["sleep_policy"]["homeostatic_awake_fraction_knots"]
    xs = sorted({_require_int(k["x"], "knot.x") for k in knots})
    nominal = MINUTES_PER_DAY - profile.sleep_need_min
    if nominal <= 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "nominal_awake_span_min must be > 0")
    out: list[int] = []
    for x in xs:
        # Find earliest t where (eff_min * 1000) // nominal >= x,
        # eff_min = max(0, (elapsed0+t)//60 - credit)
        # Seek smallest t in 1..horizon with fraction >= x (and start may already be past).
        # Closed form: need eff_min >= ceil_div(x * nominal, 1000) roughly via:
        # (eff * 1000) // nominal >= x  ⇒  eff * 1000 >= x * nominal  ⇒ eff >= ceil(x*nominal/1000)
        need_eff = (x * nominal + 999) // 1000
        need_elapsed_min = need_eff + total_nap_awake_credit_min
        need_elapsed_sec = need_elapsed_min * 60
        if need_elapsed_sec <= elapsed_since_main_end_seconds:
            continue
        t = need_elapsed_sec - elapsed_since_main_end_seconds
        if 0 < t <= horizon_seconds:
            out.append(t)
    return out


def _fatigue_knot_offsets(
    *,
    policy: Mapping[str, Any],
    human_state: Mapping[str, int],
    rate_remainders: Mapping[str, int],
    activity_context: Mapping[str, Any],
    horizon_seconds: int,
) -> list[int]:
    pol = require_v2_production_format(policy)
    rates = resolve_v2_rates(pol, activity_context)
    rate = rates["physical_fatigue"]
    if rate == 0:
        return []
    knots = pol["sleep_policy"]["fatigue_contribution_knots"]
    xs = sorted({_require_int(k["x"], "fatigue.knot.x") for k in knots})
    field = human_state["physical_fatigue"]
    rem = rate_remainders["physical_fatigue"]
    out: list[int] = []
    for x in xs:
        if rate > 0:
            kind, secs = exact_threshold_crossing_eta_seconds(
                field_value=field,
                remainder=rem,
                rate_per_hour=rate,
                threshold=x,
                direction="UP",
            )
        else:
            kind, secs = exact_threshold_crossing_eta_seconds(
                field_value=field,
                remainder=rem,
                rate_per_hour=rate,
                threshold=x,
                direction="DOWN",
            )
        if kind == "FUTURE" and secs is not None and 0 < secs <= horizon_seconds:
            out.append(secs)
    return out


def _circadian_anchor_offsets(
    *,
    now_dt: datetime,
    profile: SyntheticSleepProfile,
    policy: Mapping[str, Any],
    horizon_seconds: int,
) -> list[int]:
    nxt = next_circadian_anchor_after(now=now_dt, profile=profile, policy=policy)
    if nxt is None:
        return []
    offsets: list[int] = []
    cursor = now_dt
    # Collect finite anchors within horizon (at most a few days of unique minutes).
    for _ in range(32):
        nxt = next_circadian_anchor_after(now=cursor, profile=profile, policy=policy)
        if nxt is None:
            break
        nxt_dt = parse_rfc3339(nxt, field="circadian_anchor")
        t = int((nxt_dt - now_dt).total_seconds())
        if t <= 0:
            cursor = nxt_dt + timedelta(seconds=1)
            continue
        if t > horizon_seconds:
            break
        offsets.append(t)
        cursor = nxt_dt
    return offsets


def _component_monotonic_flags(
    *,
    policy: Mapping[str, Any],
    profile: SyntheticSleepProfile,
    activity_context: Mapping[str, Any],
    now_dt: datetime,
    t0: int,
    t1: int,
    circ_offsets: Sequence[int],
) -> bool:
    """True iff all sleep-pressure components are provably nondecreasing on [t0, t1]."""
    if t1 < t0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "bad monotonic interval")
    pol = require_v2_production_format(policy)
    rates = resolve_v2_rates(pol, activity_context)

    fat_rate = rates["physical_fatigue"]
    fat_knots = pol["sleep_policy"]["fatigue_contribution_knots"]
    for i in range(1, len(fat_knots)):
        if _require_int(fat_knots[i]["y"], "y") < _require_int(fat_knots[i - 1]["y"], "y"):
            return False
    if fat_rate < 0:
        return False

    def bias_at(offset: int) -> int:
        local = now_dt + timedelta(seconds=offset)
        return circadian_bias_at_minute(local.hour * 60 + local.minute, profile, pol)

    anchors = [t0] + [o for o in circ_offsets if t0 < o < t1] + [t1]
    prev_b = bias_at(anchors[0])
    for a in anchors[1:]:
        b = bias_at(a)
        if b < prev_b:
            return False
        prev_b = b
    return True


def _binary_search_crossing(
    score_fn,
    *,
    t0: int,
    t1: int,
    threshold: int,
) -> int | None:
    """Earliest t in (t0, t1] with score(t) >= threshold and score(t-1) < threshold.

    Precondition: score nondecreasing on [t0, t1]; score(t0) < threshold <= score(t1).
    """
    if t1 <= t0:
        return None
    s0 = score_fn(t0)
    s1 = score_fn(t1)
    if s0 >= threshold:
        return None  # already at start — caller handles immediate
    if s1 < threshold:
        return None
    lo, hi = t0 + 1, t1
    while lo < hi:
        mid = (lo + hi) // 2
        if score_fn(mid) >= threshold:
            hi = mid
        else:
            lo = mid + 1
    t = lo
    # Verify §27: t-1 < threshold and t >= threshold
    if score_fn(t) < threshold:
        return None
    if t > t0 and score_fn(t - 1) >= threshold:
        return None
    return t


def project_sleep_pressure_candidates(
    *,
    policy: Mapping[str, Any],
    human_state: Mapping[str, Any],
    rate_remainders: Mapping[str, Any],
    activity_context: Mapping[str, Any],
    now: datetime | str,
    wakeup_context: DecisionWakeupContext | Mapping[str, Any],
    sleep_profile: Mapping[str, Any] | SyntheticSleepProfile,
    elapsed_since_last_main_sleep_end_seconds: int,
    total_nap_awake_credit_min: int,
    projection_horizon_end: datetime | str,
) -> tuple[list[str], list[FutureCandidate], str | None]:
    """Project SLEEP_PRESSURE HIGH/CRITICAL wakeups or safe circadian/idle fallback.

    ``sleep_profile``, ``elapsed_since_last_main_sleep_end_seconds``, and
    ``total_nap_awake_credit_min`` are required (no silent synthetic defaults).
    """
    pol = require_v2_production_format(policy)
    hs = validate_human_state(human_state)
    rem = validate_rate_remainders(rate_remainders)
    ctx = validate_decision_wakeup_context(wakeup_context)
    if sleep_profile is None:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "sleep_profile is required (no silent default_synthetic_sleep_profile)",
        )
    prof = validate_synthetic_sleep_profile(sleep_profile)
    now_dt = _as_dt(now, field="now")
    horizon_dt = _as_dt(projection_horizon_end, field="projection_horizon_end")
    if horizon_dt <= now_dt:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "projection_horizon_end must be after now")
    horizon_seconds = int((horizon_dt - now_dt).total_seconds())
    elapsed0 = _require_int(
        elapsed_since_last_main_sleep_end_seconds, "elapsed_since_last_main_sleep_end_seconds"
    )
    credit = _require_int(total_nap_awake_credit_min, "total_nap_awake_credit_min")
    if elapsed0 < 0 or credit < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "sleep projection timing must be >= 0")

    immediate: list[str] = []
    future: list[FutureCandidate] = []
    mode: str | None = None

    if ctx.is_sleeping or not _threshold_relevant("SLEEP_PRESSURE", "HIGH", ctx):
        return immediate, future, mode

    sp_bands = pol["derived_bands"]["sleep_pressure"]
    targets = (
        ("HIGH", _require_int(sp_bands["HIGH"], "HIGH")),
        ("CRITICAL", _require_int(sp_bands["CRITICAL"], "CRITICAL")),
    )

    score_cache: dict[int, int] = {}

    def score_at(offset: int) -> int:
        if offset in score_cache:
            return score_cache[offset]
        value = _sleep_pressure_at_offset(
            policy=pol,
            profile=prof,
            human_state=hs,
            rate_remainders=rem,
            activity_context=activity_context,
            elapsed_since_main_end_seconds=elapsed0,
            total_nap_awake_credit_min=credit,
            now_dt=now_dt,
            offset_seconds=offset,
        )
        score_cache[offset] = value
        return value

    # Precompute circadian anchor offsets once for this projection.
    circ_offsets = _circadian_anchor_offsets(
        now_dt=now_dt, profile=prof, policy=pol, horizon_seconds=horizon_seconds
    )

    score_now = score_at(0)
    for band, thr in targets:
        if not _threshold_relevant("SLEEP_PRESSURE", band, ctx):
            continue
        if score_now >= thr:
            immediate.append(_threshold_reason_code("SLEEP_PRESSURE", band, "UP"))

    # Structural partition points
    bounds = sorted(
        set(
            [0, horizon_seconds]
            + circ_offsets
            + _homeostatic_knot_offsets(
                policy=pol,
                profile=prof,
                elapsed_since_main_end_seconds=elapsed0,
                total_nap_awake_credit_min=credit,
                horizon_seconds=horizon_seconds,
            )
            + _fatigue_knot_offsets(
                policy=pol,
                human_state=hs,
                rate_remainders=rem,
                activity_context=activity_context,
                horizon_seconds=horizon_seconds,
            )
        )
    )
    bounds = [b for b in bounds if 0 <= b <= horizon_seconds]

    exact_found = False
    uncertain = False
    for band, thr in targets:
        if score_now >= thr:
            continue  # immediate already recorded; no due_at=now
        if not _threshold_relevant("SLEEP_PRESSURE", band, ctx):
            continue
        for i in range(len(bounds) - 1):
            t0, t1 = bounds[i], bounds[i + 1]
            if t1 <= t0:
                continue
            if not _component_monotonic_flags(
                policy=pol,
                profile=prof,
                activity_context=activity_context,
                now_dt=now_dt,
                t0=t0,
                t1=t1,
                circ_offsets=circ_offsets,
            ):
                uncertain = True
                continue
            s0 = score_at(t0)
            s1 = score_at(t1)
            if s0 < thr <= s1:
                t = _binary_search_crossing(score_at, t0=t0, t1=t1, threshold=thr)
                if t is not None and t > 0:
                    due = format_rfc3339(now_dt + timedelta(seconds=t))
                    future.append(
                        FutureCandidate(
                            due_at=due,
                            trigger_kind="THRESHOLD",
                            decision_key=_threshold_decision_key("SLEEP_PRESSURE", band, "UP"),
                            reason_code=_threshold_reason_code("SLEEP_PRESSURE", band, "UP"),
                            metric="SLEEP_PRESSURE",
                            target_band=band,
                            direction="UP",
                        )
                    )
                    exact_found = True
                    mode = "EXACT"
                    break
        if exact_found and band == "HIGH":
            # Still allow CRITICAL search in later code path; continue loop.
            pass

    if not exact_found and uncertain:
        # Safe fallback only when monotonicity cannot be proven (§28).
        nxt = next_circadian_anchor_after(now=now_dt, profile=prof, policy=pol)
        if nxt is not None:
            nxt_dt = parse_rfc3339(nxt, field="circadian")
            if now_dt < nxt_dt <= horizon_dt:
                future.append(
                    FutureCandidate(
                        due_at=nxt,
                        trigger_kind="CIRCADIAN_REASSESSMENT",
                        decision_key="circadian:next_anchor",
                        reason_code="CIRCADIAN_NEXT_ANCHOR",
                        metric=None,
                        target_band=None,
                        direction=None,
                    )
                )
                mode = "CIRCADIAN_FALLBACK"
            else:
                mode = "IDLE_FALLBACK"
        else:
            mode = "IDLE_FALLBACK"

    return immediate, future, mode


def _precedence_rank(candidate: FutureCandidate) -> int:
    if candidate.trigger_kind == "THRESHOLD":
        key = (candidate.metric or "", candidate.target_band or "")
        if key in _URGENT_THRESHOLD:
            return 2
        if key in _NONURGENT_THRESHOLD:
            return 6
        return 6
    return _PRECEDENCE_RANK.get(candidate.trigger_kind, 99)


def select_next_decision_wakeup(
    candidates: Sequence[FutureCandidate],
) -> FutureCandidate | None:
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda c: (
            parse_rfc3339(c.due_at, field="due_at"),
            _precedence_rank(c),
            c.decision_key,
        ),
    )[0]


def apply_non_interruptible_deferral(
    candidates: Sequence[FutureCandidate],
    *,
    wakeup_context: DecisionWakeupContext,
    now: datetime | str,
) -> list[FutureCandidate]:
    ctx = validate_decision_wakeup_context(wakeup_context)
    if ctx.active_interruptibility != "NON_INTERRUPTIBLE":
        return list(candidates)
    if ctx.next_legal_reassessment_at is None:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "NON_INTERRUPTIBLE requires next_legal_reassessment_at",
        )
    legal = parse_rfc3339(ctx.next_legal_reassessment_at, field="next_legal_reassessment_at")
    now_dt = _as_dt(now, field="now")
    if legal < now_dt:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "next_legal_reassessment_at before now")

    out: list[FutureCandidate] = []
    needs_active = False
    for c in candidates:
        if c.trigger_kind == "THRESHOLD":
            due = parse_rfc3339(c.due_at, field="due_at")
            if due < legal:
                needs_active = True
                continue
        out.append(c)
    if needs_active:
        out.append(
            FutureCandidate(
                due_at=ctx.next_legal_reassessment_at,
                trigger_kind="ACTIVE_REASSESSMENT",
                decision_key="active_reassessment:legal_boundary",
                reason_code="ACTIVE_REASSESSMENT_LEGAL_BOUNDARY",
                metric=None,
                target_band=None,
                direction=None,
            )
        )
    # Drop other known ACTIVE_REASSESSMENT duplicates at same key if any
    return out


def build_idle_candidate(
    *,
    policy: Mapping[str, Any],
    now: datetime | str,
    wakeup_context: DecisionWakeupContext | Mapping[str, Any],
) -> FutureCandidate | None:
    """Awake IDLE ceiling at now+policy max, only for NONE/REASSESSABLE."""
    pol = require_v2_production_format(policy)
    ctx = validate_decision_wakeup_context(wakeup_context)
    if ctx.is_sleeping:
        return None
    if ctx.active_interruptibility not in {"NONE", "REASSESSABLE"}:
        return None
    max_min = _require_int(
        pol["decision_thresholds"]["awake_idle_reassessment_max_min"],
        "awake_idle_reassessment_max_min",
    )
    if max_min < 1:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "awake_idle_reassessment_max_min must be >= 1")
    now_dt = _as_dt(now, field="now")
    due = format_rfc3339(now_dt + timedelta(minutes=max_min))
    return FutureCandidate(
        due_at=due,
        trigger_kind="IDLE",
        decision_key="idle:awake_max_gap",
        reason_code="IDLE_AWAKE_MAX_GAP",
        metric=None,
        target_band=None,
        direction=None,
    )


def build_v2_decision_wakeup_event(
    *,
    character_id: str,
    candidate: FutureCandidate,
) -> dict[str, Any]:
    if not character_id:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "character_id required")
    if candidate.trigger_kind not in TRIGGER_KINDS:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE, f"invalid trigger_kind: {candidate.trigger_kind!r}"
        )
    if candidate.trigger_kind == "THRESHOLD":
        assert_allowed_v2_threshold_tuple(
            candidate.metric, candidate.target_band, candidate.direction
        )
    else:
        if candidate.metric is not None or candidate.target_band is not None or candidate.direction is not None:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "non-THRESHOLD FutureCandidate must have null metric/target_band/direction",
            )
    due = require_canonical_timestamp(candidate.due_at, field="due_at")
    event_id = stable_id("v2-decision-wakeup", character_id, candidate.decision_key, due)
    event = {
        "schema_version": 2,
        "event_id": event_id,
        "event_kind": "DECISION_WAKEUP",
        "due_at": due,
        "priority": V2_DECISION_WAKEUP_PRIORITY,
        "payload": {
            "trigger_kind": candidate.trigger_kind,
            "decision_key": candidate.decision_key,
            "reason_code": candidate.reason_code,
            "metric": candidate.metric,
            "target_band": candidate.target_band,
            "direction": candidate.direction,
            "source_kind": candidate.source_kind,
            "source_ref": candidate.source_ref,
        },
    }
    validate_instance(event, "queued_internal_event")
    return event


def project_decision_wakeups(
    *,
    policy: Mapping[str, Any],
    character_id: str,
    human_state: Mapping[str, Any],
    rate_remainders: Mapping[str, Any],
    activity_context: Mapping[str, Any],
    now: datetime | str,
    wakeup_context: DecisionWakeupContext | Mapping[str, Any],
    sleep_profile: Mapping[str, Any] | SyntheticSleepProfile,
    elapsed_since_last_main_sleep_end_seconds: int,
    total_nap_awake_credit_min: int,
    known_boundaries: Sequence[KnownDecisionBoundary | Mapping[str, Any]] = (),
    projection_horizon_end: datetime | str | None = None,
) -> ProjectionResult:
    """Full pure projection: immediate reasons + future candidates + selected next.

    Sleep-pressure inputs are required; no silent synthetic profile / zero elapsed defaults.
    """
    reject_binary_floats({"human_state": dict(human_state), "remainders": dict(rate_remainders)})
    if sleep_profile is None:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "sleep_profile is required (no silent default_synthetic_sleep_profile)",
        )
    ctx = validate_decision_wakeup_context(wakeup_context)
    now_s = _as_canon(now, field="now")
    now_dt = parse_rfc3339(now_s, field="now")
    if projection_horizon_end is None:
        # Default horizon: idle max + 1 day structural room.
        pol = require_v2_production_format(policy)
        idle_min = _require_int(
            pol["decision_thresholds"]["awake_idle_reassessment_max_min"],
            "awake_idle_reassessment_max_min",
        )
        horizon = now_dt + timedelta(minutes=idle_min + MINUTES_PER_DAY)
    else:
        horizon = _as_dt(projection_horizon_end, field="projection_horizon_end")

    imm_h, fut_h = project_human_state_threshold_candidates(
        policy=policy,
        human_state=human_state,
        rate_remainders=rate_remainders,
        activity_context=activity_context,
        now=now_dt,
        wakeup_context=ctx,
    )
    imm_s, fut_s, sleep_mode = project_sleep_pressure_candidates(
        policy=policy,
        human_state=human_state,
        rate_remainders=rate_remainders,
        activity_context=activity_context,
        now=now_dt,
        wakeup_context=ctx,
        sleep_profile=sleep_profile,
        elapsed_since_last_main_sleep_end_seconds=elapsed_since_last_main_sleep_end_seconds,
        total_nap_awake_credit_min=total_nap_awake_credit_min,
        projection_horizon_end=horizon,
    )

    known: list[FutureCandidate] = []
    seen_keys: set[str] = set()
    for raw in known_boundaries:
        b = validate_known_decision_boundary(raw, now=now_dt)
        if b.decision_key in seen_keys:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, f"duplicate decision_key: {b.decision_key}")
        seen_keys.add(b.decision_key)
        known.append(
            FutureCandidate(
                due_at=b.due_at,
                trigger_kind=b.trigger_kind,
                decision_key=b.decision_key,
                reason_code=b.reason_code,
                metric=None,
                target_band=None,
                direction=None,
                source_kind=b.source_kind,
                source_ref=b.source_ref,
            )
        )

    candidates = list(fut_h) + list(fut_s) + known
    candidates = apply_non_interruptible_deferral(candidates, wakeup_context=ctx, now=now_dt)

    # Awake IDLE is a real ceiling candidate (now+max), not only when the list is empty.
    idle = build_idle_candidate(policy=policy, now=now_dt, wakeup_context=ctx)
    if idle is not None:
        candidates.append(idle)

    selected = select_next_decision_wakeup(candidates)
    immediate = tuple(dict.fromkeys(imm_h + imm_s))  # stable unique
    return ProjectionResult(
        immediate_reassessment_reasons=immediate,
        future_candidates=tuple(candidates),
        selected=selected,
        sleep_projection_mode=sleep_mode,
    )


def reconcile_v2_decision_wakeups(
    events: Sequence[Mapping[str, Any]],
    *,
    character_id: str,
    policy: Mapping[str, Any],
    human_state: Mapping[str, Any],
    rate_remainders: Mapping[str, Any],
    activity_context: Mapping[str, Any],
    now: datetime | str,
    wakeup_context: DecisionWakeupContext | Mapping[str, Any],
    sleep_profile: Mapping[str, Any] | SyntheticSleepProfile,
    elapsed_since_last_main_sleep_end_seconds: int,
    total_nap_awake_credit_min: int,
    known_boundaries: Sequence[KnownDecisionBoundary | Mapping[str, Any]] = (),
    projection_horizon_end: datetime | str | None = None,
) -> tuple[list[dict[str, Any]], ProjectionResult]:
    """Pure queue reconcile: preserve non-managed; replace managed v2 DECISION_WAKEUP ≤1."""
    preserved: list[dict[str, Any]] = []
    for e in events:
        if is_managed_v2_decision_wakeup(e):
            continue
        preserved.append(deepcopy(dict(e)))

    projection = project_decision_wakeups(
        policy=policy,
        character_id=character_id,
        human_state=human_state,
        rate_remainders=rate_remainders,
        activity_context=activity_context,
        now=now,
        wakeup_context=wakeup_context,
        known_boundaries=known_boundaries,
        sleep_profile=sleep_profile,
        elapsed_since_last_main_sleep_end_seconds=elapsed_since_last_main_sleep_end_seconds,
        total_nap_awake_credit_min=total_nap_awake_credit_min,
        projection_horizon_end=projection_horizon_end,
    )

    out = preserved
    if projection.selected is not None:
        new_event = build_v2_decision_wakeup_event(
            character_id=character_id, candidate=projection.selected
        )
        ids = {e["event_id"] for e in out}
        if new_event["event_id"] in ids:
            raise LifeEngineError(ErrorCode.DUPLICATE_ID, new_event["event_id"])
        out = out + [new_event]

    out.sort(key=queue_event_sort_key)
    managed = [e for e in out if is_managed_v2_decision_wakeup(e)]
    if len(managed) > 1:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "managed v2 DECISION_WAKEUP > 1")
    return out, projection
