"""Life Engine v2 Slice 5B2C2A — active interval integration + runtime wakeup queue.

Pure/in-memory only. Advances an existing runtime-started ActiveActivity through
one explicit interval boundary and reconciles ACTIVITY_END + managed v2
DECISION_WAKEUP ownership on the pending queue.

Does **not** finalize activities, dequeue/process events, settle sleep debt /
meal remainders, switch/start, capture/Camera Roll, wire clock.py, or write
disk/Git/production branch. Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .activity_materialization import build_activity_end_event, parse_dynamics_context
from .canonical import canonical_json
from .checkpoint import validate_checkpoint
from .derived import validate_synthetic_sleep_profile
from .dynamics import integrate_v2_interval, require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .invariants import queue_event_sort_key
from .policy import normalize_policy
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .schema import reject_binary_floats
from .timeutil import (
    add_minutes,
    format_rfc3339,
    parse_rfc3339,
    require_canonical_timestamp,
)
from .wakeups import (
    DecisionWakeupContext,
    KnownDecisionBoundary,
    ProjectionResult,
    reconcile_v2_decision_wakeups,
    validate_decision_wakeup_context,
    validate_known_decision_boundary,
)

_WAKEUP_FACT_FIELDS = frozenset(
    {
        "sleep_profile",
        "elapsed_since_last_main_sleep_end_seconds",
        "total_nap_awake_credit_min",
        "fatigue_high_relevant",
        "stress_relevant",
        "social_low_relevant",
        "known_boundaries",
        "projection_horizon_end",
    }
)

_RUNTIME_ACTIVE_REQUIRED = frozenset(
    {
        "runtime_context",
        "dynamics_context",
        "decision_evidence",
        "causes",
    }
)


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _require_true_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be int (no bool/float)")
    return value


def _require_non_neg_int(value: object, label: str) -> int:
    n = _require_true_int(value, label)
    if n < 0:
        _fail(f"{label} must be >= 0")
    return n


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{label} must be bool")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = set(data.keys()) - allowed
    if unknown:
        _fail(f"{label}: unknown fields {sorted(unknown)}")


def _require_v2_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        _fail("behavior_policy must be a mapping")
    # normalize_policy: schema + float reject + v2 invariants.
    normalized = normalize_policy(dict(policy))
    # Slice 2B production-format gate (schema_version=2, PRODUCTION).
    return require_v2_production_format(normalized)


def _add_minutes_ts(ts: str, minutes: int, *, field: str) -> str:
    return format_rfc3339(add_minutes(parse_rfc3339(ts, field=field), minutes))


def _require_runtime_active_activity(
    activity: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed unless activity is a runtime-started C1+ ActiveActivity."""
    if activity is None:
        _fail("runtime-started ActiveActivity required (active-only C2A path)")
    if not isinstance(activity, Mapping):
        _fail("active_activity must be a mapping")
    missing = _RUNTIME_ACTIVE_REQUIRED - set(activity.keys())
    if missing:
        _fail(
            "legacy/Foundation ActiveActivity missing runtime fields: "
            f"{sorted(missing)}"
        )
    if activity.get("dynamics_context") is None:
        _fail("ActiveActivity.dynamics_context required (no fallback dynamics class)")
    if activity.get("runtime_context") is None:
        _fail("ActiveActivity.runtime_context required")
    if activity.get("decision_evidence") is None:
        _fail("ActiveActivity.decision_evidence required")
    if "causes" not in activity or activity["causes"] is None:
        _fail("ActiveActivity.causes required")
    validated = validate_active_activity(dict(activity))
    assert validated is not None
    # Re-check after schema normalize (optional fields may drop if absent).
    for key in _RUNTIME_ACTIVE_REQUIRED:
        if key not in validated or validated[key] is None:
            _fail(f"ActiveActivity.{key} must survive validation")
    return validated


def _dynamics_activity_context(active: Mapping[str, Any]) -> dict[str, str]:
    dynamics = parse_dynamics_context(active["dynamics_context"])
    return {
        "base_activity_class": dynamics.base_activity_class,
        "social_exposure": dynamics.social_exposure,
    }


def _elapsed_seconds(*, processed_through: str, until: str) -> int:
    start = parse_rfc3339(processed_through, field="processed_through")
    end = parse_rfc3339(until, field="until")
    delta = end - start
    seconds = delta.total_seconds()
    if seconds != int(seconds):
        _fail("interval elapsed_seconds must be whole seconds")
    elapsed = int(seconds)
    if elapsed < 0:
        raise LifeEngineError(ErrorCode.TIME_REVERSAL, "until before processed_through")
    return elapsed


