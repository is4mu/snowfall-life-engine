"""Life Engine v2 Slice 2D — categorical action candidates & pure resolver.

Pure deterministic selection among already-causal eligible candidates.
Does not start/end activities, mutate CurrentState/queue, or wire into clock.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .derived import (
    classify_hunger_band,
    classify_physical_fatigue_band,
    classify_sleep_pressure_band,
)
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .ids import stable_id
from .rng import SeedLike, keyed_digest, u01
from .timeutil import parse_rfc3339, require_canonical_timestamp
from .wakeups import ALLOWED_V2_THRESHOLD_TUPLES, TRIGGER_KINDS

# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------

# Sentinel: omitted caller-supplied feasibility must not become True/NORMAL.
class _UnsetType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _UnsetType()

SELECTION_TIERS: tuple[str, ...] = (
    "HARD_COMMITMENT",
    "URGENT_BIOLOGICAL",
    "DEADLINE_TASK",
    "SOCIAL_PROMISE",
    "ROUTINE_HABIT",
    "RESTORATIVE",
    "LEISURE_SPONTANEOUS",
)

OPPORTUNITY_CLASSES = frozenset(
    {
        "HARD_COMMITMENT",
        "DEADLINE_TASK",
        "SOCIAL_PROMISE",
        "ROUTINE_HABIT",
        "RESTORATIVE",
        "LEISURE_SPONTANEOUS",
    }
)

ACTION_KINDS = frozenset(
    {
        "CONTINUE_CURRENT",
        "HARD_COMMITMENT_ACTIVITY",
        "TRAVEL_TO_COMMITMENT",
        "MEAL",
        "SLEEP_MAIN",
        "REST",
        "STUDY",
        "WORK",
        "KICKBOXING",
        "HOUSEHOLD",
        "ERRAND",
        "SOCIAL_PROMISE",
        "SOCIAL_CONTACT",
        "PASSIVE_HOME_MEDIA",
        "MUSIC",
        "MOVIE",
        "LOCAL_WALK",
        "VINTAGE_BROWSING",
        "UNSTRUCTURED_REST",
    }
)

SOURCE_KINDS = frozenset(
    {
        "CURRENT_ACTIVITY",
        "BIOLOGICAL",
        "COMMITMENT",
        "TASK",
        "SOCIAL",
        "ROUTINE",
        "LEISURE_WINDOW",
    }
)

# priority_tier → allowed source_kind(s) (necessary but not sufficient)
_TIER_SOURCE_COMPAT: dict[str, frozenset[str]] = {
    "HARD_COMMITMENT": frozenset({"COMMITMENT"}),
    "URGENT_BIOLOGICAL": frozenset({"BIOLOGICAL"}),
    "DEADLINE_TASK": frozenset({"TASK"}),
    "SOCIAL_PROMISE": frozenset({"COMMITMENT", "SOCIAL"}),
    "ROUTINE_HABIT": frozenset({"TASK", "ROUTINE", "COMMITMENT"}),
    "RESTORATIVE": frozenset({"BIOLOGICAL", "ROUTINE"}),
    "LEISURE_SPONTANEOUS": frozenset({"LEISURE_WINDOW"}),
}

# Action-specific causal source: (priority_tier, source_kind) pairs.
# Tier-level compatibility alone is insufficient (review-1 blocker 2).
_ACTION_TIER_SOURCE: dict[str, frozenset[tuple[str, str]]] = {
    "STUDY": frozenset(
        {
            ("DEADLINE_TASK", "TASK"),
            ("ROUTINE_HABIT", "TASK"),
        }
    ),
    "WORK": frozenset(
        {
            ("HARD_COMMITMENT", "COMMITMENT"),
            ("ROUTINE_HABIT", "COMMITMENT"),
        }
    ),
    "ERRAND": frozenset(
        {
            ("DEADLINE_TASK", "TASK"),
            ("ROUTINE_HABIT", "TASK"),
            ("ROUTINE_HABIT", "COMMITMENT"),
        }
    ),
    "HOUSEHOLD": frozenset(
        {
            ("ROUTINE_HABIT", "TASK"),
            ("ROUTINE_HABIT", "ROUTINE"),
            ("DEADLINE_TASK", "TASK"),
        }
    ),
    "SOCIAL_PROMISE": frozenset(
        {
            ("SOCIAL_PROMISE", "SOCIAL"),
            ("SOCIAL_PROMISE", "COMMITMENT"),
        }
    ),
    "SOCIAL_CONTACT": frozenset(
        {
            ("SOCIAL_PROMISE", "SOCIAL"),
            ("SOCIAL_PROMISE", "COMMITMENT"),
        }
    ),
    "KICKBOXING": frozenset(
        {
            ("ROUTINE_HABIT", "ROUTINE"),
            ("HARD_COMMITMENT", "COMMITMENT"),
        }
    ),
    "HARD_COMMITMENT_ACTIVITY": frozenset({("HARD_COMMITMENT", "COMMITMENT")}),
    # Slice 2E: travel inherits destination commitment tier (HARD / SOCIAL / ROUTINE).
    "TRAVEL_TO_COMMITMENT": frozenset(
        {
            ("HARD_COMMITMENT", "COMMITMENT"),
            ("SOCIAL_PROMISE", "COMMITMENT"),
            ("ROUTINE_HABIT", "COMMITMENT"),
        }
    ),
    "MEAL": frozenset(
        {
            ("URGENT_BIOLOGICAL", "BIOLOGICAL"),
            ("RESTORATIVE", "BIOLOGICAL"),
        }
    ),
    "REST": frozenset(
        {
            ("URGENT_BIOLOGICAL", "BIOLOGICAL"),
            ("RESTORATIVE", "BIOLOGICAL"),
            ("RESTORATIVE", "ROUTINE"),
        }
    ),
    "SLEEP_MAIN": frozenset(
        {
            ("URGENT_BIOLOGICAL", "BIOLOGICAL"),
            ("RESTORATIVE", "BIOLOGICAL"),
        }
    ),
    "PASSIVE_HOME_MEDIA": frozenset({("LEISURE_SPONTANEOUS", "LEISURE_WINDOW")}),
    "MUSIC": frozenset({("LEISURE_SPONTANEOUS", "LEISURE_WINDOW")}),
    "MOVIE": frozenset({("LEISURE_SPONTANEOUS", "LEISURE_WINDOW")}),
    "LOCAL_WALK": frozenset({("LEISURE_SPONTANEOUS", "LEISURE_WINDOW")}),
    "VINTAGE_BROWSING": frozenset({("LEISURE_SPONTANEOUS", "LEISURE_WINDOW")}),
    "UNSTRUCTURED_REST": frozenset({("LEISURE_SPONTANEOUS", "LEISURE_WINDOW")}),
}

SOURCE_LESS_FORBIDDEN = frozenset(
    {
        "STUDY",
        "WORK",
        "ERRAND",
        "HOUSEHOLD",
        "SOCIAL_PROMISE",
        "SOCIAL_CONTACT",
        "KICKBOXING",
    }
)

# Slice 2C threshold metric/band pairs (direction ignored for DecisionBoundaryFact).
_ALLOWED_THRESHOLD_METRIC_BANDS = frozenset(
    (metric, band) for metric, band, _direction in ALLOWED_V2_THRESHOLD_TUPLES
)

SOFT_GUARD_REASON_CLASS_V1 = "CAPACITY"

REJECTION_REASONS = frozenset(
    {
        "MISSING_CAUSAL_SOURCE",
        "TIME_INFEASIBLE",
        "LOCATION_INFEASIBLE",
        "PHYSICAL_INFEASIBLE",
        "DOMAIN_GUARD_BLOCKED",
        "SOFT_RECONSIDER_GUARD",
        "RECENT_MEAL_GUARD",
    }
)

INTERRUPTIBILITY = frozenset({"NON_INTERRUPTIBLE", "REASSESSABLE"})
PRIOR_OUTCOMES = frozenset({"DECLINED", "DEFERRED"})
MEAL_EXTENTS = frozenset({"LIGHT", "STANDARD"})
SLEEP_OPPORTUNITIES = frozenset({"BLOCKED", "NORMAL", "FAVORABLE"})
RESULT_KINDS = frozenset({"CONTINUE_CURRENT", "SELECT_CANDIDATE", "NO_ELIGIBLE_ACTION"})

SHORT_LEISURE_CATEGORIES = frozenset(
    {"PASSIVE_HOME_MEDIA", "MUSIC", "UNSTRUCTURED_REST"}
)

LEISURE_ACTION_KINDS = frozenset(
    {
        "PASSIVE_HOME_MEDIA",
        "MUSIC",
        "MOVIE",
        "LOCAL_WALK",
        "VINTAGE_BROWSING",
        "UNSTRUCTURED_REST",
    }
)

RNG_NAMESPACE = "decision/action-choice"
CANDIDATE_ID_NAMESPACE = "action-candidate"

SELECTION_MODES = frozenset(
    {"single", "ranked_deterministic", "keyed_tie", "min_dwell"}
)


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _require_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} must be int (no bool/float)")
    return value


def _require_optional_int(value: object, label: str) -> int | None:
    if value is None:
        return None
    return _require_int(value, label)


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} must be bool (no int)")
    return value


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} must be non-empty str")
    return value


def _require_optional_str(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, label)


def _canonical_sorted_strs(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def _reject_unknown_fields(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = set(data.keys()) - allowed
    if unknown:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"{label} unknown fields: {sorted(unknown)}",
        )


def _reject_floats(obj: object, path: str = "$") -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, float):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"binary float forbidden at {path}")
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            _reject_floats(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_floats(v, f"{path}[{i}]")


def _minutes_between(earlier: str, later: str, *, field: str) -> int:
    a = parse_rfc3339(earlier, field=f"{field}.earlier")
    b = parse_rfc3339(later, field=f"{field}.later")
    delta = b - a
    return int(delta.total_seconds()) // 60


def _assert_not_after(ts: str, now: str, *, field: str) -> None:
    """Reject timestamps strictly after decision now (no negative-elapsed fail-open)."""
    a = parse_rfc3339(ts, field=field)
    b = parse_rfc3339(now, field="now")
    if a > b:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"{field} must not be after now ({ts} > {now})",
        )


def _decision_thresholds(policy: Mapping[str, Any]) -> dict[str, int]:
    pol = require_v2_production_format(policy)
    thr = pol["decision_thresholds"]
    if not isinstance(thr, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "decision_thresholds must be a mapping")
    required = (
        "reassessable_min_dwell_min",
        "soft_reconsider_guard_min",
        "near_equal_margin_permille",
    )
    out: dict[str, int] = {}
    for key in required:
        if key not in thr:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"decision_thresholds missing required key: {key}",
            )
        out[key] = _require_int(thr[key], key)
    return out


def _leisure_window_bounds(policy: Mapping[str, Any]) -> tuple[int, int]:
    """Return (short_gap_max_min, short_candidate_gap_max_min) from planning_policy."""
    pol = require_v2_production_format(policy)
    planning = pol["planning_policy"]
    short_gap = _require_int(planning["short_gap_max_min"], "short_gap_max_min")
    short_cand = _require_int(
        planning["short_candidate_gap_max_min"], "short_candidate_gap_max_min"
    )
    if short_gap < 0 or short_cand < short_gap:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "invalid leisure window bounds")
    return short_gap, short_cand


def _require_preference_permille(value: object, label: str) -> int | None:
    pref = _require_optional_int(value, label)
    if pref is not None and (pref < 0 or pref > 1000):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"{label} out of range 0..1000")
    return pref


def _meal_recent_guard_min(extent: str, policy: Mapping[str, Any]) -> int:
    pol = require_v2_production_format(policy)
    table = pol["meal_policy"]["recent_guard_min"]
    if extent not in table:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown meal_extent: {extent}")
    return _require_int(table[extent], f"meal_policy.recent_guard_min.{extent}")


def _policy_leisure_categories(policy: Mapping[str, Any]) -> frozenset[str]:
    pol = require_v2_production_format(policy)
    cats = pol["domain_policy"]["leisure_categories"]
    if not isinstance(cats, list):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "leisure_categories must be list")
    out: list[str] = []
    for c in cats:
        out.append(_require_str(c, "leisure_category"))
    return frozenset(out)


def candidate_id_for(
    *,
    character_id: str,
    decision_key: str,
    candidate_key: str,
) -> str:
    """Stable candidate identity (no queue index / heartbeat / UUID / wall-clock)."""
    return stable_id(
        CANDIDATE_ID_NAMESPACE,
        _require_str(character_id, "character_id"),
        _require_str(decision_key, "decision_key"),
        _require_str(candidate_key, "candidate_key"),
    )


def _assert_tier_source_action(
    *,
    action_kind: str,
    priority_tier: str,
    source_kind: str,
) -> None:
    if action_kind not in ACTION_KINDS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown action_kind: {action_kind}")
    if source_kind not in SOURCE_KINDS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown source_kind: {source_kind}")
    if action_kind == "CONTINUE_CURRENT":
        if priority_tier not in SELECTION_TIERS:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"CONTINUE_CURRENT priority_tier must be a selection tier: {priority_tier}",
            )
        if source_kind != "CURRENT_ACTIVITY":
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "CONTINUE_CURRENT requires source_kind=CURRENT_ACTIVITY",
            )
        return
    if priority_tier not in SELECTION_TIERS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown priority_tier: {priority_tier}")
    allowed_sources = _TIER_SOURCE_COMPAT[priority_tier]
    if source_kind not in allowed_sources:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"incompatible tier/source: {priority_tier}/{source_kind}",
        )
    action_pairs = _ACTION_TIER_SOURCE.get(action_kind)
    if action_pairs is None:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"missing action causal-source contract: {action_kind}",
        )
    if (priority_tier, source_kind) not in action_pairs:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"incompatible action/tier/source: {action_kind}/{priority_tier}/{source_kind}",
        )


def _assert_underlying_active_causal(
    *,
    action_kind: str,
    priority_tier: str,
    source_kind: str,
) -> None:
    """Validate active activity action/source/tier before CONTINUE may inherit the tier."""
    if action_kind == "CONTINUE_CURRENT":
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "active action_kind must be the underlying activity kind",
        )
    _assert_tier_source_action(
        action_kind=action_kind,
        priority_tier=priority_tier,
        source_kind=source_kind,
    )


# ---------------------------------------------------------------------------
# Transient structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ActionCandidate:
    candidate_key: str
    candidate_id: str
    action_kind: str
    priority_tier: str
    source_kind: str
    source_ref: str
    soft_candidate: bool
    time_feasible: bool
    location_feasible: bool
    physical_feasible: bool
    domain_guard_satisfied: bool
    local_preference_permille: int | None
    activity_instance_id: str | None
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "candidate_id": self.candidate_id,
            "action_kind": self.action_kind,
            "priority_tier": self.priority_tier,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "soft_candidate": self.soft_candidate,
            "time_feasible": self.time_feasible,
            "location_feasible": self.location_feasible,
            "physical_feasible": self.physical_feasible,
            "domain_guard_satisfied": self.domain_guard_satisfied,
            "local_preference_permille": self.local_preference_permille,
            "activity_instance_id": self.activity_instance_id,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class ResolvedOpportunity:
    opportunity_key: str
    opportunity_class: str
    action_kind: str
    source_kind: str
    source_ref: str
    soft_candidate: bool
    time_feasible: bool
    location_feasible: bool
    physical_feasible: bool
    domain_guard_satisfied: bool
    local_preference_permille: int | None
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class ActiveDecisionContext:
    activity_instance_id: str
    action_kind: str
    actual_start: str
    interruptibility: str
    current_priority_tier: str
    continuation_feasible: bool
    local_preference_permille: int | None
    source_kind: str
    source_ref: str
    rule_ids: tuple[str, ...]


@dataclass(frozen=True)
class DecisionBoundaryFact:
    trigger_kind: str
    decision_key: str
    reason_code: str
    metric: str | None
    target_band: str | None
    source_ref: str | None


@dataclass(frozen=True)
class SoftDecisionGuard:
    candidate_key: str
    decided_at: str
    prior_outcome: str
    reason_class: str
    meaningful_input_changed: bool


@dataclass(frozen=True)
class RejectedCandidate:
    candidate_key: str
    candidate_id: str | None
    reason: str
    rule_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_key": self.candidate_key,
            "candidate_id": self.candidate_id,
            "reason": self.reason,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class DecisionResolution:
    result_kind: str
    decision_key: str
    selected_candidate_id: str | None
    selected_action_kind: str | None
    selected_priority_tier: str | None
    activity_instance_id: str | None
    considered_candidate_ids: tuple[str, ...]
    rejected: tuple[RejectedCandidate, ...]
    rule_ids: tuple[str, ...]
    random_key: str | None
    selected_candidate_key: str | None = None
    min_dwell_forced: bool = False
    selection_mode: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "result_kind": self.result_kind,
            "decision_key": self.decision_key,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_candidate_key": self.selected_candidate_key,
            "selected_action_kind": self.selected_action_kind,
            "selected_priority_tier": self.selected_priority_tier,
            "activity_instance_id": self.activity_instance_id,
            "considered_candidate_ids": list(self.considered_candidate_ids),
            "rejected": [r.as_dict() for r in self.rejected],
            "rule_ids": list(self.rule_ids),
            "random_key": self.random_key,
            "min_dwell_forced": self.min_dwell_forced,
            "selection_mode": self.selection_mode,
        }


# ---------------------------------------------------------------------------
# Parsers (strict transient)
# ---------------------------------------------------------------------------

_OPPORTUNITY_FIELDS = frozenset(
    {
        "opportunity_key",
        "opportunity_class",
        "action_kind",
        "source_kind",
        "source_ref",
        "soft_candidate",
        "time_feasible",
        "location_feasible",
        "physical_feasible",
        "domain_guard_satisfied",
        "local_preference_permille",
        "rule_ids",
    }
)

_ACTIVE_FIELDS = frozenset(
    {
        "activity_instance_id",
        "action_kind",
        "actual_start",
        "interruptibility",
        "current_priority_tier",
        "continuation_feasible",
        "local_preference_permille",
        "source_kind",
        "source_ref",
        "rule_ids",
    }
)

_BOUNDARY_FIELDS = frozenset(
    {
        "trigger_kind",
        "decision_key",
        "reason_code",
        "metric",
        "target_band",
        "source_ref",
    }
)

_SOFT_GUARD_FIELDS = frozenset(
    {
        "candidate_key",
        "decided_at",
        "prior_outcome",
        "reason_class",
        "meaningful_input_changed",
    }
)


def parse_resolved_opportunity(data: Mapping[str, Any] | ResolvedOpportunity) -> ResolvedOpportunity:
    if isinstance(data, ResolvedOpportunity):
        data = {
            "opportunity_key": data.opportunity_key,
            "opportunity_class": data.opportunity_class,
            "action_kind": data.action_kind,
            "source_kind": data.source_kind,
            "source_ref": data.source_ref,
            "soft_candidate": data.soft_candidate,
            "time_feasible": data.time_feasible,
            "location_feasible": data.location_feasible,
            "physical_feasible": data.physical_feasible,
            "domain_guard_satisfied": data.domain_guard_satisfied,
            "local_preference_permille": data.local_preference_permille,
            "rule_ids": list(data.rule_ids),
        }
    if not isinstance(data, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "opportunity must be a mapping")
    _reject_floats(data, "opportunity")
    _reject_unknown_fields(data, _OPPORTUNITY_FIELDS, "ResolvedOpportunity")
    opp_class = _require_str(data.get("opportunity_class"), "opportunity_class")
    if opp_class not in OPPORTUNITY_CLASSES:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown opportunity_class: {opp_class}")
    action_kind = _require_str(data.get("action_kind"), "action_kind")
    source_kind = _require_str(data.get("source_kind"), "source_kind")
    source_ref = _require_str(data.get("source_ref"), "source_ref")
    _assert_tier_source_action(
        action_kind=action_kind, priority_tier=opp_class, source_kind=source_kind
    )
    if action_kind in SOURCE_LESS_FORBIDDEN and not source_ref:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"source-less action forbidden: {action_kind}",
        )
    rule_ids_raw = data.get("rule_ids", [])
    if not isinstance(rule_ids_raw, (list, tuple)):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "rule_ids must be list")
    rule_ids = _canonical_sorted_strs([_require_str(x, "rule_id") for x in rule_ids_raw])
    pref = _require_preference_permille(
        data.get("local_preference_permille"), "local_preference_permille"
    )
    return ResolvedOpportunity(
        opportunity_key=_require_str(data.get("opportunity_key"), "opportunity_key"),
        opportunity_class=opp_class,
        action_kind=action_kind,
        source_kind=source_kind,
        source_ref=source_ref,
        soft_candidate=_require_bool(data.get("soft_candidate"), "soft_candidate"),
        time_feasible=_require_bool(data.get("time_feasible"), "time_feasible"),
        location_feasible=_require_bool(data.get("location_feasible"), "location_feasible"),
        physical_feasible=_require_bool(data.get("physical_feasible"), "physical_feasible"),
        domain_guard_satisfied=_require_bool(
            data.get("domain_guard_satisfied"), "domain_guard_satisfied"
        ),
        local_preference_permille=pref,
        rule_ids=rule_ids,
    )


def parse_active_decision_context(
    data: Mapping[str, Any] | ActiveDecisionContext | None,
    *,
    now: str | None = None,
) -> ActiveDecisionContext | None:
    if data is None:
        return None
    if isinstance(data, ActiveDecisionContext):
        data = {
            "activity_instance_id": data.activity_instance_id,
            "action_kind": data.action_kind,
            "actual_start": data.actual_start,
            "interruptibility": data.interruptibility,
            "current_priority_tier": data.current_priority_tier,
            "continuation_feasible": data.continuation_feasible,
            "local_preference_permille": data.local_preference_permille,
            "source_kind": data.source_kind,
            "source_ref": data.source_ref,
            "rule_ids": list(data.rule_ids),
        }
    if not isinstance(data, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "active context must be a mapping")
    _reject_floats(data, "active")
    _reject_unknown_fields(data, _ACTIVE_FIELDS, "ActiveDecisionContext")
    interruptibility = _require_str(data.get("interruptibility"), "interruptibility")
    if interruptibility not in INTERRUPTIBILITY:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid interruptibility: {interruptibility}")
    tier = _require_str(data.get("current_priority_tier"), "current_priority_tier")
    if tier not in SELECTION_TIERS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid current_priority_tier: {tier}")
    action_kind = _require_str(data.get("action_kind"), "action_kind")
    source_kind = _require_str(data.get("source_kind"), "source_kind")
    source_ref = _require_str(data.get("source_ref"), "source_ref")
    # Prevent CONTINUE priority spoofing: underlying action must causally own the tier.
    _assert_underlying_active_causal(
        action_kind=action_kind,
        priority_tier=tier,
        source_kind=source_kind,
    )
    if action_kind in SOURCE_LESS_FORBIDDEN and not source_ref:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"source-less action forbidden: {action_kind}",
        )
    rule_ids_raw = data.get("rule_ids", [])
    if not isinstance(rule_ids_raw, (list, tuple)):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "rule_ids must be list")
    rule_ids = _canonical_sorted_strs([_require_str(x, "rule_id") for x in rule_ids_raw])
    pref = _require_preference_permille(
        data.get("local_preference_permille"), "local_preference_permille"
    )
    actual_start = require_canonical_timestamp(data.get("actual_start"), field="actual_start")
    if now is not None:
        now_c = require_canonical_timestamp(now, field="now")
        _assert_not_after(actual_start, now_c, field="actual_start")
    return ActiveDecisionContext(
        activity_instance_id=_require_str(data.get("activity_instance_id"), "activity_instance_id"),
        action_kind=action_kind,
        actual_start=actual_start,
        interruptibility=interruptibility,
        current_priority_tier=tier,
        continuation_feasible=_require_bool(
            data.get("continuation_feasible"), "continuation_feasible"
        ),
        local_preference_permille=pref,
        source_kind=source_kind,
        source_ref=source_ref,
        rule_ids=rule_ids,
    )


def parse_decision_boundary_fact(
    data: Mapping[str, Any] | DecisionBoundaryFact,
) -> DecisionBoundaryFact:
    if isinstance(data, DecisionBoundaryFact):
        data = {
            "trigger_kind": data.trigger_kind,
            "decision_key": data.decision_key,
            "reason_code": data.reason_code,
            "metric": data.metric,
            "target_band": data.target_band,
            "source_ref": data.source_ref,
        }
    if not isinstance(data, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "boundary must be a mapping")
    _reject_floats(data, "boundary")
    _reject_unknown_fields(data, _BOUNDARY_FIELDS, "DecisionBoundaryFact")
    trigger = _require_str(data.get("trigger_kind"), "trigger_kind")
    if trigger not in TRIGGER_KINDS:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"unknown trigger_kind: {trigger}")
    metric = data.get("metric")
    target_band = data.get("target_band")
    if trigger == "THRESHOLD":
        metric_s = _require_str(metric, "metric")
        band_s = _require_str(target_band, "target_band")
        if (metric_s, band_s) not in _ALLOWED_THRESHOLD_METRIC_BANDS:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"invalid THRESHOLD metric/band: {metric_s}/{band_s}",
            )
    else:
        if metric is not None or target_band is not None:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "non-THRESHOLD DecisionBoundaryFact must have null metric/target_band",
            )
        metric_s = None
        band_s = None
    return DecisionBoundaryFact(
        trigger_kind=trigger,
        decision_key=_require_str(data.get("decision_key"), "decision_key"),
        reason_code=_require_str(data.get("reason_code"), "reason_code"),
        metric=metric_s,
        target_band=band_s,
        source_ref=_require_optional_str(data.get("source_ref"), "source_ref"),
    )


def parse_soft_decision_guard(
    data: Mapping[str, Any] | SoftDecisionGuard,
    *,
    now: str | None = None,
) -> SoftDecisionGuard:
    if isinstance(data, SoftDecisionGuard):
        data = {
            "candidate_key": data.candidate_key,
            "decided_at": data.decided_at,
            "prior_outcome": data.prior_outcome,
            "reason_class": data.reason_class,
            "meaningful_input_changed": data.meaningful_input_changed,
        }
    if not isinstance(data, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "soft guard must be a mapping")
    _reject_floats(data, "soft_guard")
    _reject_unknown_fields(data, _SOFT_GUARD_FIELDS, "SoftDecisionGuard")
    prior = _require_str(data.get("prior_outcome"), "prior_outcome")
    if prior not in PRIOR_OUTCOMES:
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid prior_outcome: {prior}")
    reason_class = _require_str(data.get("reason_class"), "reason_class")
    if reason_class != SOFT_GUARD_REASON_CLASS_V1:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"soft guard reason_class must be {SOFT_GUARD_REASON_CLASS_V1} for v1",
        )
    decided_at = require_canonical_timestamp(data.get("decided_at"), field="decided_at")
    if now is not None:
        now_c = require_canonical_timestamp(now, field="now")
        _assert_not_after(decided_at, now_c, field="decided_at")
    return SoftDecisionGuard(
        candidate_key=_require_str(data.get("candidate_key"), "candidate_key"),
        decided_at=decided_at,
        prior_outcome=prior,
        reason_class=reason_class,
        meaningful_input_changed=_require_bool(
            data.get("meaningful_input_changed"), "meaningful_input_changed"
        ),
    )


# ---------------------------------------------------------------------------
# Candidate builders
# ---------------------------------------------------------------------------


def _make_candidate(
    *,
    character_id: str,
    decision_key: str,
    candidate_key: str,
    action_kind: str,
    priority_tier: str,
    source_kind: str,
    source_ref: str,
    soft_candidate: bool,
    time_feasible: bool,
    location_feasible: bool,
    physical_feasible: bool,
    domain_guard_satisfied: bool,
    local_preference_permille: int | None,
    activity_instance_id: str | None,
    rule_ids: Sequence[str],
) -> ActionCandidate:
    _assert_tier_source_action(
        action_kind=action_kind, priority_tier=priority_tier, source_kind=source_kind
    )
    if action_kind in SOURCE_LESS_FORBIDDEN and (
        not source_ref or source_kind not in SOURCE_KINDS
    ):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"source-less action forbidden: {action_kind}",
        )
    rid = _canonical_sorted_strs([_require_str(x, "rule_id") for x in rule_ids])
    pref = _require_preference_permille(
        local_preference_permille, "local_preference_permille"
    )
    return ActionCandidate(
        candidate_key=_require_str(candidate_key, "candidate_key"),
        candidate_id=candidate_id_for(
            character_id=character_id,
            decision_key=decision_key,
            candidate_key=candidate_key,
        ),
        action_kind=action_kind,
        priority_tier=priority_tier,
        source_kind=source_kind,
        source_ref=_require_str(source_ref, "source_ref"),
        soft_candidate=_require_bool(soft_candidate, "soft_candidate"),
        time_feasible=_require_bool(time_feasible, "time_feasible"),
        location_feasible=_require_bool(location_feasible, "location_feasible"),
        physical_feasible=_require_bool(physical_feasible, "physical_feasible"),
        domain_guard_satisfied=_require_bool(domain_guard_satisfied, "domain_guard_satisfied"),
        local_preference_permille=pref,
        activity_instance_id=_require_optional_str(
            activity_instance_id, "activity_instance_id"
        ),
        rule_ids=rid,
    )


def build_continue_current_candidate(
    *,
    character_id: str,
    decision_key: str,
    active: ActiveDecisionContext,
) -> ActionCandidate | None:
    """CONTINUE_CURRENT when active + continuation_feasible. Keeps activity_instance_id."""
    if not active.continuation_feasible:
        return None
    return _make_candidate(
        character_id=character_id,
        decision_key=decision_key,
        candidate_key=f"continue:{active.activity_instance_id}",
        action_kind="CONTINUE_CURRENT",
        priority_tier=active.current_priority_tier,
        source_kind="CURRENT_ACTIVITY",
        source_ref=active.source_ref,
        soft_candidate=False,
        time_feasible=True,
        location_feasible=True,
        physical_feasible=True,
        domain_guard_satisfied=True,
        local_preference_permille=active.local_preference_permille,
        activity_instance_id=active.activity_instance_id,
        rule_ids=tuple(active.rule_ids) + ("slice2d.continue_current",),
    )


def build_biological_candidates(
    *,
    character_id: str,
    decision_key: str,
    policy: Mapping[str, Any],
    human_state: Mapping[str, Any],
    meal_feasible: bool | _UnsetType = UNSET,
    rest_feasible: bool | _UnsetType = UNSET,
    sleep_pressure: int | None = None,
    sleep_opportunity: str | _UnsetType = UNSET,
    last_meal_at: str | None = None,
    meal_extent: str | None = None,
    now: str | None = None,
) -> tuple[tuple[ActionCandidate, ...], tuple[RejectedCandidate, ...]]:
    """Hunger / fatigue / sleep biological candidates from Behavior Policy bands.

    meal_feasible / rest_feasible are required caller facts (no silent True default).
    sleep_opportunity is required when sleep_pressure is supplied.
    """
    require_v2_production_format(policy)
    if not isinstance(human_state, Mapping):
        raise LifeEngineError(ErrorCode.INVALID_STATE, "human_state must be a mapping")
    if isinstance(meal_feasible, _UnsetType):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "meal_feasible required when biological generation is requested",
        )
    if isinstance(rest_feasible, _UnsetType):
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "rest_feasible required when biological generation is requested",
        )
    hunger = _require_int(human_state.get("hunger"), "hunger")
    fatigue = _require_int(human_state.get("physical_fatigue"), "physical_fatigue")
    meal_feasible_b = _require_bool(meal_feasible, "meal_feasible")
    rest_feasible_b = _require_bool(rest_feasible, "rest_feasible")

    candidates: list[ActionCandidate] = []
    rejected: list[RejectedCandidate] = []

    hunger_band = classify_hunger_band(hunger, policy)
    # Recent meal guard (transient only).
    meal_guard_active = False
    if last_meal_at is not None:
        if meal_extent is None or meal_extent not in MEAL_EXTENTS:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "meal_extent required when last_meal_at is set",
            )
        if now is None:
            raise LifeEngineError(ErrorCode.INVALID_STATE, "now required for recent meal guard")
        now_c = require_canonical_timestamp(now, field="now")
        last_c = require_canonical_timestamp(last_meal_at, field="last_meal_at")
        _assert_not_after(last_c, now_c, field="last_meal_at")
        elapsed = _minutes_between(last_c, now_c, field="meal_guard")
        guard_min = _meal_recent_guard_min(meal_extent, policy)
        meal_guard_active = elapsed < guard_min

    if hunger_band == "URGENT" and meal_feasible_b:
        # URGENT ignores recent meal guard.
        candidates.append(
            _make_candidate(
                character_id=character_id,
                decision_key=decision_key,
                candidate_key="bio:meal:urgent",
                action_kind="MEAL",
                priority_tier="URGENT_BIOLOGICAL",
                source_kind="BIOLOGICAL",
                source_ref="hunger:URGENT",
                soft_candidate=False,
                time_feasible=True,
                location_feasible=True,
                physical_feasible=True,
                domain_guard_satisfied=True,
                local_preference_permille=None,
                activity_instance_id=None,
                rule_ids=("slice2d.bio.meal.urgent",),
            )
        )
    elif hunger_band == "MEAL_NEEDED" and meal_feasible_b:
        if meal_guard_active:
            rejected.append(
                RejectedCandidate(
                    candidate_key="bio:meal:restorative",
                    candidate_id=candidate_id_for(
                        character_id=character_id,
                        decision_key=decision_key,
                        candidate_key="bio:meal:restorative",
                    ),
                    reason="RECENT_MEAL_GUARD",
                    rule_ids=("slice2d.bio.meal.recent_guard",),
                )
            )
        else:
            candidates.append(
                _make_candidate(
                    character_id=character_id,
                    decision_key=decision_key,
                    candidate_key="bio:meal:restorative",
                    action_kind="MEAL",
                    priority_tier="RESTORATIVE",
                    source_kind="BIOLOGICAL",
                    source_ref="hunger:MEAL_NEEDED",
                    soft_candidate=False,
                    time_feasible=True,
                    location_feasible=True,
                    physical_feasible=True,
                    domain_guard_satisfied=True,
                    local_preference_permille=None,
                    activity_instance_id=None,
                    rule_ids=("slice2d.bio.meal.restorative",),
                )
            )

    fatigue_band = classify_physical_fatigue_band(fatigue, policy)
    if rest_feasible_b:
        if fatigue_band == "VERY_HIGH":
            candidates.append(
                _make_candidate(
                    character_id=character_id,
                    decision_key=decision_key,
                    candidate_key="bio:rest:urgent",
                    action_kind="REST",
                    priority_tier="URGENT_BIOLOGICAL",
                    source_kind="BIOLOGICAL",
                    source_ref="physical_fatigue:VERY_HIGH",
                    soft_candidate=False,
                    time_feasible=True,
                    location_feasible=True,
                    physical_feasible=True,
                    domain_guard_satisfied=True,
                    local_preference_permille=None,
                    activity_instance_id=None,
                    rule_ids=("slice2d.bio.rest.urgent",),
                )
            )
        elif fatigue_band == "HIGH":
            candidates.append(
                _make_candidate(
                    character_id=character_id,
                    decision_key=decision_key,
                    candidate_key="bio:rest:restorative",
                    action_kind="REST",
                    priority_tier="RESTORATIVE",
                    source_kind="BIOLOGICAL",
                    source_ref="physical_fatigue:HIGH",
                    soft_candidate=False,
                    time_feasible=True,
                    location_feasible=True,
                    physical_feasible=True,
                    domain_guard_satisfied=True,
                    local_preference_permille=None,
                    activity_instance_id=None,
                    rule_ids=("slice2d.bio.rest.restorative",),
                )
            )

    # Sleep: caller supplies sleep_pressure (derived); no inventing profile.
    if sleep_pressure is not None:
        if isinstance(sleep_opportunity, _UnsetType):
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                "sleep_opportunity required when sleep_pressure is supplied",
            )
        if sleep_opportunity not in SLEEP_OPPORTUNITIES:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE, f"invalid sleep_opportunity: {sleep_opportunity}"
            )
        sp = _require_int(sleep_pressure, "sleep_pressure")
        sp_band = classify_sleep_pressure_band(sp, policy)
        sleep_ok = sleep_opportunity != "BLOCKED"
        if sp_band == "LOW":
            pass
        elif sp_band == "MODERATE":
            if sleep_opportunity == "FAVORABLE":
                candidates.append(
                    _make_candidate(
                        character_id=character_id,
                        decision_key=decision_key,
                        candidate_key="bio:sleep:restorative",
                        action_kind="SLEEP_MAIN",
                        priority_tier="RESTORATIVE",
                        source_kind="BIOLOGICAL",
                        source_ref="sleep_pressure:MODERATE",
                        soft_candidate=False,
                        time_feasible=True,
                        location_feasible=True,
                        physical_feasible=True,
                        domain_guard_satisfied=True,
                        local_preference_permille=None,
                        activity_instance_id=None,
                        rule_ids=("slice2d.bio.sleep.restorative",),
                    )
                )
        elif sp_band == "HIGH" and sleep_ok:
            candidates.append(
                _make_candidate(
                    character_id=character_id,
                    decision_key=decision_key,
                    candidate_key="bio:sleep:restorative",
                    action_kind="SLEEP_MAIN",
                    priority_tier="RESTORATIVE",
                    source_kind="BIOLOGICAL",
                    source_ref="sleep_pressure:HIGH",
                    soft_candidate=False,
                    time_feasible=True,
                    location_feasible=True,
                    physical_feasible=True,
                    domain_guard_satisfied=True,
                    local_preference_permille=None,
                    activity_instance_id=None,
                    rule_ids=("slice2d.bio.sleep.restorative",),
                )
            )
        elif sp_band == "CRITICAL" and sleep_ok:
            candidates.append(
                _make_candidate(
                    character_id=character_id,
                    decision_key=decision_key,
                    candidate_key="bio:sleep:urgent",
                    action_kind="SLEEP_MAIN",
                    priority_tier="URGENT_BIOLOGICAL",
                    source_kind="BIOLOGICAL",
                    source_ref="sleep_pressure:CRITICAL",
                    soft_candidate=False,
                    time_feasible=True,
                    location_feasible=True,
                    physical_feasible=True,
                    domain_guard_satisfied=True,
                    local_preference_permille=None,
                    activity_instance_id=None,
                    rule_ids=("slice2d.bio.sleep.urgent",),
                )
            )
    elif not isinstance(sleep_opportunity, _UnsetType):
        # Explicit opportunity without pressure is unused but validated.
        if sleep_opportunity not in SLEEP_OPPORTUNITIES:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE, f"invalid sleep_opportunity: {sleep_opportunity}"
            )

    return tuple(candidates), tuple(rejected)


def build_leisure_candidates(
    *,
    character_id: str,
    decision_key: str,
    policy: Mapping[str, Any],
    free_window_key: str,
    free_window_min: int,
    feasible_leisure_categories: Sequence[str],
) -> tuple[ActionCandidate, ...]:
    """Leisure from free-window facts only. No quota/rotation/interestingness."""
    policy_cats = _policy_leisure_categories(policy)
    window_key = _require_str(free_window_key, "free_window_key")
    window_min = _require_int(free_window_min, "free_window_min")
    if window_min < 0:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "free_window_min must be >= 0")

    declared: list[str] = []
    for cat in feasible_leisure_categories:
        c = _require_str(cat, "feasible_leisure_category")
        if c not in policy_cats:
            raise LifeEngineError(
                ErrorCode.INVALID_STATE,
                f"leisure category not in domain_policy: {c}",
            )
        if c not in LEISURE_ACTION_KINDS:
            raise LifeEngineError(ErrorCode.INVALID_STATE, f"invalid leisure action_kind: {c}")
        declared.append(c)
    declared = list(_canonical_sorted_strs(declared))

    short_gap_max, short_cand_max = _leisure_window_bounds(policy)
    # Contract: <= short_gap_max → none; <= short_cand_max → short/passive; else full.
    if window_min <= short_gap_max:
        return ()
    if window_min <= short_cand_max:
        allowed = [c for c in declared if c in SHORT_LEISURE_CATEGORIES]
    else:
        allowed = declared

    out: list[ActionCandidate] = []
    for cat in allowed:
        out.append(
            _make_candidate(
                character_id=character_id,
                decision_key=decision_key,
                candidate_key=f"leisure:{cat}:{window_key}",
                action_kind=cat,
                priority_tier="LEISURE_SPONTANEOUS",
                source_kind="LEISURE_WINDOW",
                source_ref=window_key,
                soft_candidate=True,
                time_feasible=True,
                location_feasible=True,
                physical_feasible=True,
                domain_guard_satisfied=True,
                local_preference_permille=None,
                activity_instance_id=None,
                rule_ids=("slice2d.leisure",),
            )
        )
    return tuple(out)


def opportunity_to_candidate(
    *,
    character_id: str,
    decision_key: str,
    opportunity: ResolvedOpportunity | Mapping[str, Any],
) -> ActionCandidate:
    opp = parse_resolved_opportunity(opportunity)
    return _make_candidate(
        character_id=character_id,
        decision_key=decision_key,
        candidate_key=opp.opportunity_key,
        action_kind=opp.action_kind,
        priority_tier=opp.opportunity_class,
        source_kind=opp.source_kind,
        source_ref=opp.source_ref,
        soft_candidate=opp.soft_candidate,
        time_feasible=opp.time_feasible,
        location_feasible=opp.location_feasible,
        physical_feasible=opp.physical_feasible,
        domain_guard_satisfied=opp.domain_guard_satisfied,
        local_preference_permille=opp.local_preference_permille,
        activity_instance_id=None,
        rule_ids=opp.rule_ids,
    )


# ---------------------------------------------------------------------------
# Feasibility gate + soft reconsider
# ---------------------------------------------------------------------------


def active_same_source_rejection(
    candidate: ActionCandidate,
    *,
    active: ActiveDecisionContext | None,
) -> RejectedCandidate | None:
    """Block a new start for the exact causal activity that is already running.

    Scope is intentionally narrow: same action/source/tier and only while the
    current activity remains continuation-feasible. Different sources, tier
    changes, and reviewed end-boundary replacement keep existing semantics.
    """
    if active is None or not active.continuation_feasible:
        return None
    if candidate.action_kind == "CONTINUE_CURRENT":
        return None
    if (
        candidate.action_kind == active.action_kind
        and candidate.priority_tier == active.current_priority_tier
        and candidate.source_kind == active.source_kind
        and candidate.source_ref == active.source_ref
    ):
        return RejectedCandidate(
            candidate_key=candidate.candidate_key,
            candidate_id=candidate.candidate_id,
            reason="ACTIVE_SOURCE_ALREADY_RUNNING",
            rule_ids=("slice2d.active.same_source_running",),
        )
    return None


def physical_feasibility_rejection(candidate: ActionCandidate) -> RejectedCandidate | None:
    """Gate order: causal source → time → location → physical → domain."""
    if candidate.action_kind in SOURCE_LESS_FORBIDDEN and not candidate.source_ref:
        return RejectedCandidate(
            candidate_key=candidate.candidate_key,
            candidate_id=candidate.candidate_id,
            reason="MISSING_CAUSAL_SOURCE",
        )
    if not candidate.time_feasible:
        return RejectedCandidate(
            candidate_key=candidate.candidate_key,
            candidate_id=candidate.candidate_id,
            reason="TIME_INFEASIBLE",
        )
    if not candidate.location_feasible:
        return RejectedCandidate(
            candidate_key=candidate.candidate_key,
            candidate_id=candidate.candidate_id,
            reason="LOCATION_INFEASIBLE",
        )
    if not candidate.physical_feasible:
        return RejectedCandidate(
            candidate_key=candidate.candidate_key,
            candidate_id=candidate.candidate_id,
            reason="PHYSICAL_INFEASIBLE",
        )
    if not candidate.domain_guard_satisfied:
        return RejectedCandidate(
            candidate_key=candidate.candidate_key,
            candidate_id=candidate.candidate_id,
            reason="DOMAIN_GUARD_BLOCKED",
        )
    return None


def soft_reconsider_blocks(
    candidate: ActionCandidate,
    *,
    guards: Sequence[SoftDecisionGuard],
    now: str,
    soft_reconsider_guard_min: int,
) -> RejectedCandidate | None:
    """Soft + same key + <30m + meaningful_input_changed=false → SOFT_RECONSIDER_GUARD.

    Not applied to HARD_COMMITMENT / URGENT_BIOLOGICAL.
    """
    if candidate.priority_tier in {"HARD_COMMITMENT", "URGENT_BIOLOGICAL"}:
        return None
    if not candidate.soft_candidate:
        return None
    now_c = require_canonical_timestamp(now, field="now")
    for g in guards:
        if g.candidate_key != candidate.candidate_key:
            continue
        if g.reason_class != "CAPACITY":
            continue
        if g.meaningful_input_changed:
            continue
        elapsed = _minutes_between(g.decided_at, now_c, field="soft_reconsider")
        if elapsed < soft_reconsider_guard_min:
            return RejectedCandidate(
                candidate_key=candidate.candidate_key,
                candidate_id=candidate.candidate_id,
                reason="SOFT_RECONSIDER_GUARD",
                rule_ids=("slice2d.soft_reconsider_guard",),
            )
    return None


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _near_equal_peers(
    candidates: Sequence[ActionCandidate],
    *,
    margin_permille: int,
) -> list[ActionCandidate]:
    """Within one tier: near-equal set by local_preference_permille ranks."""
    if not candidates:
        return []
    ranks = [c.local_preference_permille for c in candidates]
    has_null = any(r is None for r in ranks)
    has_ranked = any(r is not None for r in ranks)
    if has_null and has_ranked:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "mixed ranked and null local_preference_permille in same tier",
        )
    if all(r is None for r in ranks):
        # All null → keyed tie peers = entire tier set.
        return list(candidates)
    assert all(r is not None for r in ranks)
    best = max(r for r in ranks if r is not None)
    threshold = best - margin_permille
    peers = [c for c in candidates if (c.local_preference_permille or 0) >= threshold]
    return peers


def keyed_tie_choose(
    *,
    world_seed: SeedLike,
    character_id: str,
    decision_key: str,
    peers: Sequence[ActionCandidate],
) -> tuple[ActionCandidate, str]:
    """Order-independent keyed tie. Returns (winner, random_key)."""
    if not peers:
        raise LifeEngineError(ErrorCode.INVALID_STATE, "keyed tie requires peers")
    sorted_peers = sorted(peers, key=lambda c: c.candidate_id)
    sorted_ids = [c.candidate_id for c in sorted_peers]
    decision_instance = "\0".join(sorted_ids)
    digest = keyed_digest(
        world_seed,
        RNG_NAMESPACE,
        character_id,
        decision_key,
        decision_instance,
    )
    draw = u01(
        world_seed,
        RNG_NAMESPACE,
        character_id,
        decision_key,
        decision_instance,
    )
    index = (draw * len(sorted_peers)) // 1_000_000_000
    if index >= len(sorted_peers):
        index = len(sorted_peers) - 1
    winner = sorted_peers[index]
    random_key = digest.hex()
    return winner, random_key


def select_among_eligible(
    *,
    world_seed: SeedLike,
    character_id: str,
    decision_key: str,
    eligible: Sequence[ActionCandidate],
    near_equal_margin_permille: int,
) -> tuple[ActionCandidate | None, str | None, str | None]:
    """Top nonempty tier only; within-tier near-equal + keyed tie.

    Returns (winner, random_key, selection_mode) where selection_mode is
    ``single`` | ``ranked_deterministic`` | ``keyed_tie`` | None.
    """
    if not eligible:
        return None, None, None
    by_tier: dict[str, list[ActionCandidate]] = {t: [] for t in SELECTION_TIERS}
    for c in eligible:
        by_tier[c.priority_tier].append(c)
    top: list[ActionCandidate] | None = None
    for tier in SELECTION_TIERS:
        if by_tier[tier]:
            top = by_tier[tier]
            break
    if top is None:
        return None, None, None
    if len(top) == 1:
        return top[0], None, "single"
    peers = _near_equal_peers(top, margin_permille=near_equal_margin_permille)
    if len(peers) == 1:
        return peers[0], None, "ranked_deterministic"
    winner, random_key = keyed_tie_choose(
        world_seed=world_seed,
        character_id=character_id,
        decision_key=decision_key,
        peers=peers,
    )
    return winner, random_key, "keyed_tie"


def _min_dwell_prefers_continue(
    *,
    active: ActiveDecisionContext | None,
    boundary: DecisionBoundaryFact,
    now: str,
    continue_candidate: ActionCandidate | None,
    eligible: Sequence[ActionCandidate],
    reassessable_min_dwell_min: int,
) -> bool:
    """THRESHOLD anti-chatter only: prefer CONTINUE unless HARD/URGENT challenger."""
    if active is None or continue_candidate is None:
        return False
    if active.interruptibility != "REASSESSABLE":
        return False
    if boundary.trigger_kind != "THRESHOLD":
        return False
    if continue_candidate.candidate_id not in {c.candidate_id for c in eligible}:
        return False
    elapsed = _minutes_between(active.actual_start, now, field="min_dwell")
    if elapsed >= reassessable_min_dwell_min:
        return False
    for c in eligible:
        if c.priority_tier in {"HARD_COMMITMENT", "URGENT_BIOLOGICAL"}:
            if c.action_kind != "CONTINUE_CURRENT":
                return False
    return True


# ---------------------------------------------------------------------------
# Main resolver
# ---------------------------------------------------------------------------


def collect_action_candidates(
    *,
    character_id: str,
    decision_key: str,
    policy: Mapping[str, Any],
    human_state: Mapping[str, Any] | None = None,
    active: ActiveDecisionContext | Mapping[str, Any] | None = None,
    opportunities: Sequence[ResolvedOpportunity | Mapping[str, Any]] = (),
    meal_feasible: bool | _UnsetType = UNSET,
    rest_feasible: bool | _UnsetType = UNSET,
    sleep_pressure: int | None = None,
    sleep_opportunity: str | _UnsetType = UNSET,
    last_meal_at: str | None = None,
    meal_extent: str | None = None,
    now: str | None = None,
    free_window_key: str | None = None,
    free_window_min: int | None = None,
    feasible_leisure_categories: Sequence[str] = (),
) -> tuple[tuple[ActionCandidate, ...], tuple[RejectedCandidate, ...]]:
    """Assemble raw candidates (pre-gate except bio recent-meal rejects)."""
    require_v2_production_format(policy)
    active_ctx = parse_active_decision_context(active, now=now)
    collected: list[ActionCandidate] = []
    early_rejected: list[RejectedCandidate] = []

    if active_ctx is not None:
        cont = build_continue_current_candidate(
            character_id=character_id,
            decision_key=decision_key,
            active=active_ctx,
        )
        if cont is not None:
            collected.append(cont)

    if human_state is not None:
        bio, bio_rej = build_biological_candidates(
            character_id=character_id,
            decision_key=decision_key,
            policy=policy,
            human_state=human_state,
            meal_feasible=meal_feasible,
            rest_feasible=rest_feasible,
            sleep_pressure=sleep_pressure,
            sleep_opportunity=sleep_opportunity,
            last_meal_at=last_meal_at,
            meal_extent=meal_extent,
            now=now,
        )
        collected.extend(bio)
        early_rejected.extend(bio_rej)

    for opp in opportunities:
        collected.append(
            opportunity_to_candidate(
                character_id=character_id,
                decision_key=decision_key,
                opportunity=opp,
            )
        )

    if free_window_key is not None and free_window_min is not None:
        leisure = build_leisure_candidates(
            character_id=character_id,
            decision_key=decision_key,
            policy=policy,
            free_window_key=free_window_key,
            free_window_min=free_window_min,
            feasible_leisure_categories=feasible_leisure_categories,
        )
        collected.extend(leisure)

    # Duplicate key/id → fail closed (no silent dedupe).
    keys = [c.candidate_key for c in collected]
    ids = [c.candidate_id for c in collected]
    if len(keys) != len(set(keys)):
        raise LifeEngineError(ErrorCode.DUPLICATE_ID, "duplicate candidate_key in decision set")
    if len(ids) != len(set(ids)):
        raise LifeEngineError(ErrorCode.DUPLICATE_ID, "duplicate candidate_id in decision set")

    return tuple(collected), tuple(early_rejected)


def resolve_action_decision(
    *,
    world_seed: SeedLike,
    character_id: str,
    policy: Mapping[str, Any],
    boundary: DecisionBoundaryFact | Mapping[str, Any],
    now: str,
    human_state: Mapping[str, Any] | None = None,
    active: ActiveDecisionContext | Mapping[str, Any] | None = None,
    opportunities: Sequence[ResolvedOpportunity | Mapping[str, Any]] = (),
    soft_guards: Sequence[SoftDecisionGuard | Mapping[str, Any]] = (),
    meal_feasible: bool | _UnsetType = UNSET,
    rest_feasible: bool | _UnsetType = UNSET,
    sleep_pressure: int | None = None,
    sleep_opportunity: str | _UnsetType = UNSET,
    last_meal_at: str | None = None,
    meal_extent: str | None = None,
    free_window_key: str | None = None,
    free_window_min: int | None = None,
    feasible_leisure_categories: Sequence[str] = (),
) -> DecisionResolution:
    """Pure categorical resolver. Synthetic-only; no runtime mutation."""
    if world_seed is None or world_seed == "":
        raise LifeEngineError(ErrorCode.INVALID_STATE, "world_seed required")
    character_id = _require_str(character_id, "character_id")
    now_c = require_canonical_timestamp(now, field="now")
    boundary_f = parse_decision_boundary_fact(boundary)
    thr = _decision_thresholds(policy)
    guards = [parse_soft_decision_guard(g, now=now_c) for g in soft_guards]
    active_ctx = parse_active_decision_context(active, now=now_c)

    raw, early_rejected = collect_action_candidates(
        character_id=character_id,
        decision_key=boundary_f.decision_key,
        policy=policy,
        human_state=human_state,
        active=active_ctx,
        opportunities=opportunities,
        meal_feasible=meal_feasible,
        rest_feasible=rest_feasible,
        sleep_pressure=sleep_pressure,
        sleep_opportunity=sleep_opportunity,
        last_meal_at=last_meal_at,
        meal_extent=meal_extent,
        now=now_c,
        free_window_key=free_window_key,
        free_window_min=free_window_min,
        feasible_leisure_categories=feasible_leisure_categories,
    )

    eligible: list[ActionCandidate] = []
    rejected: list[RejectedCandidate] = list(early_rejected)
    for cand in raw:
        same_active = active_same_source_rejection(cand, active=active_ctx)
        if same_active is not None:
            rejected.append(same_active)
            continue
        gate = physical_feasibility_rejection(cand)
        if gate is not None:
            rejected.append(gate)
            continue
        soft = soft_reconsider_blocks(
            cand,
            guards=guards,
            now=now_c,
            soft_reconsider_guard_min=thr["soft_reconsider_guard_min"],
        )
        if soft is not None:
            rejected.append(soft)
            continue
        eligible.append(cand)

    considered = _canonical_sorted_strs([c.candidate_id for c in raw])
    rejected_sorted = tuple(
        sorted(rejected, key=lambda r: (r.candidate_key, r.reason, r.candidate_id or ""))
    )
    all_rule_ids: list[str] = ["slice2d.resolve"]
    for c in raw:
        all_rule_ids.extend(c.rule_ids)
    for r in rejected:
        all_rule_ids.extend(r.rule_ids)
    rule_ids = tuple(sorted(set(all_rule_ids)))

    continue_cand = next(
        (c for c in eligible if c.action_kind == "CONTINUE_CURRENT"),
        None,
    )

    if _min_dwell_prefers_continue(
        active=active_ctx,
        boundary=boundary_f,
        now=now_c,
        continue_candidate=continue_cand,
        eligible=eligible,
        reassessable_min_dwell_min=thr["reassessable_min_dwell_min"],
    ):
        assert continue_cand is not None
        return DecisionResolution(
            result_kind="CONTINUE_CURRENT",
            decision_key=boundary_f.decision_key,
            selected_candidate_id=continue_cand.candidate_id,
            selected_candidate_key=continue_cand.candidate_key,
            selected_action_kind=continue_cand.action_kind,
            selected_priority_tier=continue_cand.priority_tier,
            activity_instance_id=continue_cand.activity_instance_id,
            considered_candidate_ids=considered,
            rejected=rejected_sorted,
            rule_ids=rule_ids,
            random_key=None,
            min_dwell_forced=True,
            selection_mode="min_dwell",
        )

    winner, random_key, selection_mode = select_among_eligible(
        world_seed=world_seed,
        character_id=character_id,
        decision_key=boundary_f.decision_key,
        eligible=eligible,
        near_equal_margin_permille=thr["near_equal_margin_permille"],
    )

    if winner is None:
        return DecisionResolution(
            result_kind="NO_ELIGIBLE_ACTION",
            decision_key=boundary_f.decision_key,
            selected_candidate_id=None,
            selected_candidate_key=None,
            selected_action_kind=None,
            selected_priority_tier=None,
            activity_instance_id=None,
            considered_candidate_ids=considered,
            rejected=rejected_sorted,
            rule_ids=rule_ids,
            random_key=None,
            min_dwell_forced=False,
            selection_mode=None,
        )

    if winner.action_kind == "CONTINUE_CURRENT":
        result_kind = "CONTINUE_CURRENT"
    else:
        result_kind = "SELECT_CANDIDATE"

    return DecisionResolution(
        result_kind=result_kind,
        decision_key=boundary_f.decision_key,
        selected_candidate_id=winner.candidate_id,
        selected_candidate_key=winner.candidate_key,        selected_action_kind=winner.action_kind,
        selected_priority_tier=winner.priority_tier,
        activity_instance_id=winner.activity_instance_id,
        considered_candidate_ids=considered,
        rejected=rejected_sorted,
        rule_ids=rule_ids,
        random_key=random_key,
        min_dwell_forced=False,
        selection_mode=selection_mode,
    )


def build_decision_evidence(
    *,
    decision_type: str,
    policy_version: str,
    rule_ids: Sequence[str],
    input_state_refs: Sequence[str],
    random_key: str | None,
    selected_result: str,
) -> dict[str, Any]:
    """Pure helper: ActualEvent-compatible decision_evidence shape. No event generation."""
    _reject_floats(
        {
            "decision_type": decision_type,
            "policy_version": policy_version,
            "rule_ids": list(rule_ids),
            "input_state_refs": list(input_state_refs),
            "random_key": random_key,
            "selected_result": selected_result,
        },
        "decision_evidence",
    )
    return {
        "decision_type": _require_str(decision_type, "decision_type"),
        "policy_version": _require_str(policy_version, "policy_version"),
        "rule_ids": list(_canonical_sorted_strs([_require_str(x, "rule_id") for x in rule_ids])),
        "input_state_refs": list(
            _canonical_sorted_strs([_require_str(x, "input_state_ref") for x in input_state_refs])
        ),
        "random_key": (
            None if random_key is None else _require_str(random_key, "random_key")
        ),
        "selected_result": _require_str(selected_result, "selected_result"),
    }

