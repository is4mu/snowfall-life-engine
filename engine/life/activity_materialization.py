"""Slice 5B2C1 — selected candidate → runtime ActiveActivity materialization.

Pure in-memory only. Builds ActivityStartSpec + DynamicsContext, starts via the
existing 5B1 bridge, optionally constructs (but does not enqueue) ACTIVITY_END,
and terminalizes immediate social ACCEPT only after a validated successful start.

Does not implement C2A lifecycle integration / wakeup reconcile, C2B
finalization (aside from freezing ``runtime_finish_context`` for later C2B
derivation), or C3 target-time loop. No duration PRNG. No disk/Git/life-branch
writes. Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .activity_runtime import (
    ActivityStartSpec,
    derive_activity_instance_id,
    parse_activity_start_spec,
    start_activity_from_selection,
)
from .checkpoint import validate_checkpoint
from .decisions import ActionCandidate, INTERRUPTIBILITY
from .derived import validate_synthetic_sleep_profile
from .dynamics import BASE_ACTIVITY_CLASSES, SOCIAL_EXPOSURE_CLASSES
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .policy import normalize_policy
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .runtime_decision import (
    RuntimeDecisionFacts,
    RuntimeDecisionFrame,
    build_decision_facts_hash,
    build_materialization_context_hash,
    parse_runtime_decision_facts,
)
from .schema import reject_binary_floats, validate_instance
from .social_response import SocialResponseDecision
from .social_runtime import (
    PRIOR_TERMINAL_REASON,
    PendingImmediateAccept,
    SocialResponseState,
    SuccessfulImmediateSocialStart,
    apply_pending_immediate_accept_after_successful_start,
    parse_pending_immediate_accept,
    validate_social_response_state,
)
from .timeutil import add_minutes, format_rfc3339, parse_rfc3339

# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------

SOCIAL_CONTACT_MODES = frozenset(
    {
        "LIGHTWEIGHT",
        "CALL",
        "LONG_CATCHUP_CALL",
        "LOCAL_OUTING",
        "POST_WORK",
    }
)

MEAL_SOURCES = frozenset(
    {
        "HOME_COOKED",
        "OUTSIDE",
        "CONVENIENCE",
        "DELIVERY",
        "SOCIAL_MEAL",
    }
)

MEAL_EXTENTS = frozenset({"LIGHT", "STANDARD"})

ACTION_KIND_TO_ACTIVITY_TYPE: dict[str, str] = {
    "TRAVEL_TO_COMMITMENT": "TRAVEL",
    "MEAL": "MEAL",
    "SLEEP_MAIN": "SLEEP",
    "REST": "REST",
    "STUDY": "STUDY",
    "WORK": "WORK",
    "KICKBOXING": "KICKBOXING",
    "HOUSEHOLD": "HOUSEHOLD",
    "ERRAND": "ERRAND",
    "SOCIAL_PROMISE": "SOCIAL_CONTACT",
    "SOCIAL_CONTACT": "SOCIAL_CONTACT",
    "PASSIVE_HOME_MEDIA": "PASSIVE_HOME_MEDIA",
    "MUSIC": "MUSIC",
    "MOVIE": "MOVIE",
    "LOCAL_WALK": "LOCAL_WALK",
    "VINTAGE_BROWSING": "VINTAGE_BROWSING",
    "UNSTRUCTURED_REST": "UNSTRUCTURED_REST",
}

HARD_COMMITMENT_KIND_TO_ACTIVITY: dict[str, str] = {
    "SOCIAL": "SOCIAL_CONTACT",
    "ERRAND": "ERRAND",
    "CLASS": "CLASS",
    "EXERCISE": "EXERCISE",
    "APPOINTMENT": "APPOINTMENT",
    "SELF_TASK": "SELF_TASK",
}

# activity_type → (allowed bases, allowed social exposures)
DYNAMICS_COMPATIBILITY: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "SLEEP": (frozenset({"SLEEP"}), frozenset({"NONE"})),
    "SOCIAL_CONTACT": (
        frozenset({"REST_AWAKE", "LIGHT_ACTIVE"}),
        frozenset({"ACTIVE"}),
    ),
    "KICKBOXING": (
        frozenset({"PHYSICALLY_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "EXERCISE": (
        frozenset({"PHYSICALLY_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "TRAVEL": (frozenset({"LIGHT_ACTIVE"}), frozenset({"NONE", "PASSIVE"})),
    "HOUSEHOLD": (frozenset({"LIGHT_ACTIVE"}), frozenset({"NONE", "PASSIVE"})),
    "ERRAND": (frozenset({"LIGHT_ACTIVE"}), frozenset({"NONE", "PASSIVE"})),
    "LOCAL_WALK": (frozenset({"LIGHT_ACTIVE"}), frozenset({"NONE", "PASSIVE"})),
    "VINTAGE_BROWSING": (
        frozenset({"LIGHT_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "STUDY": (
        frozenset({"SEDENTARY_FOCUSED", "LIGHT_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "WORK": (
        frozenset({"SEDENTARY_FOCUSED", "LIGHT_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "CLASS": (
        frozenset({"SEDENTARY_FOCUSED", "LIGHT_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "APPOINTMENT": (
        frozenset({"SEDENTARY_FOCUSED", "LIGHT_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "SELF_TASK": (
        frozenset({"SEDENTARY_FOCUSED", "LIGHT_ACTIVE"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "MEAL": (
        frozenset({"REST_AWAKE", "SEDENTARY_FOCUSED"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "REST": (
        frozenset({"REST_AWAKE", "SEDENTARY_FOCUSED"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "PASSIVE_HOME_MEDIA": (
        frozenset({"REST_AWAKE", "SEDENTARY_FOCUSED"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "MUSIC": (
        frozenset({"REST_AWAKE", "SEDENTARY_FOCUSED"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "MOVIE": (
        frozenset({"REST_AWAKE", "SEDENTARY_FOCUSED"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
    "UNSTRUCTURED_REST": (
        frozenset({"REST_AWAKE", "SEDENTARY_FOCUSED"}),
        frozenset({"NONE", "PASSIVE"}),
    ),
}

_MATERIALIZATION_FACT_FIELDS = frozenset(
    {
        "selected_candidate_id",
        "base_activity_class",
        "social_exposure",
        "meal_source",
        "meal_extent",
        "social_contact_mode",
        "sleep_profile",
    }
)

_IMMEDIATE_ACCEPT_OPPORTUNITY_KINDS = frozenset(
    {"CONTACT_OPPORTUNITY", "POST_WORK_OPPORTUNITY"}
)

OPEN_LEISURE_OR_REST = frozenset(
    {
        "REST",
        "PASSIVE_HOME_MEDIA",
        "MUSIC",
        "MOVIE",
        "LOCAL_WALK",
        "VINTAGE_BROWSING",
        "UNSTRUCTURED_REST",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label}: expected non-empty string")
    return value


def _require_true_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label}: expected true int (no bool/float/string)")
    return value


def _require_non_neg_int(value: object, label: str) -> int:
    n = _require_true_int(value, label)
    if n < 0:
        _fail(f"{label}: expected >= 0")
    return n


def _add_minutes_ts(ts: str, minutes: int, *, field: str) -> str:
    return format_rfc3339(add_minutes(parse_rfc3339(ts, field=field), minutes))


def _reject_unknown(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    extra = set(data.keys()) - allowed
    if extra:
        _fail(f"{label}: unexpected fields {sorted(extra)}")


def _require_v2_policy(policy: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(policy, Mapping):
        _fail("behavior_policy must be a mapping")
    normalized = normalize_policy(dict(policy))
    if normalized.get("schema_version") != 2 or normalized.get("policy_mode") != "PRODUCTION":
        _fail(
            "5B2C1 materialization requires schema_version=2 policy_mode=PRODUCTION "
            f"(got schema_version={normalized.get('schema_version')!r} "
            f"policy_mode={normalized.get('policy_mode')!r})"
        )
    return normalized


def _validate_pending_policy_durations(
    pending: PendingImmediateAccept,
    policy: Mapping[str, Any],
    *,
    available_window_min: int,
) -> None:
    """Bind CALL / POST_WORK pending durations to Slice 2H window-capped policy.

    Exact 2H ACCEPT semantics:
    - duration_min == policy duration_min
    - duration_max == min(policy duration_max, available_window_min)
    - LIGHTWEIGHT remains null/null
    - available_window_min < policy min ⇒ ACCEPT pending must not exist
    """
    if pending.contact_mode == "LIGHTWEIGHT":
        if pending.duration_min is not None or pending.duration_max is not None:
            _fail("LIGHTWEIGHT pending must have null durations")
        return
    if isinstance(available_window_min, bool) or not isinstance(available_window_min, int):
        _fail("available_window_min must be int for pending duration bind")
    if available_window_min < 0:
        _fail("available_window_min must be >= 0")
    social = policy.get("social_policy")
    if not isinstance(social, Mapping):
        _fail("behavior_policy.social_policy required for immediate SOCIAL")
    durations = social.get("contact_mode_durations")
    if not isinstance(durations, Mapping):
        _fail("social_policy.contact_mode_durations required")
    if pending.contact_mode not in durations:
        _fail(
            f"missing social_policy.contact_mode_durations.{pending.contact_mode}"
        )
    block = durations[pending.contact_mode]
    if not isinstance(block, Mapping):
        _fail(
            f"social_policy.contact_mode_durations.{pending.contact_mode} "
            "must be a mapping"
        )
    if set(block.keys()) != {"duration_min", "duration_max"}:
        _fail(
            f"social_policy.contact_mode_durations.{pending.contact_mode} "
            "must have exactly duration_min/max"
        )
    policy_min = block["duration_min"]
    policy_max = block["duration_max"]
    if isinstance(policy_min, bool) or not isinstance(policy_min, int):
        _fail(f"policy duration_min must be int for {pending.contact_mode}")
    if isinstance(policy_max, bool) or not isinstance(policy_max, int):
        _fail(f"policy duration_max must be int for {pending.contact_mode}")
    if available_window_min < policy_min:
        _fail(
            "available_window_min < policy duration_min; "
            "ACCEPT PendingImmediateAccept must not exist "
            f"(window={available_window_min}, policy_min={policy_min})"
        )
    expected_min = policy_min
    expected_max = min(policy_max, available_window_min)
    if (
        pending.duration_min != expected_min
        or pending.duration_max != expected_max
    ):
        _fail(
            "PendingImmediateAccept durations must equal 2H window-capped "
            f"social_policy.contact_mode_durations.{pending.contact_mode} "
            f"(got {pending.duration_min}..{pending.duration_max}, "
            f"expected {expected_min}..{expected_max} "
            f"= min(policy_max={policy_max}, "
            f"available_window_min={available_window_min}))"
        )


def _bind_immediate_social_from_pending(
    *,
    candidate: ActionCandidate,
    pending: PendingImmediateAccept,
    behavior_policy: Mapping[str, Any] | None = None,
    available_window_min: int | None = None,
) -> PendingImmediateAccept:
    """Strictly bind and authorize immediate SOCIAL payload from pending handoff."""
    if candidate.source_kind != "SOCIAL":
        _fail("immediate social bind requires SOCIAL source candidate")
    if pending.opportunity_id != candidate.source_ref:
        _fail("pending_immediate_accept.opportunity_id mismatch vs source_ref")
    expected_response_id = stable_id(
        "social-response", pending.opportunity_id, "ACCEPT"
    )
    if pending.response_id != expected_response_id:
        _fail(
            "pending_immediate_accept.response_id must equal "
            'stable_id("social-response", opportunity_id, "ACCEPT")'
        )
    if pending.opportunity_kind not in _IMMEDIATE_ACCEPT_OPPORTUNITY_KINDS:
        _fail(
            "pending_immediate_accept.opportunity_kind must be "
            "CONTACT_OPPORTUNITY or POST_WORK_OPPORTUNITY "
            f"(got {pending.opportunity_kind!r})"
        )
    expected_resolved = stable_id(
        "social-resolved-opportunity", pending.opportunity_id
    )
    if pending.selected_opportunity_key != expected_resolved:
        _fail(
            "pending_immediate_accept.selected_opportunity_key mismatch vs stable-id "
            f"(got {pending.selected_opportunity_key!r}, expected {expected_resolved!r})"
        )
    if pending.selected_opportunity_key != candidate.candidate_key:
        _fail(
            "pending_immediate_accept.selected_opportunity_key must equal "
            "selected candidate.candidate_key"
        )
    if pending.selected_candidate_key != candidate.candidate_key:
        _fail(
            "pending_immediate_accept.selected_candidate_key must equal "
            "selected candidate.candidate_key"
        )
    if pending.selected_candidate_id != candidate.candidate_id:
        _fail(
            "pending_immediate_accept.selected_candidate_id must equal "
            "selected candidate.candidate_id"
        )
    if pending.contact_mode not in SOCIAL_CONTACT_MODES:
        _fail(f"pending contact_mode invalid: {pending.contact_mode!r}")
    if behavior_policy is not None:
        if available_window_min is None:
            _fail(
                "available_window_min required when validating pending "
                "policy durations"
            )
        _validate_pending_policy_durations(
            pending,
            behavior_policy,
            available_window_min=available_window_min,
        )
    return pending


def _parse_social_response_decision(
    raw: SocialResponseDecision | Mapping[str, Any],
) -> SocialResponseDecision:
    """Strict 2H-shaped SocialResponseDecision parser (dataclass and mapping).

    Kept for diagnostic/API compatibility. Immediate SOCIAL materialization
    authority is PendingImmediateAccept, not this collection.
    """
    if isinstance(raw, SocialResponseDecision):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("SocialResponseDecision: expected object")
    reject_binary_floats(data, path="$.SocialResponseDecision")
    allowed = frozenset(
        {
            "response_id",
            "opportunity_id",
            "person_id",
            "opportunity_kind",
            "response",
            "reason_code",
            "contact_mode",
            "duration_min",
            "duration_max",
            "resolved_opportunity_key",
            "commitment_proposal_id",
            "rule_ids",
        }
    )
    _reject_unknown(data, allowed, "SocialResponseDecision")
    missing = allowed - set(data)
    if missing:
        _fail(f"SocialResponseDecision: missing fields {sorted(missing)}")

    opportunity_id = _require_str(data["opportunity_id"], "opportunity_id")
    response = _require_str(data["response"], "response")
    response_id = _require_str(data["response_id"], "response_id")
    expected_response_id = stable_id("social-response", opportunity_id, response)
    if response_id != expected_response_id:
        _fail(
            "SocialResponseDecision.response_id must equal "
            'stable_id("social-response", opportunity_id, response) '
            f"(got {response_id!r}, expected {expected_response_id!r})"
        )

    opportunity_kind = _require_str(data["opportunity_kind"], "opportunity_kind")
    contact_mode = data["contact_mode"]
    if contact_mode is not None:
        contact_mode = _require_str(contact_mode, "contact_mode")
        if contact_mode not in SOCIAL_CONTACT_MODES:
            _fail(f"SocialResponseDecision.contact_mode invalid: {contact_mode!r}")
    duration_min = data["duration_min"]
    if duration_min is not None:
        duration_min = _require_true_int(duration_min, "duration_min")
    duration_max = data["duration_max"]
    if duration_max is not None:
        duration_max = _require_true_int(duration_max, "duration_max")
    resolved = data["resolved_opportunity_key"]
    if resolved is not None:
        resolved = _require_str(resolved, "resolved_opportunity_key")
    proposal = data["commitment_proposal_id"]
    if proposal is not None:
        proposal = _require_str(proposal, "commitment_proposal_id")
    rule_ids_raw = data["rule_ids"]
    if not isinstance(rule_ids_raw, (list, tuple)) or isinstance(
        rule_ids_raw, (str, bytes)
    ):
        _fail("SocialResponseDecision.rule_ids: expected list/tuple")
    rule_ids = tuple(_require_str(x, "rule_id") for x in rule_ids_raw)

    reason_code = _require_str(data["reason_code"], "reason_code")
    if (
        response == "ACCEPT"
        and opportunity_kind in _IMMEDIATE_ACCEPT_OPPORTUNITY_KINDS
        and reason_code != PRIOR_TERMINAL_REASON
    ):
        expected_resolved = stable_id("social-resolved-opportunity", opportunity_id)
        if resolved != expected_resolved:
            _fail(
                "immediate ACCEPT SocialResponseDecision.resolved_opportunity_key "
                'must equal stable_id("social-resolved-opportunity", opportunity_id) '
                f"(got {resolved!r}, expected {expected_resolved!r})"
            )

    return SocialResponseDecision(
        response_id=response_id,
        opportunity_id=opportunity_id,
        person_id=_require_str(data["person_id"], "person_id"),
        opportunity_kind=opportunity_kind,
        response=response,
        reason_code=reason_code,
        contact_mode=contact_mode,
        duration_min=duration_min,
        duration_max=duration_max,
        resolved_opportunity_key=resolved,
        commitment_proposal_id=proposal,
        rule_ids=rule_ids,
    )


# ---------------------------------------------------------------------------
# DynamicsContext + materialization facts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DynamicsContext:
    schema_version: int
    base_activity_class: str
    social_exposure: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "base_activity_class": self.base_activity_class,
            "social_exposure": self.social_exposure,
        }


def parse_dynamics_context(raw: Mapping[str, Any] | DynamicsContext) -> DynamicsContext:
    if isinstance(raw, DynamicsContext):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("DynamicsContext: expected object")
    reject_binary_floats(data, path="$.DynamicsContext")
    _reject_unknown(
        data,
        frozenset({"schema_version", "base_activity_class", "social_exposure"}),
        "DynamicsContext",
    )
    missing = {"schema_version", "base_activity_class", "social_exposure"} - set(data)
    if missing:
        _fail(f"DynamicsContext: missing fields {sorted(missing)}")
    version = data["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        _fail("DynamicsContext.schema_version must be 1")
    base = _require_str(data["base_activity_class"], "base_activity_class")
    social = _require_str(data["social_exposure"], "social_exposure")
    if base not in BASE_ACTIVITY_CLASSES:
        _fail(f"invalid base_activity_class: {base!r}")
    if social not in SOCIAL_EXPOSURE_CLASSES:
        _fail(f"invalid social_exposure: {social!r}")
    return DynamicsContext(
        schema_version=1,
        base_activity_class=base,
        social_exposure=social,
    )


@dataclass(frozen=True)
class RuntimeActivityMaterializationFacts:
    selected_candidate_id: str
    base_activity_class: str
    social_exposure: str
    meal_source: str | None
    meal_extent: str | None
    social_contact_mode: str | None
    sleep_profile: Mapping[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        # Non-coercive: malformed sleep_profile must reach the parser intact.
        sleep_profile: Any
        if self.sleep_profile is None:
            sleep_profile = None
        elif isinstance(self.sleep_profile, Mapping):
            sleep_profile = dict(self.sleep_profile)
        else:
            sleep_profile = self.sleep_profile
        return {
            "selected_candidate_id": self.selected_candidate_id,
            "base_activity_class": self.base_activity_class,
            "social_exposure": self.social_exposure,
            "meal_source": self.meal_source,
            "meal_extent": self.meal_extent,
            "social_contact_mode": self.social_contact_mode,
            "sleep_profile": sleep_profile,
        }


def parse_runtime_activity_materialization_facts(
    raw: Mapping[str, Any] | RuntimeActivityMaterializationFacts,
) -> RuntimeActivityMaterializationFacts:
    if isinstance(raw, RuntimeActivityMaterializationFacts):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeActivityMaterializationFacts: expected object")
    reject_binary_floats(data, path="$.RuntimeActivityMaterializationFacts")
    _reject_unknown(data, _MATERIALIZATION_FACT_FIELDS, "RuntimeActivityMaterializationFacts")
    missing = {
        "selected_candidate_id",
        "base_activity_class",
        "social_exposure",
        "meal_source",
        "meal_extent",
        "social_contact_mode",
        "sleep_profile",
    } - set(data)
    if missing:
        _fail(f"RuntimeActivityMaterializationFacts: missing fields {sorted(missing)}")

    selected = _require_str(data["selected_candidate_id"], "selected_candidate_id")
    base = _require_str(data["base_activity_class"], "base_activity_class")
    social = _require_str(data["social_exposure"], "social_exposure")
    if base not in BASE_ACTIVITY_CLASSES:
        _fail(f"invalid base_activity_class: {base!r}")
    if social not in SOCIAL_EXPOSURE_CLASSES:
        _fail(f"invalid social_exposure: {social!r}")

    meal_source = data["meal_source"]
    if meal_source is not None:
        meal_source = _require_str(meal_source, "meal_source")
        if meal_source not in MEAL_SOURCES:
            _fail(f"invalid meal_source: {meal_source!r}")

    meal_extent = data["meal_extent"]
    if meal_extent is not None:
        meal_extent = _require_str(meal_extent, "meal_extent")
        if meal_extent not in MEAL_EXTENTS:
            _fail(f"invalid meal_extent: {meal_extent!r}")

    social_contact_mode = data["social_contact_mode"]
    if social_contact_mode is not None:
        social_contact_mode = _require_str(social_contact_mode, "social_contact_mode")
        if social_contact_mode not in SOCIAL_CONTACT_MODES:
            _fail(f"invalid social_contact_mode: {social_contact_mode!r}")

    sleep_profile_raw = data["sleep_profile"]
    sleep_profile: dict[str, Any] | None
    if sleep_profile_raw is None:
        sleep_profile = None
    elif isinstance(sleep_profile_raw, Mapping):
        # Validate via Slice 2B sleep-profile contract; store as plain dict.
        validated = validate_synthetic_sleep_profile(sleep_profile_raw)
        sleep_profile = dict(validated._asdict())
    else:
        _fail("sleep_profile: expected object or null")

    return RuntimeActivityMaterializationFacts(
        selected_candidate_id=selected,
        base_activity_class=base,
        social_exposure=social,
        meal_source=meal_source,
        meal_extent=meal_extent,
        social_contact_mode=social_contact_mode,
        sleep_profile=sleep_profile,
    )


def validate_dynamics_for_activity_type(
    *,
    activity_type: str,
    base_activity_class: str,
    social_exposure: str,
) -> DynamicsContext:
    matrix = DYNAMICS_COMPATIBILITY.get(activity_type)
    if matrix is None:
        _fail(f"no dynamics compatibility matrix for activity_type={activity_type!r}")
    allowed_bases, allowed_social = matrix
    if base_activity_class not in allowed_bases:
        _fail(
            f"dynamics mismatch: activity_type={activity_type!r} "
            f"base_activity_class={base_activity_class!r} "
            f"allowed={sorted(allowed_bases)}"
        )
    if social_exposure not in allowed_social:
        _fail(
            f"dynamics mismatch: activity_type={activity_type!r} "
            f"social_exposure={social_exposure!r} "
            f"allowed={sorted(allowed_social)}"
        )
    return DynamicsContext(
        schema_version=1,
        base_activity_class=base_activity_class,
        social_exposure=social_exposure,
    )


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


def _find_exactly_one(
    rows: Sequence[Mapping[str, Any]],
    *,
    id_key: str,
    target_id: str,
    label: str,
) -> Mapping[str, Any]:
    matches = [r for r in rows if r.get(id_key) == target_id]
    if len(matches) != 1:
        _fail(
            f"{label}: expected exactly one {id_key}={target_id!r}, "
            f"found {len(matches)}"
        )
    return matches[0]


def _resolve_commitment(
    schedule_state: Mapping[str, Any], source_ref: str
) -> Mapping[str, Any]:
    commitments = schedule_state.get("commitments")
    if not isinstance(commitments, list):
        _fail("schedule_state.commitments must be a list")
    return _find_exactly_one(
        commitments,
        id_key="commitment_id",
        target_id=source_ref,
        label="commitment",
    )


def _resolve_task(schedule_state: Mapping[str, Any], source_ref: str) -> Mapping[str, Any]:
    tasks = schedule_state.get("obligation_tasks")
    if not isinstance(tasks, list):
        _fail("schedule_state.obligation_tasks must be a list")
    return _find_exactly_one(
        tasks,
        id_key="task_id",
        target_id=source_ref,
        label="obligation_task",
    )


def _find_forward_route(
    *,
    routes: Sequence[Mapping[str, Any]],
    origin: str,
    destination: str,
) -> Mapping[str, Any]:
    forward = [
        r
        for r in routes
        if r.get("origin_location_id") == origin
        and r.get("destination_location_id") == destination
    ]
    if not forward:
        _fail(f"no forward route for {origin!r}->{destination!r}")

    def _key(r: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            r["origin_location_id"],
            r["destination_location_id"],
            r["mode"],
            int(r["duration_min"]),
            int(r["duration_max"]),
            r["effort_class"],
        )

    keys = {_key(r) for r in forward}
    if len(keys) > 1:
        _fail(
            f"ambiguous non-identical forward routes for {origin}->{destination}: "
            f"{sorted(r['route_profile_id'] for r in forward)}"
        )
    return sorted(forward, key=lambda r: r["route_profile_id"])[0]


def resolve_activity_type(
    *,
    action_kind: str,
    commitment: Mapping[str, Any] | None,
) -> str:
    """Resolve only the existing action/commitment mapping, without selection."""
    if action_kind == "CONTINUE_CURRENT":
        _fail("CONTINUE_CURRENT cannot start a new activity")
    if action_kind == "HARD_COMMITMENT_ACTIVITY":
        if commitment is None:
            _fail("HARD_COMMITMENT_ACTIVITY requires a resolved commitment")
        kind = commitment.get("kind")
        mapped = HARD_COMMITMENT_KIND_TO_ACTIVITY.get(kind)  # type: ignore[arg-type]
        if mapped is None:
            _fail(f"unsupported HARD_COMMITMENT_ACTIVITY commitment.kind={kind!r}")
        return mapped
    mapped_direct = ACTION_KIND_TO_ACTIVITY_TYPE.get(action_kind)
    if mapped_direct is None:
        _fail(f"unknown ActionKind for materialization: {action_kind!r}")
    return mapped_direct


@dataclass(frozen=True)
class RuntimeMaterializationContext:
    """Exact resolved C3 authority, with no materialization value selection."""

    selected_candidate_id: str
    selected_candidate_key: str
    action_kind: str
    source_kind: str
    source_ref: str
    activity_type: str
    pending_immediate_accept: PendingImmediateAccept | None

    def __post_init__(self) -> None:
        for name in ("selected_candidate_id", "selected_candidate_key", "action_kind",
                     "source_kind", "source_ref", "activity_type"):
            _require_str(getattr(self, name), name)
        pending = self.pending_immediate_accept
        if self.source_kind == "SOCIAL":
            if pending is None:
                _fail("SOCIAL materialization context requires pending handoff")
            pending = parse_pending_immediate_accept(pending)
            if (pending.selected_candidate_id != self.selected_candidate_id
                    or pending.selected_candidate_key != self.selected_candidate_key
                    or pending.opportunity_id != self.source_ref
                    or self.action_kind != "SOCIAL_CONTACT"
                    or self.activity_type != "SOCIAL_CONTACT"):
                _fail("context pending handoff differs from selected social candidate")
        elif pending is not None:
            _fail("non-social materialization forbids pending handoff")
        object.__setattr__(self, "pending_immediate_accept", pending)


def build_runtime_materialization_context(
    *, bundle: RuntimeBundle, frame: RuntimeDecisionFrame,
    pending_immediate_accept: PendingImmediateAccept | Mapping[str, Any] | None,
) -> RuntimeMaterializationContext:
    """Project an already-resolved no-active selection; never run a resolver."""
    current = bundle.current_state
    if current["context"]["active_activity"] is not None:
        _fail("materialization context requires no active activity")
    selected = frame.selected_candidate
    if selected is None or frame.resolution.result_kind != "SELECT_CANDIDATE":
        _fail("materialization context requires SELECT_CANDIDATE")
    if (selected.candidate_id != frame.resolution.selected_candidate_id
            or selected.candidate_key != frame.resolution.selected_candidate_key
            or selected.action_kind != frame.resolution.selected_action_kind):
        _fail("selected candidate differs from resolved selection")
    commitment = (_resolve_commitment(bundle.schedule_state, selected.source_ref)
                  if selected.source_kind == "COMMITMENT" else None)
    activity_type = resolve_activity_type(action_kind=selected.action_kind,
                                          commitment=commitment)
    pending = None
    if selected.source_kind == "SOCIAL":
        if pending_immediate_accept is None:
            _fail("SOCIAL materialization context requires pending handoff")
        pending = _bind_immediate_social_from_pending(
            candidate=selected,
            pending=parse_pending_immediate_accept(pending_immediate_accept),
        )
        if (pending.character_id != current["character_id"]
                or pending.decided_at != current["processed_through"]
                or pending.decision_key != frame.resolution.decision_key):
            _fail("pending handoff differs from current decision boundary")
    elif pending_immediate_accept is not None:
        _fail("non-social materialization forbids pending handoff")
    return RuntimeMaterializationContext(
        selected_candidate_id=selected.candidate_id,
        selected_candidate_key=selected.candidate_key,
        action_kind=selected.action_kind, source_kind=selected.source_kind,
        source_ref=selected.source_ref, activity_type=activity_type,
        pending_immediate_accept=pending,
    )


def validate_runtime_materialization_context(
    context: RuntimeMaterializationContext, *, bundle: RuntimeBundle,
    frame: RuntimeDecisionFrame,
    pending_immediate_accept: PendingImmediateAccept | Mapping[str, Any] | None,
) -> RuntimeMaterializationContext:
    expected = build_runtime_materialization_context(
        bundle=bundle, frame=frame, pending_immediate_accept=pending_immediate_accept)
    if context != expected:
        _fail("materialization context differs from exact resolved authority")
    return expected


def _companions_from_participants(participants: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    if not isinstance(participants, (list, tuple)):
        _fail("commitment.participants must be a list")
    ids: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(participants):
        pid = _require_str(raw, f"participants[{i}]")
        if pid not in seen:
            seen.add(pid)
            ids.append(pid)
    ids.sort()
    return tuple({"entity_id": pid} for pid in ids)


# ---------------------------------------------------------------------------
# End semantics
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EndPlan:
    planned_end: str | None
    expected_end: str | None
    expected_end_window: dict[str, Any] | None


def _bounded_window(now: str, duration_min: int, duration_max: int) -> _EndPlan:
    if duration_min < 1:
        _fail(f"duration_min must be >= 1, got {duration_min}")
    if duration_max < duration_min:
        _fail(f"impossible bounds: duration_max={duration_max} < duration_min={duration_min}")
    return _EndPlan(
        planned_end=None,
        expected_end=None,
        expected_end_window={
            "earliest": _add_minutes_ts(now, duration_min, field="now"),
            "latest": _add_minutes_ts(now, duration_max, field="now"),
        },
    )


def _open_reassessable(now: str, available_window_min: int) -> _EndPlan:
    # Deterministic latest bound only from RuntimeDecisionFacts.available_window_min.
    return _EndPlan(
        planned_end=None,
        expected_end=None,
        expected_end_window={
            "earliest": None,
            "latest": _add_minutes_ts(now, available_window_min, field="now"),
        },
    )


def _exact_source_end(planned_end: str) -> _EndPlan:
    end = _require_str(planned_end, "planned_end")
    return _EndPlan(planned_end=end, expected_end=end, expected_end_window=None)


def _cap_latest(duration_max: int, available_window_min: int) -> int:
    return min(duration_max, available_window_min)


def _meal_duration_family(*, meal_extent: str, meal_source: str) -> str:
    if meal_extent == "LIGHT":
        return "LIGHT"
    if meal_extent != "STANDARD":
        _fail(f"unsupported meal_extent: {meal_extent!r}")
    if meal_source in {"CONVENIENCE", "DELIVERY"}:
        return "QUICK_STANDARD"
    if meal_source == "HOME_COOKED":
        return "HOME_COOKED_STANDARD"
    if meal_source == "OUTSIDE":
        return "OUTSIDE_STANDARD"
    if meal_source == "SOCIAL_MEAL":
        return "SOCIAL_MEAL_STANDARD"
    _fail(f"no meal duration family for source={meal_source!r} extent={meal_extent!r}")
    raise AssertionError("unreachable")


def _policy_duration_range(
    block: Mapping[str, Any], *, label: str
) -> tuple[int, int]:
    if not isinstance(block, Mapping):
        _fail(f"{label}: expected duration range mapping")
    if "duration_min" not in block or "duration_max" not in block:
        _fail(f"{label}: must include duration_min/max")
    dmin = _require_true_int(block["duration_min"], f"{label}.duration_min")
    dmax = _require_true_int(block["duration_max"], f"{label}.duration_max")
    if dmin < 1 or dmax < dmin:
        _fail(f"{label}: invalid duration range {dmin}..{dmax}")
    return dmin, dmax


# ---------------------------------------------------------------------------
# Materialize ActivityStartSpec
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MaterializedActivityPlan:
    """Transient plan: start spec + dynamics + finish context (post-5B1 install)."""

    start_spec: ActivityStartSpec
    dynamics_context: DynamicsContext
    activity_type: str
    runtime_finish_context: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "start_spec": self.start_spec.as_dict(),
            "dynamics_context": self.dynamics_context.as_dict(),
            "activity_type": self.activity_type,
            "runtime_finish_context": dict(self.runtime_finish_context),
        }


def build_runtime_finish_context(
    *,
    action_kind: str,
    source_kind: str,
    sleep_profile: Mapping[str, Any] | None,
    commitment: Mapping[str, Any] | None,
    schedule_state: Mapping[str, Any],
    source_ref: str,
) -> dict[str, Any]:
    """Freeze start-time factual inputs for C2B end-effect derivation.

    Action-specific gating: only the relevant nullable field is populated.
    """
    ctx: dict[str, Any] = {
        "schema_version": 1,
        "sleep_need_min": None,
        "travel_destination_location_id": None,
        "task_effort_remaining_at_start_min": None,
    }
    if action_kind == "SLEEP_MAIN":
        if sleep_profile is None:
            _fail("SLEEP_MAIN requires sleep_profile for runtime_finish_context")
        ctx["sleep_need_min"] = _require_true_int(
            sleep_profile["sleep_need_min"], "sleep_need_min"
        )
    elif action_kind == "TRAVEL_TO_COMMITMENT":
        if commitment is None:
            _fail("TRAVEL_TO_COMMITMENT requires commitment for finish context")
        dest = commitment.get("location_id")
        if not isinstance(dest, str) or not dest:
            _fail("TRAVEL_TO_COMMITMENT requires commitment.location_id")
        ctx["travel_destination_location_id"] = dest
    elif action_kind in {"STUDY", "ERRAND"} and source_kind == "TASK":
        task = _resolve_task(schedule_state, source_ref)
        ctx["task_effort_remaining_at_start_min"] = _require_true_int(
            task["effort_remaining_min"], "effort_remaining_min"
        )
    return ctx



def materialize_activity_plan(
    *,
    selected_candidate: ActionCandidate | Mapping[str, Any],
    decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
    materialization_facts: RuntimeActivityMaterializationFacts | Mapping[str, Any],
    schedule_state: Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    now: str,
    current_location_id: str,
    social_responses: Sequence[SocialResponseDecision | Mapping[str, Any]] = (),
    pending_immediate_accept: PendingImmediateAccept | Mapping[str, Any] | None = None,
) -> MaterializedActivityPlan:
    """Build ActivityStartSpec + DynamicsContext for a selected candidate.

    Does not start the activity, enqueue events, or mutate inputs.
    """
    if isinstance(selected_candidate, ActionCandidate):
        cand = selected_candidate
    elif isinstance(selected_candidate, Mapping):
        # Strict field reconstruction without re-running Slice 2D selection.
        from .decisions import candidate_id_for  # noqa: F401 — kept for clarity

        reject_binary_floats(dict(selected_candidate), path="$.selected_candidate")
        allowed = frozenset(
            {
                "candidate_key",
                "candidate_id",
                "action_kind",
                "priority_tier",
                "source_kind",
                "source_ref",
                "soft_candidate",
                "time_feasible",
                "location_feasible",
                "physical_feasible",
                "domain_guard_satisfied",
                "local_preference_permille",
                "activity_instance_id",
                "rule_ids",
            }
        )
        _reject_unknown(selected_candidate, allowed, "selected_candidate")
        rule_ids_raw = selected_candidate.get("rule_ids")
        if not isinstance(rule_ids_raw, (list, tuple)) or isinstance(
            rule_ids_raw, (str, bytes)
        ):
            _fail("selected_candidate.rule_ids: expected list/tuple")
        pref = selected_candidate.get("local_preference_permille")
        if pref is not None:
            pref = _require_true_int(pref, "local_preference_permille")
        activity_instance_id = selected_candidate.get("activity_instance_id")
        if activity_instance_id is not None:
            activity_instance_id = _require_str(
                activity_instance_id, "activity_instance_id"
            )
        soft = selected_candidate.get("soft_candidate")
        if not isinstance(soft, bool):
            _fail("soft_candidate: expected bool")
        for flag_name in (
            "time_feasible",
            "location_feasible",
            "physical_feasible",
            "domain_guard_satisfied",
        ):
            if not isinstance(selected_candidate.get(flag_name), bool):
                _fail(f"{flag_name}: expected bool")
        cand = ActionCandidate(
            candidate_key=_require_str(
                selected_candidate.get("candidate_key"), "candidate_key"
            ),
            candidate_id=_require_str(
                selected_candidate.get("candidate_id"), "candidate_id"
            ),
            action_kind=_require_str(
                selected_candidate.get("action_kind"), "action_kind"
            ),
            priority_tier=_require_str(
                selected_candidate.get("priority_tier"), "priority_tier"
            ),
            source_kind=_require_str(
                selected_candidate.get("source_kind"), "source_kind"
            ),
            source_ref=_require_str(selected_candidate.get("source_ref"), "source_ref"),
            soft_candidate=soft,
            time_feasible=selected_candidate["time_feasible"],
            location_feasible=selected_candidate["location_feasible"],
            physical_feasible=selected_candidate["physical_feasible"],
            domain_guard_satisfied=selected_candidate["domain_guard_satisfied"],
            local_preference_permille=pref,
            activity_instance_id=activity_instance_id,
            rule_ids=tuple(_require_str(x, "rule_id") for x in rule_ids_raw),
        )
    else:
        _fail("selected_candidate: expected ActionCandidate or mapping")

    facts = parse_runtime_decision_facts(decision_facts, now=now)
    mat = parse_runtime_activity_materialization_facts(materialization_facts)
    policy = _require_v2_policy(behavior_policy)
    now_c = _require_str(now, "now")
    location = _require_str(current_location_id, "current_location_id")

    if mat.selected_candidate_id != cand.candidate_id:
        _fail(
            "materialization facts selected_candidate_id mismatch "
            f"(facts={mat.selected_candidate_id!r} candidate={cand.candidate_id!r})"
        )

    pending_for_bind: PendingImmediateAccept | None = None
    if pending_immediate_accept is not None:
        pending_for_bind = parse_pending_immediate_accept(pending_immediate_accept)

    commitment: Mapping[str, Any] | None = None
    social_pending: PendingImmediateAccept | None = None

    if cand.source_kind == "COMMITMENT":
        commitment = _resolve_commitment(schedule_state, cand.source_ref)
    elif cand.source_kind == "SOCIAL":
        if pending_for_bind is None:
            _fail(
                "selected SOCIAL requires pending_immediate_accept "
                "(authoritative 5B2B handoff)"
            )
        social_pending = _bind_immediate_social_from_pending(
            candidate=cand,
            pending=pending_for_bind,
            behavior_policy=policy,
            available_window_min=facts.available_window_min,
        )
        # Optional diagnostic parse only; never authority for person/mode/duration.
        if social_responses:
            _ = tuple(_parse_social_response_decision(r) for r in social_responses)
    elif social_responses:
        _ = tuple(_parse_social_response_decision(r) for r in social_responses)

    activity_type = resolve_activity_type(
        action_kind=cand.action_kind, commitment=commitment
    )

    # Field gating for materialization facts.
    if mat.meal_source is not None and cand.action_kind != "MEAL":
        _fail("meal_source is only allowed for MEAL")
    if mat.meal_extent is not None and cand.action_kind != "MEAL":
        _fail("meal_extent is only allowed for MEAL")
    if mat.sleep_profile is not None and cand.action_kind != "SLEEP_MAIN":
        _fail("sleep_profile is only allowed for SLEEP_MAIN")
    if cand.action_kind == "SLEEP_MAIN" and mat.sleep_profile is None:
        _fail("SLEEP_MAIN requires sleep_profile")

    # social_contact_mode only when scheduled social lacks authoritative 2H mode.
    scheduled_social = (
        commitment is not None
        and activity_type == "SOCIAL_CONTACT"
        and cand.source_kind == "COMMITMENT"
    )
    if mat.social_contact_mode is not None and not scheduled_social:
        _fail(
            "social_contact_mode is only allowed for scheduled COMMITMENT "
            "social activity without an authoritative 2H mode"
        )
    if social_pending is not None and mat.social_contact_mode is not None:
        _fail(
            "social_contact_mode must be null when immediate 2H pending "
            "already owns the contact mode"
        )

    dynamics = validate_dynamics_for_activity_type(
        activity_type=activity_type,
        base_activity_class=mat.base_activity_class,
        social_exposure=mat.social_exposure,
    )

    # Details
    details: Any = None
    contact_mode: str | None = None
    if activity_type == "SLEEP":
        details = {"sleep_kind": "MAIN"}
    elif activity_type == "MEAL":
        if mat.meal_source is None:
            _fail("MEAL requires meal_source")
        if mat.meal_extent is None:
            _fail("MEAL requires RuntimeActivityMaterializationFacts.meal_extent")
        if mat.meal_extent not in MEAL_EXTENTS:
            _fail(f"invalid meal_extent: {mat.meal_extent!r}")
        details = {
            "meal_source": mat.meal_source,
            "meal_extent": mat.meal_extent,
        }
    elif activity_type == "SOCIAL_CONTACT":
        if social_pending is not None:
            contact_mode = social_pending.contact_mode
        elif commitment is not None:
            if commitment.get("source_kind") == "EXOGENOUS_SOCIAL":
                if mat.social_contact_mode is not None:
                    _fail(
                        "EXOGENOUS_SOCIAL scheduled social must not supply "
                        "social_contact_mode (mode is LOCAL_OUTING)"
                    )
                contact_mode = "LOCAL_OUTING"
            else:
                if mat.social_contact_mode is None:
                    _fail(
                        "scheduled social commitment without EXOGENOUS_SOCIAL "
                        "requires explicit social_contact_mode"
                    )
                contact_mode = mat.social_contact_mode
        else:
            _fail("SOCIAL_CONTACT requires SOCIAL pending or COMMITMENT source")
        if contact_mode not in SOCIAL_CONTACT_MODES:
            _fail(f"invalid social contact_mode: {contact_mode!r}")
        details = {"contact_mode": contact_mode}

    # Companions / commitment_id
    commitment_id: str | None = None
    companions: tuple[Mapping[str, Any], ...] = ()
    if cand.source_kind == "COMMITMENT":
        assert commitment is not None
        commitment_id = commitment["commitment_id"]
        companions = _companions_from_participants(commitment.get("participants", []))
    elif cand.source_kind == "SOCIAL":
        assert social_pending is not None
        companions = ({"entity_id": social_pending.person_id},)
        commitment_id = None

    # End semantics
    available = facts.available_window_min
    end_plan: _EndPlan

    if cand.action_kind == "TRAVEL_TO_COMMITMENT":
        assert commitment is not None
        dest = commitment.get("location_id")
        if dest is None:
            _fail("TRAVEL_TO_COMMITMENT requires commitment.location_id")
        route = _find_forward_route(
            routes=facts.route_profiles,
            origin=location,
            destination=str(dest),
        )
        dmin = _require_true_int(route["duration_min"], "route.duration_min")
        dmax = _require_true_int(route["duration_max"], "route.duration_max")
        end_plan = _bounded_window(now_c, dmin, dmax)

    elif cand.action_kind in {"STUDY", "ERRAND"} and cand.source_kind == "TASK":
        task = _resolve_task(schedule_state, cand.source_ref)
        earliest = _require_true_int(task["min_work_chunk_min"], "min_work_chunk_min")
        remaining = _require_true_int(
            task["effort_remaining_min"], "effort_remaining_min"
        )
        domain_policy = policy["domain_policy"]
        if cand.action_kind == "STUDY":
            domain_max = _require_true_int(
                domain_policy["study"]["chunk_max"], "domain_policy.study.chunk_max"
            )
        else:
            domain_max = _require_true_int(
                domain_policy["errand"]["duration_max"],
                "domain_policy.errand.duration_max",
            )
        latest = min(remaining, available, domain_max)
        if latest < earliest:
            _fail(
                f"impossible task bounds: latest={latest} < earliest={earliest} "
                f"(remaining={remaining} window={available} domain_max={domain_max})"
            )
        end_plan = _bounded_window(now_c, earliest, latest)

    elif cand.action_kind == "HOUSEHOLD":
        block = policy["domain_policy"]["household_age"]
        dmin, dmax = _policy_duration_range(block, label="household_age")
        latest = _cap_latest(dmax, available)
        if latest < dmin:
            _fail(f"impossible household bounds: latest={latest} < min={dmin}")
        end_plan = _bounded_window(now_c, dmin, latest)

    elif cand.action_kind == "KICKBOXING":
        block = policy["domain_policy"]["exercise_kickboxing"]
        dmin, dmax = _policy_duration_range(block, label="exercise_kickboxing")
        latest = _cap_latest(dmax, available)
        if latest < dmin:
            _fail(f"impossible kickboxing bounds: latest={latest} < min={dmin}")
        end_plan = _bounded_window(now_c, dmin, latest)

    elif cand.action_kind == "MEAL":
        assert details is not None
        family = _meal_duration_family(
            meal_extent=details["meal_extent"], meal_source=details["meal_source"]
        )
        block = policy["meal_policy"]["duration_families"][family]
        dmin, dmax = _policy_duration_range(block, label=f"meal_policy.{family}")
        latest = _cap_latest(dmax, available)
        if latest < dmin:
            _fail(f"impossible meal bounds: latest={latest} < min={dmin}")
        end_plan = _bounded_window(now_c, dmin, latest)

    elif cand.action_kind == "SLEEP_MAIN":
        assert mat.sleep_profile is not None
        need = _require_true_int(mat.sleep_profile["sleep_need_min"], "sleep_need_min")
        wake = policy["sleep_policy"]["wake_variation"]
        early = _require_non_neg_int(wake["early_min"], "wake_variation.early_min")
        late = _require_non_neg_int(wake["late_min"], "wake_variation.late_min")
        earliest = max(1, need - early)
        latest = need + late
        if latest < earliest:
            _fail(f"impossible sleep bounds: latest={latest} < earliest={earliest}")
        end_plan = _bounded_window(now_c, earliest, latest)

    elif activity_type == "SOCIAL_CONTACT" and social_pending is not None:
        # Immediate social — payload owned by PendingImmediateAccept.
        assert contact_mode is not None
        if contact_mode == "LIGHTWEIGHT":
            end_plan = _open_reassessable(now_c, available)
        else:
            if social_pending.duration_min is None or social_pending.duration_max is None:
                _fail(
                    f"immediate social mode {contact_mode!r} requires "
                    "duration_min/max from pending handoff"
                )
            dmin = social_pending.duration_min
            dmax = social_pending.duration_max
            latest = _cap_latest(dmax, available)
            if latest < dmin:
                _fail(
                    f"impossible immediate social bounds: latest={latest} < min={dmin}"
                )
            end_plan = _bounded_window(now_c, dmin, latest)

    elif commitment is not None and cand.action_kind != "TRAVEL_TO_COMMITMENT":
        # Commitment activity (WORK / HARD_* / SOCIAL_PROMISE / scheduled social)
        timing = commitment["timing"]
        timing_kind = timing.get("timing_kind")
        if timing_kind == "EXACT":
            end_plan = _exact_source_end(timing["planned_end"])
        elif timing_kind == "WINDOW":
            dmin = _require_true_int(timing["duration_min"], "timing.duration_min")
            dmax = _require_true_int(timing["duration_max"], "timing.duration_max")
            end_plan = _bounded_window(now_c, dmin, dmax)
        else:
            _fail(f"unsupported commitment timing_kind: {timing_kind!r}")

    elif activity_type in OPEN_LEISURE_OR_REST or cand.action_kind == "REST":
        end_plan = _open_reassessable(now_c, available)

    else:
        # Fail closed rather than invent an end.
        _fail(
            f"no reviewed end semantics for action_kind={cand.action_kind!r} "
            f"activity_type={activity_type!r} source_kind={cand.source_kind!r}"
        )

    # Interruptibility
    interruptibility = "REASSESSABLE"
    if (
        cand.source_kind == "COMMITMENT"
        and cand.priority_tier == "HARD_COMMITMENT"
        and commitment is not None
        and commitment["timing"].get("timing_kind") == "EXACT"
        and end_plan.planned_end is not None
    ):
        interruptibility = "NON_INTERRUPTIBLE"
    if interruptibility not in INTERRUPTIBILITY:
        _fail(f"invalid interruptibility: {interruptibility!r}")

    start_spec = parse_activity_start_spec(
        {
            "activity_type": activity_type,
            "actual_start": now_c,
            "interruptibility": interruptibility,
            "planned_end": end_plan.planned_end,
            "expected_end": end_plan.expected_end,
            "expected_end_window": end_plan.expected_end_window,
            "location_id": location,
            "companions": list(companions),
            "commitment_id": commitment_id,
            "details": details,
            "effects_on_end": [],
        }
    )
    finish_context = build_runtime_finish_context(
        action_kind=cand.action_kind,
        source_kind=cand.source_kind,
        sleep_profile=mat.sleep_profile,
        commitment=commitment,
        schedule_state=schedule_state,
        source_ref=cand.source_ref,
    )
    return MaterializedActivityPlan(
        start_spec=start_spec,
        dynamics_context=dynamics,
        activity_type=activity_type,
        runtime_finish_context=finish_context,
    )


# ---------------------------------------------------------------------------
# ACTIVITY_END builder (no enqueue)
# ---------------------------------------------------------------------------


def build_activity_end_event(
    *,
    activity_instance_id: str,
    planned_end: str | None,
) -> dict[str, Any] | None:
    """Construct ACTIVITY_END only for true exact planned_end. Never enqueue."""
    if planned_end is None:
        return None
    instance = _require_str(activity_instance_id, "activity_instance_id")
    due = _require_str(planned_end, "planned_end")
    event = {
        "schema_version": 1,
        "event_id": stable_id("activity-end", instance, due),
        "event_kind": "ACTIVITY_END",
        "due_at": due,
        "priority": 10,
        "payload": {"activity_instance_id": instance},
    }
    validate_instance(event, "queued_internal_event")
    return event


# ---------------------------------------------------------------------------
# Start result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeActivityStartResult:
    bundle: RuntimeBundle
    active_activity: Mapping[str, Any]
    activity_end_event: Mapping[str, Any] | None
    social_response_state: SocialResponseState | None

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
            "active_activity": dict(self.active_activity),
            "activity_end_event": (
                None if self.activity_end_event is None else dict(self.activity_end_event)
            ),
            "social_response_state": (
                None
                if self.social_response_state is None
                else self.social_response_state.as_dict()
            ),
        }


def _install_dynamics_context(
    current_state: Mapping[str, Any],
    dynamics: DynamicsContext,
) -> dict[str, Any]:
    state = deepcopy(dict(current_state))
    active = state["context"].get("active_activity")
    if not isinstance(active, Mapping):
        _fail("active_activity missing after 5B1 start")
    enriched = dict(active)
    enriched["dynamics_context"] = dynamics.as_dict()
    validated = validate_active_activity(enriched)
    assert validated is not None
    if "dynamics_context" not in validated:
        _fail("dynamics_context must survive ActiveActivity validation")
    state["context"]["active_activity"] = validated
    return validate_checkpoint(state)


def _install_runtime_finish_context(
    current_state: Mapping[str, Any],
    finish_context: Mapping[str, Any],
) -> dict[str, Any]:
    state = deepcopy(dict(current_state))
    active = state["context"].get("active_activity")
    if not isinstance(active, Mapping):
        _fail("active_activity missing after 5B1 start")
    enriched = dict(active)
    enriched["runtime_finish_context"] = dict(finish_context)
    validated = validate_active_activity(enriched)
    assert validated is not None
    if "runtime_finish_context" not in validated:
        _fail("runtime_finish_context must survive ActiveActivity validation")
    state["context"]["active_activity"] = validated
    return validate_checkpoint(state)


def start_runtime_activity_from_selection(
    *,
    bundle: RuntimeBundle,
    reference_sets: RuntimeReferenceSets,
    frame: RuntimeDecisionFrame,
    decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
    materialization_facts: RuntimeActivityMaterializationFacts | Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    social_responses: Sequence[SocialResponseDecision | Mapping[str, Any]] = (),
    social_response_state: SocialResponseState | Mapping[str, Any] | None = None,
    pending_immediate_accept: PendingImmediateAccept | Mapping[str, Any] | None = None,
) -> RuntimeActivityStartResult:
    """Materialize + start via 5B1 bridge. Pure; does not enqueue ACTIVITY_END.

    Immediate social ACCEPT is terminalized only after validated successful start.
    Input bundle is not mutated.
    """
    # Snapshot inputs for non-mutation checks by callers; deep-copy before work.
    input_bundle = bundle
    validated_in = validate_runtime_bundle(input_bundle, reference_sets=reference_sets)
    current_in = validated_in.current_state
    schedule_in = validated_in.schedule_state

    processed_before = current_in["processed_through"]
    revision_before = current_in["state_revision"]
    queue_before = deepcopy(current_in["pending_queue"])
    schedule_before = deepcopy(dict(schedule_in))
    world_before = {
        "relation": deepcopy(dict(validated_in.relation_state)),
        "home": deepcopy(dict(validated_in.home_state)),
        "consumables": deepcopy(dict(validated_in.consumables_state)),
        "wardrobe": deepcopy(dict(validated_in.wardrobe_state)),
        "finance": deepcopy(dict(validated_in.finance_state)),
    }

    if frame.selected_candidate is None:
        _fail("RuntimeDecisionFrame.selected_candidate is required for start")
    if frame.resolution.result_kind != "SELECT_CANDIDATE":
        _fail(
            "RuntimeDecisionFrame.resolution.result_kind must be SELECT_CANDIDATE "
            f"(got {frame.resolution.result_kind!r})"
        )

    mat_facts = parse_runtime_activity_materialization_facts(materialization_facts)
    if mat_facts.selected_candidate_id != frame.selected_candidate.candidate_id:
        _fail("materialization facts selected_candidate_id mismatch vs frame")

    # Explicit Behavior Policy version bind to CurrentState (no resolver re-run).
    policy = _require_v2_policy(behavior_policy)
    if policy["behavior_policy_version"] != current_in["behavior_policy_version"]:
        _fail(
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current_in['behavior_policy_version']!r}"
        )

    # Bind decision facts to the resolved frame (no resolver re-run).
    parsed_decision_facts = parse_runtime_decision_facts(
        decision_facts, now=processed_before
    )
    facts_hash = build_decision_facts_hash(parsed_decision_facts)
    if facts_hash != frame.decision_facts_hash:
        _fail(
            "decision_facts_hash mismatch vs RuntimeDecisionFrame "
            "(post-decision facts drift is rejected)"
        )

    # Bind full C1 materialization inputs to the resolved frame.
    context_hash = build_materialization_context_hash(
        current_state=current_in,
        schedule_state=schedule_in,
        behavior_policy=behavior_policy,
        decision_facts_hash=facts_hash,
    )
    if context_hash != frame.materialization_context_hash:
        _fail(
            "materialization_context_hash mismatch vs RuntimeDecisionFrame "
            "(post-decision CurrentState/ScheduleState/Behavior Policy drift "
            "is rejected)"
        )

    pending: PendingImmediateAccept | None = None
    social_state_in: SocialResponseState | None = None
    selected = frame.selected_candidate
    if selected.source_kind == "SOCIAL":
        if pending_immediate_accept is None:
            _fail(
                "selected SOCIAL requires pending_immediate_accept "
                "(authoritative 5B2B handoff)"
            )
        if social_response_state is None:
            _fail(
                "selected SOCIAL requires social_response_state "
                "(for terminalization after successful start)"
            )
        pending = parse_pending_immediate_accept(pending_immediate_accept)
        social_state_in = validate_social_response_state(social_response_state)
        if pending.decided_at != processed_before:
            _fail(
                "pending_immediate_accept.decided_at must equal "
                "CurrentState.processed_through"
            )
        if pending.selected_candidate_id != selected.candidate_id:
            _fail("pending_immediate_accept.selected_candidate_id mismatch")
        if pending.decision_key != frame.resolution.decision_key:
            _fail("pending_immediate_accept.decision_key mismatch")
        if pending.character_id != current_in["character_id"]:
            _fail("pending_immediate_accept.character_id mismatch")
        if selected.source_ref != pending.opportunity_id:
            _fail("pending_immediate_accept.opportunity_id mismatch vs source_ref")
        if pending.person_id not in reference_sets.known_person_ids:
            _fail(
                "pending_immediate_accept.person_id must belong to "
                "reference_sets.known_person_ids "
                f"(got {pending.person_id!r})"
            )
    elif pending_immediate_accept is not None:
        _fail("pending_immediate_accept requires SOCIAL source candidate")
    elif social_response_state is not None:
        social_state_in = validate_social_response_state(social_response_state)

    location = current_in["context"]["location_id"]
    if not isinstance(location, str) or not location:
        _fail("current_state.context.location_id required")

    plan = materialize_activity_plan(
        selected_candidate=frame.selected_candidate,
        decision_facts=parsed_decision_facts,
        materialization_facts=mat_facts,
        schedule_state=schedule_in,
        behavior_policy=behavior_policy,
        now=processed_before,
        current_location_id=location,
        social_responses=social_responses,
        pending_immediate_accept=pending,
    )

    # 5B1 start bridge (reuse; do not reimplement selection validation).
    started_state = start_activity_from_selection(
        current_state=current_in,
        policy_version=current_in["behavior_policy_version"],
        resolution=frame.resolution,
        selected_candidate=frame.selected_candidate,
        start_spec=plan.start_spec,
        input_state_refs=frame.input_state_refs,
    )
    started_state = _install_dynamics_context(started_state, plan.dynamics_context)
    started_state = _install_runtime_finish_context(
        started_state, plan.runtime_finish_context
    )
    active = started_state["context"]["active_activity"]
    assert isinstance(active, Mapping)

    # Identity must match 5B1 deterministic formula.
    expected_instance = derive_activity_instance_id(
        character_id=current_in["character_id"],
        decision_key=frame.resolution.decision_key,
        selected_candidate_id=frame.selected_candidate.candidate_id,
    )
    if active["activity_instance_id"] != expected_instance:
        _fail("activity_instance_id mismatch vs derive_activity_instance_id")

    end_event = build_activity_end_event(
        activity_instance_id=active["activity_instance_id"],
        planned_end=active.get("planned_end"),
    )

    social_state_out = social_state_in
    if pending is not None:
        assert social_state_in is not None
        # Terminalize only after validated successful start evidence.
        social_state_out = apply_pending_immediate_accept_after_successful_start(
            social_state_in,
            pending,
            successful_start=SuccessfulImmediateSocialStart(active_activity=active),
            as_of=processed_before,
        )

    # Assemble output bundle: only current_state.active_activity may change.
    out_current = started_state
    if out_current["processed_through"] != processed_before:
        _fail("processed_through must remain unchanged")
    if out_current["state_revision"] != revision_before:
        _fail("state_revision must remain unchanged")
    if out_current["pending_queue"] != queue_before:
        _fail("pending_queue must remain unchanged in C1")

    out_bundle = RuntimeBundle(
        current_state=out_current,
        schedule_state=schedule_before,
        relation_state=world_before["relation"],
        home_state=world_before["home"],
        consumables_state=world_before["consumables"],
        wardrobe_state=world_before["wardrobe"],
        finance_state=world_before["finance"],
    )
    # Re-validate assembled bundle.
    out_validated = validate_runtime_bundle(out_bundle, reference_sets=reference_sets)

    return RuntimeActivityStartResult(
        bundle=out_validated,
        active_activity=dict(out_validated.current_state["context"]["active_activity"]),
        activity_end_event=end_event,
        social_response_state=social_state_out,
    )