def integrate_active_interval(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    until: str,
) -> RuntimeBundle:
    """Integrate CurrentState from processed_through to until via Slice 2B v2 dynamics.

    Updates only human_state, integration.rate_remainders, and processed_through.
    """
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state

    policy = _require_v2_policy(behavior_policy)
    if policy["behavior_policy_version"] != current_in["behavior_policy_version"]:
        _fail(
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current_in['behavior_policy_version']!r}"
        )

    until_c = require_canonical_timestamp(until, field="until")
    processed = require_canonical_timestamp(
        current_in["processed_through"], field="processed_through"
    )

    active = _require_runtime_active_activity(current_in["context"].get("active_activity"))
    actual_start = require_canonical_timestamp(active["actual_start"], field="actual_start")
    if parse_rfc3339(actual_start, field="actual_start") > parse_rfc3339(
        processed, field="processed_through"
    ):
        _fail("active.actual_start must be <= processed_through")

    if parse_rfc3339(until_c, field="until") < parse_rfc3339(
        processed, field="processed_through"
    ):
        raise LifeEngineError(ErrorCode.TIME_REVERSAL, "until before processed_through")

    planned_end = active.get("planned_end")
    if planned_end is not None:
        planned_c = require_canonical_timestamp(planned_end, field="planned_end")
        if parse_rfc3339(until_c, field="until") > parse_rfc3339(
            planned_c, field="planned_end"
        ):
            _fail("until past planned_end (equality allowed)")

    window = active.get("expected_end_window")
    if isinstance(window, Mapping):
        latest = window.get("latest")
        if latest is not None:
            latest_c = require_canonical_timestamp(latest, field="expected_end_window.latest")
            if parse_rfc3339(until_c, field="until") > parse_rfc3339(
                latest_c, field="expected_end_window.latest"
            ):
                _fail("until past expected_end_window.latest (equality allowed)")

    elapsed = _elapsed_seconds(processed_through=processed, until=until_c)
    activity_context = _dynamics_activity_context(active)
    new_hs, new_rem = integrate_v2_interval(
        current_in["human_state"],
        current_in["integration"]["rate_remainders"],
        policy,
        activity_context,
        elapsed,
    )

    out_current = deepcopy(dict(current_in))
    # Preserve sleep_debt_min exactly (integrate_v2_interval already leaves it unchanged,
    # but re-bind from input for fail-closed clarity).
    sleep_debt = current_in["human_state"]["sleep_debt_min"]
    if new_hs["sleep_debt_min"] != sleep_debt:
        _fail("sleep_debt_min must not change under continuous integration")
    out_current["human_state"] = new_hs
    out_current["integration"] = {
        **deepcopy(dict(current_in["integration"])),
        "rate_remainders": new_rem,
    }
    out_current["processed_through"] = until_c

    # Invariants: everything else untouched.
    if out_current["state_revision"] != current_in["state_revision"]:
        _fail("state_revision must remain unchanged")
    if out_current["history"] != current_in["history"]:
        _fail("history must remain unchanged")
    if out_current["pending_queue"] != current_in["pending_queue"]:
        _fail("pending_queue must remain unchanged during integrate_active_interval")
    if out_current["context"]["active_activity"] != current_in["context"]["active_activity"]:
        _fail("active_activity must remain unchanged during integrate_active_interval")

    out_current = validate_checkpoint(out_current)
    out_bundle = RuntimeBundle(
        current_state=out_current,
        schedule_state=deepcopy(dict(validated_in.schedule_state)),
        relation_state=deepcopy(dict(validated_in.relation_state)),
        home_state=deepcopy(dict(validated_in.home_state)),
        consumables_state=deepcopy(dict(validated_in.consumables_state)),
        wardrobe_state=deepcopy(dict(validated_in.wardrobe_state)),
        finance_state=deepcopy(dict(validated_in.finance_state)),
    )
    return validate_runtime_bundle(out_bundle, reference_sets=reference_sets)


def active_reassessment_boundaries(
    active_activity: Mapping[str, Any],
    *,
    now: str,
) -> tuple[KnownDecisionBoundary, ...]:
    """Canonical ACTIVE_REASSESSMENT boundaries from expected_end_window.

    Exact planned_end activities emit no ACTIVE_REASSESSMENT (ACTIVITY_END owns
    that instant). Never invents ACTIVITY_END from expected latest.
    """
    active = _require_runtime_active_activity(active_activity)
    now_c = require_canonical_timestamp(now, field="now")
    now_dt = parse_rfc3339(now_c, field="now")
    instance = _require_str(active["activity_instance_id"], "activity_instance_id")

    if active.get("planned_end") is not None:
        return ()

    window = active.get("expected_end_window")
    if window is None:
        return ()
    if not isinstance(window, Mapping):
        _fail("expected_end_window must be object or null")

    earliest_raw = window.get("earliest")
    latest_raw = window.get("latest")
    earliest = (
        None
        if earliest_raw is None
        else require_canonical_timestamp(earliest_raw, field="expected_end_window.earliest")
    )
    latest = (
        None
        if latest_raw is None
        else require_canonical_timestamp(latest_raw, field="expected_end_window.latest")
    )
    if latest is None:
        _fail("expected_end_window.latest required when window is present")

    boundaries: list[KnownDecisionBoundary] = []

    def _maybe_add(*, due: str, kind_token: str, reason: str) -> None:
        due_dt = parse_rfc3339(due, field="due_at")
        if due_dt < now_dt:
            return
        boundaries.append(
            validate_known_decision_boundary(
                KnownDecisionBoundary(
                    due_at=due,
                    trigger_kind="ACTIVE_REASSESSMENT",
                    decision_key=stable_id(
                        "active-reassessment", instance, kind_token, due
                    ),
                    reason_code=reason,
                    source_kind="CURRENT_ACTIVITY",
                    source_ref=instance,
                ),
                now=now_c,
            )
        )

    if earliest is not None and earliest == latest:
        _maybe_add(
            due=latest,
            kind_token="BOUNDARY",
            reason="ACTIVE_EXPECTED_WINDOW_BOUNDARY",
        )
    elif earliest is None:
        _maybe_add(
            due=latest,
            kind_token="LATEST",
            reason="ACTIVE_EXPECTED_WINDOW_LATEST",
        )
    else:
        if parse_rfc3339(earliest, field="earliest") > parse_rfc3339(
            latest, field="latest"
        ):
            _fail("expected_end_window.earliest must be <= latest")
        _maybe_add(
            due=earliest,
            kind_token="EARLIEST",
            reason="ACTIVE_EXPECTED_WINDOW_EARLIEST",
        )
        _maybe_add(
            due=latest,
            kind_token="LATEST",
            reason="ACTIVE_EXPECTED_WINDOW_LATEST",
        )

    return tuple(boundaries)


def _assert_expected_latest_not_missed(
    *,
    active: Mapping[str, Any],
    processed_through: str,
) -> None:
    """Fail closed if mandatory expected-window latest is already in the past."""
    window = active.get("expected_end_window")
    if not isinstance(window, Mapping):
        return
    latest_raw = window.get("latest")
    if latest_raw is None:
        return
    latest = require_canonical_timestamp(
        latest_raw, field="expected_end_window.latest"
    )
    if parse_rfc3339(processed_through, field="processed_through") > parse_rfc3339(
        latest, field="expected_end_window.latest"
    ):
        _fail(
            "processed_through past expected_end_window.latest "
            "(mandatory reassessment boundary already missed)"
        )


def _apply_consumed_active_boundary_key(
    boundaries: tuple[KnownDecisionBoundary, ...],
    *,
    consumed_active_boundary_key: str | None,
    processed_through: str,
    activity_instance_id: str,
) -> tuple[KnownDecisionBoundary, ...]:
    """One-shot exclusion of a just-consumed ACTIVE_REASSESSMENT at due_at==now.

    Transient only — never persisted. Null preserves due-at-now recovery.
    """
    if consumed_active_boundary_key is None:
        return boundaries
    if (
        not isinstance(consumed_active_boundary_key, str)
        or not consumed_active_boundary_key
    ):
        _fail("consumed_active_boundary_key must be non-empty str or null")

    matches = [
        b for b in boundaries if b.decision_key == consumed_active_boundary_key
    ]
    if len(matches) != 1:
        _fail(
            "consumed_active_boundary_key must match exactly one active "
            f"reassessment boundary at processed_through "
            f"(key={consumed_active_boundary_key!r} matches={len(matches)})"
        )
    match = matches[0]
    if match.trigger_kind != "ACTIVE_REASSESSMENT":
        _fail("consumed_active_boundary_key must refer to ACTIVE_REASSESSMENT")
    if match.source_kind != "CURRENT_ACTIVITY":
        _fail("consumed_active_boundary_key source_kind must be CURRENT_ACTIVITY")
    if match.source_ref != activity_instance_id:
        _fail(
            "consumed_active_boundary_key source_ref must equal current "
            f"activity_instance_id (got {match.source_ref!r})"
        )
    if match.due_at != processed_through:
        _fail(
            "consumed_active_boundary_key due_at must equal "
            "CurrentState.processed_through "
            f"(due_at={match.due_at!r} processed_through={processed_through!r})"
        )
    return tuple(b for b in boundaries if b.decision_key != consumed_active_boundary_key)


def apply_active_window_continuation_guard(
    active_activity: Mapping[str, Any],
    *,
    now: str,
    source_continuation_feasible: bool,
) -> bool:
    """Hard-false CONTINUE_CURRENT past planned_end / expected latest."""
    if not isinstance(source_continuation_feasible, bool):
        _fail("source_continuation_feasible must be bool")
    active = _require_runtime_active_activity(active_activity)
    now_c = require_canonical_timestamp(now, field="now")
    now_dt = parse_rfc3339(now_c, field="now")

    planned_end = active.get("planned_end")
    if planned_end is not None:
        planned_c = require_canonical_timestamp(planned_end, field="planned_end")
        if now_dt >= parse_rfc3339(planned_c, field="planned_end"):
            return False

    window = active.get("expected_end_window")
    if isinstance(window, Mapping):
        latest = window.get("latest")
        if latest is not None:
            latest_c = require_canonical_timestamp(
                latest, field="expected_end_window.latest"
            )
            if now_dt >= parse_rfc3339(latest_c, field="expected_end_window.latest"):
                return False

    return source_continuation_feasible


@dataclass(frozen=True)
class RuntimeWakeupProjectionFacts:
    sleep_profile: Mapping[str, Any]
    elapsed_since_last_main_sleep_end_seconds: int
    total_nap_awake_credit_min: int
    fatigue_high_relevant: bool
    stress_relevant: bool
    social_low_relevant: bool
    known_boundaries: tuple[KnownDecisionBoundary, ...]
    projection_horizon_end: str

    def as_dict(self) -> dict[str, Any]:
        """Non-coercive snapshot for the strict parser.

        Malformed direct dataclass fields must reach validation intact — never
        call ``dict(sleep_profile)`` / ``b._asdict()`` before type proof.
        """
        sleep_profile: Any = self.sleep_profile
        if isinstance(sleep_profile, Mapping):
            sleep_profile = dict(sleep_profile)

        raw_bounds: Any = self.known_boundaries
        if isinstance(raw_bounds, (list, tuple)) and not isinstance(
            raw_bounds, (str, bytes)
        ):
            bounds_out: list[Any] = []
            for item in raw_bounds:
                if isinstance(item, KnownDecisionBoundary):
                    bounds_out.append(item._asdict())
                elif isinstance(item, Mapping):
                    bounds_out.append(dict(item))
                else:
                    bounds_out.append(item)
            known_boundaries: Any = bounds_out
        else:
            known_boundaries = raw_bounds

        return {
            "sleep_profile": sleep_profile,
            "elapsed_since_last_main_sleep_end_seconds": (
                self.elapsed_since_last_main_sleep_end_seconds
            ),
            "total_nap_awake_credit_min": self.total_nap_awake_credit_min,
            "fatigue_high_relevant": self.fatigue_high_relevant,
            "stress_relevant": self.stress_relevant,
            "social_low_relevant": self.social_low_relevant,
            "known_boundaries": known_boundaries,
            "projection_horizon_end": self.projection_horizon_end,
        }


def parse_runtime_wakeup_projection_facts(
    raw: Mapping[str, Any] | RuntimeWakeupProjectionFacts,
) -> RuntimeWakeupProjectionFacts:
    """Strict transient C2A wakeup projection input. No hidden defaults."""
    if isinstance(raw, RuntimeWakeupProjectionFacts):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeWakeupProjectionFacts: expected object")

    reject_binary_floats(data, path="$.RuntimeWakeupProjectionFacts")
    _reject_unknown(data, _WAKEUP_FACT_FIELDS, "RuntimeWakeupProjectionFacts")
    missing = _WAKEUP_FACT_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeWakeupProjectionFacts: missing fields {sorted(missing)}")

    sleep_raw = data["sleep_profile"]
    if sleep_raw is None:
        _fail("sleep_profile required (no synthetic default)")
    if not isinstance(sleep_raw, Mapping):
        _fail("sleep_profile: expected object")
    validated_profile = validate_synthetic_sleep_profile(sleep_raw)
    sleep_profile = dict(validated_profile._asdict())

    elapsed = _require_non_neg_int(
        data["elapsed_since_last_main_sleep_end_seconds"],
        "elapsed_since_last_main_sleep_end_seconds",
    )
    nap_credit = _require_non_neg_int(
        data["total_nap_awake_credit_min"], "total_nap_awake_credit_min"
    )
    fatigue = _require_bool(data["fatigue_high_relevant"], "fatigue_high_relevant")
    stress = _require_bool(data["stress_relevant"], "stress_relevant")
    social = _require_bool(data["social_low_relevant"], "social_low_relevant")

    horizon = require_canonical_timestamp(
        data["projection_horizon_end"], field="projection_horizon_end"
    )

    boundaries_raw = data["known_boundaries"]
    if isinstance(boundaries_raw, (str, bytes)) or not isinstance(
        boundaries_raw, (list, tuple)
    ):
        _fail("known_boundaries must be a list/tuple")
    # Structural parse only here; due_at >= now is validated at reconcile time
    # against processed_through as `now`.
    parsed_boundaries: list[KnownDecisionBoundary] = []
    seen_keys: set[str] = set()
    for item in boundaries_raw:
        if isinstance(item, KnownDecisionBoundary):
            bdata = item._asdict()
        elif isinstance(item, Mapping):
            bdata = dict(item)
        else:
            _fail("known_boundaries item must be mapping or KnownDecisionBoundary")
        # Validate shape with due_at >= due_at (self) so past-vs-now is deferred.
        b = validate_known_decision_boundary(bdata, now=bdata.get("due_at", horizon))
        if b.decision_key in seen_keys:
            _fail(f"duplicate decision_key in known_boundaries: {b.decision_key}")
        seen_keys.add(b.decision_key)
        parsed_boundaries.append(b)

    return RuntimeWakeupProjectionFacts(
        sleep_profile=sleep_profile,
        elapsed_since_last_main_sleep_end_seconds=elapsed,
        total_nap_awake_credit_min=nap_credit,
        fatigue_high_relevant=fatigue,
        stress_relevant=stress,
        social_low_relevant=social,
        known_boundaries=tuple(parsed_boundaries),
        projection_horizon_end=horizon,
    )


def _assert_main_sleep_profile_matches_window(
    *,
    active: Mapping[str, Any],
    sleep_profile: Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
) -> None:
    """MAIN sleep: supplied sleep_profile must reproduce C1 expected_end_window."""
    if active.get("activity_type") != "SLEEP":
        return
    details = active.get("details")
    if not isinstance(details, Mapping) or details.get("sleep_kind") != "MAIN":
        return

    policy = _require_v2_policy(behavior_policy)
    need = _require_true_int(sleep_profile["sleep_need_min"], "sleep_need_min")
    wake = policy["sleep_policy"]["wake_variation"]
    early = _require_non_neg_int(wake["early_min"], "wake_variation.early_min")
    late = _require_non_neg_int(wake["late_min"], "wake_variation.late_min")
    start = require_canonical_timestamp(active["actual_start"], field="actual_start")
    expected = {
        "earliest": _add_minutes_ts(start, max(1, need - early), field="actual_start"),
        "latest": _add_minutes_ts(start, need + late, field="actual_start"),
    }
    window = active.get("expected_end_window")
    if not isinstance(window, Mapping):
        _fail("MAIN SLEEP requires expected_end_window for sleep_profile integrity")
    actual_window = {
        "earliest": (
            None
            if window.get("earliest") is None
            else require_canonical_timestamp(
                window["earliest"], field="expected_end_window.earliest"
            )
        ),
        "latest": (
            None
            if window.get("latest") is None
            else require_canonical_timestamp(
                window["latest"], field="expected_end_window.latest"
            )
        ),
    }
    if actual_window != expected:
        _fail(
            "MAIN sleep sleep_profile incompatible with ActiveActivity.expected_end_window: "
            f"expected={expected!r} active={actual_window!r}"
        )


def derive_decision_wakeup_context(
    *,
    active_activity: Mapping[str, Any],
    fatigue_high_relevant: bool,
    stress_relevant: bool,
    social_low_relevant: bool,
) -> DecisionWakeupContext:
    """Derive Slice 2C DecisionWakeupContext from active activity + explicit flags."""
    active = _require_runtime_active_activity(active_activity)
    is_sleeping = active["activity_type"] == "SLEEP"
    runtime_ctx = active["runtime_context"]
    if not isinstance(runtime_ctx, Mapping):
        _fail("runtime_context must be a mapping")
    interruptibility = _require_str(runtime_ctx.get("interruptibility"), "interruptibility")
    if interruptibility == "NON_INTERRUPTIBLE":
        planned_end = active.get("planned_end")
        if planned_end is None:
            _fail("NON_INTERRUPTIBLE requires planned_end")
        next_legal = require_canonical_timestamp(planned_end, field="planned_end")
    elif interruptibility == "REASSESSABLE":
        next_legal = None
    else:
        _fail(f"unsupported interruptibility for C2A wakeup context: {interruptibility!r}")

    return validate_decision_wakeup_context(
        DecisionWakeupContext(
            is_sleeping=is_sleeping,
            active_interruptibility=interruptibility,
            next_legal_reassessment_at=next_legal,
            fatigue_high_relevant=_require_bool(
                fatigue_high_relevant, "fatigue_high_relevant"
            ),
            stress_relevant=_require_bool(stress_relevant, "stress_relevant"),
            social_low_relevant=_require_bool(
                social_low_relevant, "social_low_relevant"
            ),
        )
    )


def _activity_end_semantic_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Exact semantic identity for ACTIVITY_END ownership (C1 builder shape)."""
    keys = ("schema_version", "event_id", "event_kind", "due_at", "priority", "payload")
    try:
        a = {k: left[k] for k in keys}
        b = {k: right[k] for k in keys}
    except KeyError:
        return False
    return canonical_json(a) == canonical_json(b)


def reconcile_activity_end_event(
    events: Sequence[Mapping[str, Any]],
    *,
    active_activity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Ensure exact ACTIVITY_END ownership for planned_end only. Never enqueues from latest."""
    active = _require_runtime_active_activity(active_activity)
    instance = _require_str(active["activity_instance_id"], "activity_instance_id")
    planned_end = active.get("planned_end")

    preserved: list[dict[str, Any]] = []
    ends: list[dict[str, Any]] = []
    for raw in events:
        event = deepcopy(dict(raw))
        if event.get("event_kind") == "ACTIVITY_END":
            ends.append(event)
        else:
            preserved.append(event)

    if planned_end is None:
        if ends:
            _fail(
                "open/window ActiveActivity must have zero ACTIVITY_END "
                "(expected latest must never become ACTIVITY_END)"
            )
        out = preserved
    else:
        expected = build_activity_end_event(
            activity_instance_id=instance,
            planned_end=planned_end,
        )
        assert expected is not None
        if not ends:
            preserved.append(deepcopy(expected))
            out = preserved
        else:
            for end in ends:
                payload = end.get("payload") or {}
                other_id = payload.get("activity_instance_id")
                if other_id != instance:
                    _fail(
                        "ACTIVITY_END for another activity_instance_id is forbidden: "
                        f"active={instance!r} queued={other_id!r}"
                    )
            if len(ends) != 1:
                _fail(f"expected exactly one ACTIVITY_END for {instance!r}, found {len(ends)}")
            if not _activity_end_semantic_equal(ends[0], expected):
                _fail(
                    "conflicting ACTIVITY_END for current activity "
                    "(id/due/payload must match C1 deterministic builder)"
                )
            preserved.append(deepcopy(ends[0]))
            out = preserved

    out.sort(key=queue_event_sort_key)
    return out


@dataclass(frozen=True)
class RuntimeActiveQueueResult:
    bundle: RuntimeBundle
    projection: ProjectionResult
    active_boundaries: tuple[KnownDecisionBoundary, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "bundle": {
                "current_state": dict(self.bundle.current_state),
                "schedule_state": dict(self.bundle.schedule_state),
                "relation_state": dict(self.bundle.relation_state),
                "home_state": dict(self.bundle.home_state),
                "consumables_state": dict(self.bundle.consumables_state),
                "wardrobe_state": dict(self.bundle.wardrobe_state),
                "finance_state": dict(self.bundle.finance_state),
            },
            "projection": {
                "immediate_reassessment_reasons": list(
                    self.projection.immediate_reassessment_reasons
                ),
                "future_candidates": [
                    {
                        "due_at": c.due_at,
                        "trigger_kind": c.trigger_kind,
                        "decision_key": c.decision_key,
                        "reason_code": c.reason_code,
                        "metric": c.metric,
                        "target_band": c.target_band,
                        "direction": c.direction,
                        "source_kind": c.source_kind,
                        "source_ref": c.source_ref,
                    }
                    for c in self.projection.future_candidates
                ],
                "selected": (
                    None
                    if self.projection.selected is None
                    else {
                        "due_at": self.projection.selected.due_at,
                        "trigger_kind": self.projection.selected.trigger_kind,
                        "decision_key": self.projection.selected.decision_key,
                        "reason_code": self.projection.selected.reason_code,
                        "metric": self.projection.selected.metric,
                        "target_band": self.projection.selected.target_band,
                        "direction": self.projection.selected.direction,
                        "source_kind": self.projection.selected.source_kind,
                        "source_ref": self.projection.selected.source_ref,
                    }
                ),
                "sleep_projection_mode": self.projection.sleep_projection_mode,
            },
            "active_boundaries": [b._asdict() for b in self.active_boundaries],
        }


def reconcile_active_runtime_queue(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    wakeup_facts: RuntimeWakeupProjectionFacts | Mapping[str, Any],
    consumed_active_boundary_key: str | None = None,
) -> RuntimeActiveQueueResult:
    """Reconcile ACTIVITY_END ownership + managed v2 DECISION_WAKEUP (<=1).

    Updates CurrentState.pending_queue.events only. Surfaces
    projection.immediate_reassessment_reasons unchanged (no fake due-now event).

    ``consumed_active_boundary_key`` is a transient C3 handoff: when non-null it
    must identify exactly one ACTIVE_REASSESSMENT for the current activity at
    ``due_at == processed_through``, and that boundary is excluded for this
    reconcile only (one-shot CONTINUE_CURRENT). Null preserves due-at-now recovery.
    Never persisted. Caller ``known_boundaries`` must not supply any
    ``ACTIVE_REASSESSMENT`` + ``CURRENT_ACTIVITY`` boundary (regardless of
    source_ref / decision_key; C2A-derived boundaries are the sole authority;
    consumed exclusion is not ownership transfer).
    """
    validated_in = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    policy = _require_v2_policy(behavior_policy)
    if policy["behavior_policy_version"] != current_in["behavior_policy_version"]:
        _fail(
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current_in['behavior_policy_version']!r}"
        )

    facts = parse_runtime_wakeup_projection_facts(wakeup_facts)
    processed = require_canonical_timestamp(
        current_in["processed_through"], field="processed_through"
    )
    horizon = facts.projection_horizon_end
    if parse_rfc3339(horizon, field="projection_horizon_end") <= parse_rfc3339(
        processed, field="processed_through"
    ):
        _fail("projection_horizon_end must be > processed_through")

    active = _require_runtime_active_activity(current_in["context"].get("active_activity"))
    _assert_main_sleep_profile_matches_window(
        active=active,
        sleep_profile=facts.sleep_profile,
        behavior_policy=policy,
    )
    # Mandatory expected latest must not already be missed (queue-side counterpart
    # of integrate_active_interval overrun guard).
    _assert_expected_latest_not_missed(active=active, processed_through=processed)

    # Snapshot immutables for post-checks.
    revision_before = current_in["state_revision"]
    history_before = deepcopy(current_in["history"])
    schedule_before = deepcopy(dict(validated_in.schedule_state))
    world_before = {
        "relation": deepcopy(dict(validated_in.relation_state)),
        "home": deepcopy(dict(validated_in.home_state)),
        "consumables": deepcopy(dict(validated_in.consumables_state)),
        "wardrobe": deepcopy(dict(validated_in.wardrobe_state)),
        "finance": deepcopy(dict(validated_in.finance_state)),
    }
    active_before = deepcopy(active)
    human_before = deepcopy(current_in["human_state"])
    rem_before = deepcopy(current_in["integration"]["rate_remainders"])

    events_in = list(current_in["pending_queue"]["events"] or [])
    events_after_end = reconcile_activity_end_event(events_in, active_activity=active)

    raw_active_boundaries = active_reassessment_boundaries(active, now=processed)
    instance_id = _require_str(active["activity_instance_id"], "activity_instance_id")
    # C2A is the sole owner of CURRENT_ACTIVITY ACTIVE_REASSESSMENT boundaries.
    # Reject any caller-supplied ACTIVE_REASSESSMENT + CURRENT_ACTIVITY pair
    # regardless of source_ref or decision_key (wrong-ref / renamed-key injection
    # forbidden; consumed exclusion is not ownership transfer).
    for raw_caller in facts.known_boundaries:
        caller_b = validate_known_decision_boundary(raw_caller, now=processed)
        if (
            caller_b.trigger_kind == "ACTIVE_REASSESSMENT"
            and caller_b.source_kind == "CURRENT_ACTIVITY"
        ):
            _fail(
                "caller known_boundaries must not supply CURRENT_ACTIVITY "
                "ACTIVE_REASSESSMENT "
                f"(decision_key={caller_b.decision_key!r}, "
                f"source_ref={caller_b.source_ref!r}); "
                "C2A-derived active boundaries are the sole authority"
            )

    active_boundaries = _apply_consumed_active_boundary_key(
        raw_active_boundaries,
        consumed_active_boundary_key=consumed_active_boundary_key,
        processed_through=processed,
        activity_instance_id=instance_id,
    )

    # Merge caller known_boundaries + (post-consumption) active boundaries.
    merged_boundaries: list[KnownDecisionBoundary] = []
    seen_keys: set[str] = set()
    for group in (facts.known_boundaries, active_boundaries):
        for boundary in group:
            b = validate_known_decision_boundary(boundary, now=processed)
            if b.decision_key in seen_keys:
                _fail(f"duplicate decision_key in merged boundaries: {b.decision_key}")
            seen_keys.add(b.decision_key)
            merged_boundaries.append(b)

    wakeup_context = derive_decision_wakeup_context(
        active_activity=active,
        fatigue_high_relevant=facts.fatigue_high_relevant,
        stress_relevant=facts.stress_relevant,
        social_low_relevant=facts.social_low_relevant,
    )
    activity_context = _dynamics_activity_context(active)

    new_events, projection = reconcile_v2_decision_wakeups(
        events_after_end,
        character_id=current_in["character_id"],
        policy=policy,
        human_state=current_in["human_state"],
        rate_remainders=current_in["integration"]["rate_remainders"],
        activity_context=activity_context,
        now=processed,
        wakeup_context=wakeup_context,
        sleep_profile=facts.sleep_profile,
        elapsed_since_last_main_sleep_end_seconds=(
            facts.elapsed_since_last_main_sleep_end_seconds
        ),
        total_nap_awake_credit_min=facts.total_nap_awake_credit_min,
        known_boundaries=merged_boundaries,
        projection_horizon_end=horizon,
    )

    out_current = deepcopy(dict(current_in))
    out_current["pending_queue"] = {
        "cursor": 0,
        "events": new_events,
    }
    out_current = validate_checkpoint(out_current)

    if out_current["state_revision"] != revision_before:
        _fail("state_revision must remain unchanged")
    if out_current["history"] != history_before:
        _fail("history must remain unchanged")
    if out_current["processed_through"] != processed:
        _fail("processed_through must remain unchanged during queue reconcile")
    if out_current["human_state"] != human_before:
        _fail("human_state must remain unchanged during queue reconcile")
    if out_current["integration"]["rate_remainders"] != rem_before:
        _fail("rate_remainders must remain unchanged during queue reconcile")
    if out_current["context"]["active_activity"] != active_before:
        _fail("active_activity must remain unchanged during queue reconcile")

    out_bundle = RuntimeBundle(
        current_state=out_current,
        schedule_state=schedule_before,
        relation_state=world_before["relation"],
        home_state=world_before["home"],
        consumables_state=world_before["consumables"],
        wardrobe_state=world_before["wardrobe"],
        finance_state=world_before["finance"],
    )
    out_validated = validate_runtime_bundle(out_bundle, reference_sets=reference_sets)
    return RuntimeActiveQueueResult(
        bundle=out_validated,
        projection=projection,
        active_boundaries=active_boundaries,
    )


__all__ = [
    "RuntimeActiveQueueResult",
    "RuntimeWakeupProjectionFacts",
    "active_reassessment_boundaries",
    "apply_active_window_continuation_guard",
    "derive_decision_wakeup_context",
    "integrate_active_interval",
    "parse_runtime_wakeup_projection_facts",
    "reconcile_active_runtime_queue",
    "reconcile_activity_end_event",
]
