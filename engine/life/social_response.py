"""Life Engine v2 Slice 2H — social response / acceptance adapter (pure).

Converts exogenous SocialExogenousOpportunity facts into the application character's semantic
ACCEPT / DECLINE / DEFER response and either a Slice-2D SOCIAL_CONTACT
ResolvedOpportunity or a transient SOFT SOCIAL Commitment proposal.

No response RNG, no file/network I/O, no runtime/queue/clock mutation,
no ActualEvent / relation / commitment persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from .contracts import validate_commitment
from .decisions import ResolvedOpportunity, parse_resolved_opportunity
from .derived import (
    classify_physical_fatigue_band,
    classify_sleep_pressure_band,
    classify_social_battery_band,
)
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .fixed_point import validate_human_state
from .ids import stable_id
from .schema import reject_binary_floats
from .social_opportunities import SocialExogenousOpportunity
from .timeutil import (
    TOKYO,
    format_rfc3339,
    parse_rfc3339,
    require_canonical_timestamp,
)

# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------

RESPONSES = frozenset({"ACCEPT", "DECLINE", "DEFER"})
TERMINAL_RESPONSES = frozenset({"ACCEPT", "DECLINE"})

REASON_CODES = frozenset(
    {
        "CONTACT_READY",
        "POST_WORK_READY",
        "INVITE_FEASIBLE",
        "HARD_COMMITMENT_BLOCKING",
        "HARD_COMMITMENT_CONFLICT",
        "LOW_SOCIAL_BATTERY",
        "VERY_HIGH_FATIGUE",
        "CRITICAL_SLEEP_PRESSURE",
        "PHYSICAL_BLOCKED",
        "LOCATION_BLOCKED",
        "INSUFFICIENT_WINDOW",
        "MISSING_AVAILABILITY",
        "OPPORTUNITY_EXPIRED",
        "PRIOR_TERMINAL_RESPONSE",
    }
)

DIAGNOSTIC_CODES = frozenset(
    {
        "NOT_YET_AVAILABLE",
    }
)

SUPPORTED_RECEPTIVITY = "MODERATE_TO_HIGH_IF_CAPACITY"

CONTACT_MODE_MAP: dict[tuple[str, str], str] = {
    ("REMOTE_CLOSE_BURSTY", "CONTACT_OPPORTUNITY"): "CALL",
    ("LOCAL_CLOSE_INVITER", "CONTACT_OPPORTUNITY"): "LIGHTWEIGHT",
    ("LOCAL_CLOSE_INVITER", "INVITE_OPPORTUNITY"): "LOCAL_OUTING",
    ("WORK_CONTEXTUAL", "POST_WORK_OPPORTUNITY"): "POST_WORK",
}

DURATIONED_MODES = frozenset({"CALL", "LOCAL_OUTING", "POST_WORK"})
LIGHTWEIGHT_MODE = "LIGHTWEIGHT"

RULE_SLICE2H = "slice2h.social-response"
PROVENANCE_RULE = "slice2h.social-response"

_CONTEXT_FIELDS = frozenset(
    {
        "now",
        "human_state",
        "sleep_pressure",
        "available_window_min",
        "hard_commitment_blocking_now",
        "social_contact_physical_feasible",
        "post_work_location_feasible",
    }
)
_AVAIL_FIELDS = frozenset(
    {
        "availability_id",
        "opportunity_id",
        "earliest_start",
        "latest_end",
        "location_id",
        "hard_conflict",
    }
)
_PRIOR_FIELDS = frozenset(
    {
        "response_id",
        "opportunity_id",
        "response",
        "decided_at",
        "reason_code",
    }
)
_OPP_FIELDS = frozenset(
    {
        "opportunity_id",
        "opportunity_kind",
        "person_id",
        "archetype_id",
        "source_kind",
        "source_ref",
        "available_local_date",
        "invite_timing_kind",
        "target_local_date",
        "activation_random_key",
        "timing_random_key",
        "future_day_random_key",
        "rule_ids",
    }
)
_FORBIDDEN_FIELD_NAMES = frozenset(
    {
        "name",
        "message",
        "text",
        "topic",
        "friendship_score",
        "closeness",
        "trust",
        "acceptance_probability",
        "relationship_score",
    }
)


# ---------------------------------------------------------------------------
# Transient structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SocialResponseContext:
    now: str
    human_state: Mapping[str, int]
    sleep_pressure: int
    available_window_min: int
    hard_commitment_blocking_now: bool
    social_contact_physical_feasible: bool
    post_work_location_feasible: bool


@dataclass(frozen=True)
class SocialAvailabilityWindowFact:
    availability_id: str
    opportunity_id: str
    earliest_start: str
    latest_end: str
    location_id: str | None
    hard_conflict: bool


@dataclass(frozen=True)
class PriorSocialResponseFact:
    response_id: str
    opportunity_id: str
    response: str
    decided_at: str
    reason_code: str


@dataclass(frozen=True)
class SocialCommitmentProposal:
    proposal_id: str
    source_opportunity_id: str
    commitment: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "source_opportunity_id": self.source_opportunity_id,
            "commitment": dict(self.commitment),
        }


@dataclass(frozen=True)
class SocialResponseDecision:
    response_id: str
    opportunity_id: str
    person_id: str
    opportunity_kind: str
    response: str
    reason_code: str
    contact_mode: str | None
    duration_min: int | None
    duration_max: int | None
    resolved_opportunity_key: str | None
    commitment_proposal_id: str | None
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        # Non-coercive: bare strings must not become character lists before parse.
        rule_ids: Any
        if isinstance(self.rule_ids, (str, bytes)):
            rule_ids = self.rule_ids
        elif isinstance(self.rule_ids, (list, tuple)):
            rule_ids = list(self.rule_ids)
        else:
            rule_ids = self.rule_ids
        return {
            "response_id": self.response_id,
            "opportunity_id": self.opportunity_id,
            "person_id": self.person_id,
            "opportunity_kind": self.opportunity_kind,
            "response": self.response,
            "reason_code": self.reason_code,
            "contact_mode": self.contact_mode,
            "duration_min": self.duration_min,
            "duration_max": self.duration_max,
            "resolved_opportunity_key": self.resolved_opportunity_key,
            "commitment_proposal_id": self.commitment_proposal_id,
            "rule_ids": rule_ids,
        }


@dataclass(frozen=True)
class SocialResponseDiagnostic:
    code: str
    opportunity_id: str | None
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "opportunity_id": self.opportunity_id,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class SocialResponseAdapterResult:
    responses: tuple[SocialResponseDecision, ...]
    opportunities: tuple[ResolvedOpportunity, ...]
    commitment_proposals: tuple[SocialCommitmentProposal, ...]
    diagnostics: tuple[SocialResponseDiagnostic, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_nonempty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be non-empty str")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{label} must be bool")
    return value


def _require_int_ge0(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be int (no bool/float)")
    if value < 0:
        _fail(f"{label} must be >= 0")
    return value


def _require_int_0_1000(value: object, label: str) -> int:
    v = _require_int_ge0(value, label)
    if v > 1000:
        _fail(f"{label} must be 0..1000")
    return v


def _canonical_sorted_strs(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(values))


def _reject_forbidden_keys(data: Mapping[str, Any], *, label: str) -> None:
    bad = sorted(set(data) & _FORBIDDEN_FIELD_NAMES)
    if bad:
        _fail(f"{label}: forbidden fields {bad}")


def _reject_unknown(data: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        _fail(f"{label}: unknown fields {unknown}")


def _dt(ts: str, *, field: str) -> datetime:
    return parse_rfc3339(ts, field=field)


def _tokyo_local_date(ts: str, *, field: str) -> str:
    return _dt(ts, field=field).astimezone(TOKYO).date().isoformat()


def _parse_iso_date(value: object, *, label: str) -> date:
    text = _require_nonempty_str(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE, f"{label} must be YYYY-MM-DD: {text}"
        ) from exc


def _seconds_between(later: str, earlier: str, *, later_field: str, earlier_field: str) -> int:
    later_dt = _dt(later, field=later_field)
    earlier_dt = _dt(earlier, field=earlier_field)
    if later_dt.microsecond != 0 or earlier_dt.microsecond != 0:
        _fail("social response math rejects sub-second timestamps")
    delta = later_dt - earlier_dt
    if delta.microseconds != 0:
        _fail("social response delta has nonzero microseconds")
    return delta.days * 86400 + delta.seconds


def _exact_minutes_between(
    later: str, earlier: str, *, later_field: str, earlier_field: str
) -> int:
    secs = _seconds_between(
        later, earlier, later_field=later_field, earlier_field=earlier_field
    )
    if secs % 60 != 0:
        _fail(
            f"availability window must be exact whole minutes "
            f"({earlier_field}..{later_field})"
        )
    return secs // 60


def _add_minutes_ts(ts: str, minutes: int, *, field: str) -> str:
    dt = _dt(ts, field=field)
    return format_rfc3339(dt + timedelta(minutes=minutes))


def _parse_known_person_ids(known_person_ids: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(known_person_ids, (list, tuple)):
        _fail("known_person_ids must be a sequence")
    out: list[str] = []
    seen: set[str] = set()
    for raw in known_person_ids:
        pid = _require_nonempty_str(raw, "known_person_id")
        if pid in seen:
            _fail(f"duplicate known_person_id: {pid}")
        seen.add(pid)
        out.append(pid)
    if not out:
        _fail("known_person_ids must be non-empty")
    return tuple(out)


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_social_response_context(
    data: SocialResponseContext | Mapping[str, Any],
) -> SocialResponseContext:
    if isinstance(data, SocialResponseContext):
        payload = {
            "now": data.now,
            "human_state": dict(data.human_state),
            "sleep_pressure": data.sleep_pressure,
            "available_window_min": data.available_window_min,
            "hard_commitment_blocking_now": data.hard_commitment_blocking_now,
            "social_contact_physical_feasible": data.social_contact_physical_feasible,
            "post_work_location_feasible": data.post_work_location_feasible,
        }
    else:
        if not isinstance(data, Mapping):
            _fail("SocialResponseContext must be a mapping")
        payload = dict(data)
    reject_binary_floats(payload)
    _reject_forbidden_keys(payload, label="SocialResponseContext")
    _reject_unknown(payload, _CONTEXT_FIELDS, "SocialResponseContext")
    now = _require_nonempty_str(payload.get("now"), "now")
    require_canonical_timestamp(now, field="now")
    hs_raw = payload.get("human_state")
    if not isinstance(hs_raw, Mapping):
        _fail("human_state must be a mapping")
    reject_binary_floats(dict(hs_raw), path="$.human_state")
    human_state = validate_human_state(hs_raw)
    return SocialResponseContext(
        now=now,
        human_state=human_state,
        sleep_pressure=_require_int_0_1000(payload.get("sleep_pressure"), "sleep_pressure"),
        available_window_min=_require_int_ge0(
            payload.get("available_window_min"), "available_window_min"
        ),
        hard_commitment_blocking_now=_require_bool(
            payload.get("hard_commitment_blocking_now"), "hard_commitment_blocking_now"
        ),
        social_contact_physical_feasible=_require_bool(
            payload.get("social_contact_physical_feasible"),
            "social_contact_physical_feasible",
        ),
        post_work_location_feasible=_require_bool(
            payload.get("post_work_location_feasible"), "post_work_location_feasible"
        ),
    )


def _assert_exogenous_opportunity_semantics(
    *,
    kind: str,
    source_kind: str,
    available_local_date: str,
    invite_timing: str | None,
    target: str | None,
    timing_key: str | None,
    future_key: str | None,
) -> None:
    """Fail-closed Slice-2G kind/source/timing invariants (revalidate at 2H)."""
    if kind in {"CONTACT_OPPORTUNITY", "INVITE_OPPORTUNITY"}:
        if source_kind != "WEEKLY_SLOT":
            _fail(
                f"{kind} requires source_kind=WEEKLY_SLOT "
                f"(got {source_kind!r})"
            )
    elif kind == "POST_WORK_OPPORTUNITY":
        if source_kind != "POST_WORK_CONTEXT":
            _fail(
                "POST_WORK_OPPORTUNITY requires source_kind=POST_WORK_CONTEXT "
                f"(got {source_kind!r})"
            )
    else:
        _fail(f"unknown opportunity_kind: {kind}")

    available_d = _parse_iso_date(available_local_date, label="available_local_date")

    if kind == "CONTACT_OPPORTUNITY":
        if invite_timing is not None:
            _fail("CONTACT_OPPORTUNITY must not set invite_timing_kind")
        if target is not None:
            _fail("CONTACT_OPPORTUNITY must not set target_local_date")
        if timing_key is not None:
            _fail("CONTACT_OPPORTUNITY must not set timing_random_key")
        if future_key is not None:
            _fail("CONTACT_OPPORTUNITY must not set future_day_random_key")
        return

    if kind == "POST_WORK_OPPORTUNITY":
        if invite_timing is not None:
            _fail("POST_WORK_OPPORTUNITY must not set invite_timing_kind")
        if timing_key is not None:
            _fail("POST_WORK_OPPORTUNITY must not set timing_random_key")
        if future_key is not None:
            _fail("POST_WORK_OPPORTUNITY must not set future_day_random_key")
        if target is None:
            _fail("POST_WORK_OPPORTUNITY requires target_local_date")
        target_d = _parse_iso_date(target, label="target_local_date")
        if target_d != available_d:
            _fail(
                "POST_WORK_OPPORTUNITY target_local_date must equal "
                "available_local_date"
            )
        return

    # INVITE_OPPORTUNITY
    if invite_timing is None:
        _fail("INVITE_OPPORTUNITY requires invite_timing_kind")
    if invite_timing not in {"SAME_DAY_SOFT", "FUTURE_SOFT"}:
        _fail(f"unknown invite_timing_kind: {invite_timing}")
    if target is None:
        _fail("INVITE_OPPORTUNITY requires target_local_date")
    if timing_key is None:
        _fail("INVITE_OPPORTUNITY requires timing_random_key")
    target_d = _parse_iso_date(target, label="target_local_date")
    if invite_timing == "SAME_DAY_SOFT":
        if target_d != available_d:
            _fail(
                "SAME_DAY_SOFT target_local_date must equal available_local_date"
            )
        if future_key is not None:
            _fail("SAME_DAY_SOFT must not set future_day_random_key")
    else:
        # FUTURE_SOFT
        if target_d <= available_d:
            _fail(
                "FUTURE_SOFT target_local_date must be after available_local_date"
            )
        if future_key is None:
            _fail("FUTURE_SOFT requires future_day_random_key")


def parse_social_exogenous_opportunity(
    data: SocialExogenousOpportunity | Mapping[str, Any],
) -> SocialExogenousOpportunity:
    # Dataclass and mapping share one validation path (re-materialize).
    if isinstance(data, SocialExogenousOpportunity):
        payload = data.as_dict()
    elif isinstance(data, Mapping):
        payload = dict(data)
    else:
        _fail("opportunity must be a mapping")
    reject_binary_floats(payload)
    _reject_forbidden_keys(payload, label="SocialExogenousOpportunity")
    _reject_unknown(payload, _OPP_FIELDS, "SocialExogenousOpportunity")
    kind = _require_nonempty_str(payload.get("opportunity_kind"), "opportunity_kind")
    if kind not in {
        "CONTACT_OPPORTUNITY",
        "INVITE_OPPORTUNITY",
        "POST_WORK_OPPORTUNITY",
    }:
        _fail(f"unknown opportunity_kind: {kind}")
    invite_timing = payload.get("invite_timing_kind")
    if invite_timing is not None:
        invite_timing = _require_nonempty_str(invite_timing, "invite_timing_kind")
        if invite_timing not in {"SAME_DAY_SOFT", "FUTURE_SOFT"}:
            _fail(f"unknown invite_timing_kind: {invite_timing}")
    target = payload.get("target_local_date")
    if target is not None:
        _parse_iso_date(target, label="target_local_date")
        target = str(target)
    available = _parse_iso_date(
        payload.get("available_local_date"), label="available_local_date"
    ).isoformat()
    rules_raw = payload.get("rule_ids", [])
    if not isinstance(rules_raw, (list, tuple)):
        _fail("rule_ids must be list")
    rule_ids = _canonical_sorted_strs(
        [_require_nonempty_str(x, "rule_id") for x in rules_raw]
    )
    timing_key = payload.get("timing_random_key")
    if timing_key is not None:
        timing_key = _require_nonempty_str(timing_key, "timing_random_key")
    future_key = payload.get("future_day_random_key")
    if future_key is not None:
        future_key = _require_nonempty_str(future_key, "future_day_random_key")
    source_kind = _require_nonempty_str(payload.get("source_kind"), "source_kind")
    _assert_exogenous_opportunity_semantics(
        kind=kind,
        source_kind=source_kind,
        available_local_date=available,
        invite_timing=invite_timing,
        target=target,
        timing_key=timing_key,
        future_key=future_key,
    )
    return SocialExogenousOpportunity(
        opportunity_id=_require_nonempty_str(payload.get("opportunity_id"), "opportunity_id"),
        opportunity_kind=kind,
        person_id=_require_nonempty_str(payload.get("person_id"), "person_id"),
        archetype_id=_require_nonempty_str(payload.get("archetype_id"), "archetype_id"),
        source_kind=source_kind,
        source_ref=_require_nonempty_str(payload.get("source_ref"), "source_ref"),
        available_local_date=available,
        invite_timing_kind=invite_timing,
        target_local_date=target,
        activation_random_key=_require_nonempty_str(
            payload.get("activation_random_key"), "activation_random_key"
        ),
        timing_random_key=timing_key,
        future_day_random_key=future_key,
        rule_ids=rule_ids,
    )


def parse_availability_window(
    data: SocialAvailabilityWindowFact | Mapping[str, Any],
    *,
    now: str,
    opportunity: SocialExogenousOpportunity,
) -> SocialAvailabilityWindowFact:
    if isinstance(data, SocialAvailabilityWindowFact):
        payload = {
            "availability_id": data.availability_id,
            "opportunity_id": data.opportunity_id,
            "earliest_start": data.earliest_start,
            "latest_end": data.latest_end,
            "location_id": data.location_id,
            "hard_conflict": data.hard_conflict,
        }
    else:
        if not isinstance(data, Mapping):
            _fail("availability must be a mapping")
        payload = dict(data)
    reject_binary_floats(payload)
    _reject_forbidden_keys(payload, label="SocialAvailabilityWindowFact")
    _reject_unknown(payload, _AVAIL_FIELDS, "SocialAvailabilityWindowFact")
    if opportunity.opportunity_kind != "INVITE_OPPORTUNITY":
        _fail("availability window only valid for INVITE_OPPORTUNITY")
    avail_id = _require_nonempty_str(payload.get("availability_id"), "availability_id")
    opp_id = _require_nonempty_str(payload.get("opportunity_id"), "opportunity_id")
    if opp_id != opportunity.opportunity_id:
        _fail("availability.opportunity_id must match opportunity")
    earliest = _require_nonempty_str(payload.get("earliest_start"), "earliest_start")
    latest = _require_nonempty_str(payload.get("latest_end"), "latest_end")
    require_canonical_timestamp(earliest, field="earliest_start")
    require_canonical_timestamp(latest, field="latest_end")
    if _seconds_between(earliest, now, later_field="earliest_start", earlier_field="now") < 0:
        _fail("availability.earliest_start must be >= now")
    if _seconds_between(latest, earliest, later_field="latest_end", earlier_field="earliest_start") <= 0:
        _fail("availability.latest_end must be > earliest_start")
    target = opportunity.target_local_date
    if target is None:
        _fail("invite opportunity missing target_local_date")
    if _tokyo_local_date(earliest, field="earliest_start") != target:
        _fail("availability.earliest_start must be on invite target_local_date")
    if _tokyo_local_date(latest, field="latest_end") != target:
        _fail("availability.latest_end must be on invite target_local_date")
    loc = payload.get("location_id")
    if loc is not None:
        loc = _require_nonempty_str(loc, "location_id")
    return SocialAvailabilityWindowFact(
        availability_id=avail_id,
        opportunity_id=opp_id,
        earliest_start=earliest,
        latest_end=latest,
        location_id=loc,
        hard_conflict=_require_bool(payload.get("hard_conflict"), "hard_conflict"),
    )


def parse_prior_response(
    data: PriorSocialResponseFact | Mapping[str, Any],
    *,
    now: str,
    known_opp_ids: set[str],
) -> PriorSocialResponseFact:
    # Dataclass and mapping share one validation path (re-materialize).
    if isinstance(data, PriorSocialResponseFact):
        payload = {
            "response_id": data.response_id,
            "opportunity_id": data.opportunity_id,
            "response": data.response,
            "decided_at": data.decided_at,
            "reason_code": data.reason_code,
        }
    elif isinstance(data, Mapping):
        payload = dict(data)
    else:
        _fail("prior response must be a mapping")
    reject_binary_floats(payload)
    _reject_forbidden_keys(payload, label="PriorSocialResponseFact")
    _reject_unknown(payload, _PRIOR_FIELDS, "PriorSocialResponseFact")
    response = _require_nonempty_str(payload.get("response"), "response")
    if response not in RESPONSES:
        _fail(f"unknown prior response: {response}")
    reason = _require_nonempty_str(payload.get("reason_code"), "reason_code")
    if reason not in REASON_CODES:
        _fail(f"unknown prior reason_code: {reason}")
    decided_at = _require_nonempty_str(payload.get("decided_at"), "decided_at")
    require_canonical_timestamp(decided_at, field="decided_at")
    if _seconds_between(now, decided_at, later_field="now", earlier_field="decided_at") < 0:
        _fail("prior decided_at must be <= now")
    opp_id = _require_nonempty_str(payload.get("opportunity_id"), "opportunity_id")
    if opp_id not in known_opp_ids:
        _fail(f"prior response unknown opportunity_id: {opp_id}")
    response_id = _require_nonempty_str(payload.get("response_id"), "response_id")
    expected_id = stable_id("social-response", opp_id, response)
    if response_id != expected_id:
        _fail(
            "prior response_id must equal "
            'stable_id("social-response", opportunity_id, response) '
            f"(got {response_id!r}, expected {expected_id!r})"
        )
    return PriorSocialResponseFact(
        response_id=response_id,
        opportunity_id=opp_id,
        response=response,
        decided_at=decided_at,
        reason_code=reason,
    )


def _mode_for(opp: SocialExogenousOpportunity) -> str:
    key = (opp.archetype_id, opp.opportunity_kind)
    mode = CONTACT_MODE_MAP.get(key)
    if mode is None:
        _fail(
            f"kind/archetype mismatch: {opp.archetype_id}+{opp.opportunity_kind}"
        )
    return mode


def _policy_duration(
    social_policy: Mapping[str, Any], mode: str
) -> tuple[int | None, int | None]:
    durations = social_policy["contact_mode_durations"]
    if mode not in durations:
        _fail(f"missing contact_mode_durations for {mode}")
    block = durations[mode]
    if not isinstance(block, Mapping):
        _fail(f"contact_mode_durations.{mode} must be mapping")
    if mode == LIGHTWEIGHT_MODE:
        if block:
            _fail("LIGHTWEIGHT must have no duration fields")
        return None, None
    if mode not in DURATIONED_MODES:
        _fail(f"unsupported duration mode: {mode}")
    if set(block) != {"duration_min", "duration_max"}:
        _fail(f"contact_mode_durations.{mode} must have exactly duration_min/max")
    dmin = block["duration_min"]
    dmax = block["duration_max"]
    if isinstance(dmin, bool) or not isinstance(dmin, int):
        _fail(f"duration_min must be int for {mode}")
    if isinstance(dmax, bool) or not isinstance(dmax, int):
        _fail(f"duration_max must be int for {mode}")
    if dmin <= 0 or dmax < dmin:
        _fail(f"invalid duration bounds for {mode}")
    return dmin, dmax


def _rule_ids(*, archetype_id: str, mode: str | None, extra: Sequence[str] = ()) -> tuple[str, ...]:
    parts = [RULE_SLICE2H, f"archetype:{archetype_id}"]
    if mode is not None:
        parts.append(f"mode:{mode}")
    parts.extend(extra)
    return _canonical_sorted_strs(parts)


def _capacity_reason(
    *,
    battery_band: str,
    fatigue_band: str,
    sleep_band: str,
) -> str | None:
    if battery_band == "LOW":
        return "LOW_SOCIAL_BATTERY"
    if fatigue_band == "VERY_HIGH":
        return "VERY_HIGH_FATIGUE"
    if sleep_band == "CRITICAL":
        return "CRITICAL_SLEEP_PRESSURE"
    return None


def _decision(
    *,
    opportunity_id: str,
    person_id: str,
    opportunity_kind: str,
    response: str,
    reason_code: str,
    contact_mode: str | None = None,
    duration_min: int | None = None,
    duration_max: int | None = None,
    resolved_opportunity_key: str | None = None,
    commitment_proposal_id: str | None = None,
    rule_ids: Sequence[str],
) -> SocialResponseDecision:
    if response not in RESPONSES:
        _fail(f"invalid response: {response}")
    if reason_code not in REASON_CODES:
        _fail(f"invalid reason_code: {reason_code}")
    return SocialResponseDecision(
        response_id=stable_id("social-response", opportunity_id, response),
        opportunity_id=opportunity_id,
        person_id=person_id,
        opportunity_kind=opportunity_kind,
        response=response,
        reason_code=reason_code,
        contact_mode=contact_mode,
        duration_min=duration_min,
        duration_max=duration_max,
        resolved_opportunity_key=resolved_opportunity_key,
        commitment_proposal_id=commitment_proposal_id,
        rule_ids=_canonical_sorted_strs(rule_ids),
    )


def _resolved_contact(
    *,
    opportunity_id: str,
    mode: str,
    archetype_id: str,
) -> ResolvedOpportunity:
    key = stable_id("social-resolved-opportunity", opportunity_id)
    rules = _rule_ids(archetype_id=archetype_id, mode=mode)
    return parse_resolved_opportunity(
        {
            "opportunity_key": key,
            "opportunity_class": "SOCIAL_PROMISE",
            "action_kind": "SOCIAL_CONTACT",
            "source_kind": "SOCIAL",
            "source_ref": opportunity_id,
            "soft_candidate": True,
            "time_feasible": True,
            "location_feasible": True,
            "physical_feasible": True,
            "domain_guard_satisfied": True,
            "local_preference_permille": None,
            "rule_ids": list(rules),
        }
    )


def _build_invite_commitment(
    *,
    context: SocialResponseContext,
    opportunity: SocialExogenousOpportunity,
    availability: SocialAvailabilityWindowFact,
    duration_min: int,
    effective_duration_max: int,
) -> SocialCommitmentProposal:
    commitment_id = stable_id("social-commitment", opportunity.opportunity_id)
    proposal_id = stable_id("social-commitment-proposal", opportunity.opportunity_id)
    latest_start = _add_minutes_ts(
        availability.latest_end, -effective_duration_max, field="latest_end"
    )
    # Must remain fully inside availability.
    if (
        _seconds_between(
            latest_start,
            availability.earliest_start,
            later_field="latest_start",
            earlier_field="earliest_start",
        )
        < 0
    ):
        _fail("invite latest_start before earliest_start")
    end_of_window = _add_minutes_ts(
        latest_start, effective_duration_max, field="latest_start"
    )
    if (
        _seconds_between(
            availability.latest_end,
            end_of_window,
            later_field="latest_end",
            earlier_field="computed_end",
        )
        < 0
    ):
        _fail("invite commitment window escapes availability")
    commitment: dict[str, Any] = {
        "schema_version": 1,
        "commitment_id": commitment_id,
        "kind": "SOCIAL",
        "hardness": "SOFT",
        "created_at": context.now,
        "timing": {
            "timing_kind": "WINDOW",
            "earliest_start": availability.earliest_start,
            "latest_start": latest_start,
            "duration_min": duration_min,
            "duration_max": effective_duration_max,
        },
        "location_id": availability.location_id,
        "participants": [opportunity.person_id],
        "source_kind": "EXOGENOUS_SOCIAL",
        "provenance": {
            "origin": "ENGINE_DEFAULT",
            "source_rule_id": PROVENANCE_RULE,
        },
        "recurrence_id": None,
        "status": "PLANNED",
        "status_history": [
            {
                "changed_at": context.now,
                "from": None,
                "to": "PLANNED",
                "reason": "SOCIAL_INVITE_ACCEPTED",
                "source_ref": opportunity.opportunity_id,
            }
        ],
    }
    validated = validate_commitment(commitment)
    return SocialCommitmentProposal(
        proposal_id=proposal_id,
        source_opportunity_id=opportunity.opportunity_id,
        commitment=validated,
    )


def _sort_responses(
    items: Sequence[SocialResponseDecision],
) -> tuple[SocialResponseDecision, ...]:
    return tuple(
        sorted(
            items,
            key=lambda r: (
                r.opportunity_id,
                r.response,
                r.reason_code,
                r.response_id,
            ),
        )
    )


def _sort_opportunities(
    items: Sequence[ResolvedOpportunity],
) -> tuple[ResolvedOpportunity, ...]:
    return tuple(sorted(items, key=lambda o: o.opportunity_key))


def _sort_proposals(
    items: Sequence[SocialCommitmentProposal],
) -> tuple[SocialCommitmentProposal, ...]:
    return tuple(sorted(items, key=lambda p: (p.source_opportunity_id, p.proposal_id)))


def _sort_diagnostics(
    items: Sequence[SocialResponseDiagnostic],
) -> tuple[SocialResponseDiagnostic, ...]:
    return tuple(
        sorted(
            items,
            key=lambda d: (d.code, d.opportunity_id or "", d.rule_ids),
        )
    )


# ---------------------------------------------------------------------------
# Per-opportunity evaluation
# ---------------------------------------------------------------------------


def _eval_contact(
    *,
    context: SocialResponseContext,
    opportunity: SocialExogenousOpportunity,
    mode: str,
    social_policy: Mapping[str, Any],
    battery_band: str,
    fatigue_band: str,
    sleep_band: str,
) -> tuple[
    SocialResponseDecision,
    ResolvedOpportunity | None,
    SocialCommitmentProposal | None,
]:
    rules = _rule_ids(archetype_id=opportunity.archetype_id, mode=mode)
    # Order: hard → battery → fatigue → sleep → physical → mode/window
    if context.hard_commitment_blocking_now:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DEFER",
                reason_code="HARD_COMMITMENT_BLOCKING",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    cap = _capacity_reason(
        battery_band=battery_band, fatigue_band=fatigue_band, sleep_band=sleep_band
    )
    if cap is not None:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DEFER",
                reason_code=cap,
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    if not context.social_contact_physical_feasible:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DEFER",
                reason_code="PHYSICAL_BLOCKED",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    dmin, dmax = _policy_duration(social_policy, mode)
    if mode == LIGHTWEIGHT_MODE:
        duration_min = None
        duration_max = None
    else:
        assert dmin is not None and dmax is not None
        if context.available_window_min < dmin:
            return (
                _decision(
                    opportunity_id=opportunity.opportunity_id,
                    person_id=opportunity.person_id,
                    opportunity_kind=opportunity.opportunity_kind,
                    response="DEFER",
                    reason_code="INSUFFICIENT_WINDOW",
                    contact_mode=mode,
                    duration_min=dmin,
                    duration_max=dmax,
                    rule_ids=rules,
                ),
                None,
                None,
            )
        duration_min = dmin
        duration_max = min(dmax, context.available_window_min)
    resolved = _resolved_contact(
        opportunity_id=opportunity.opportunity_id,
        mode=mode,
        archetype_id=opportunity.archetype_id,
    )
    return (
        _decision(
            opportunity_id=opportunity.opportunity_id,
            person_id=opportunity.person_id,
            opportunity_kind=opportunity.opportunity_kind,
            response="ACCEPT",
            reason_code="CONTACT_READY",
            contact_mode=mode,
            duration_min=duration_min,
            duration_max=duration_max,
            resolved_opportunity_key=resolved.opportunity_key,
            rule_ids=rules,
        ),
        resolved,
        None,
    )


def _eval_post_work(
    *,
    context: SocialResponseContext,
    opportunity: SocialExogenousOpportunity,
    mode: str,
    social_policy: Mapping[str, Any],
    battery_band: str,
    fatigue_band: str,
    sleep_band: str,
) -> tuple[
    SocialResponseDecision,
    ResolvedOpportunity | None,
    SocialCommitmentProposal | None,
]:
    rules = _rule_ids(archetype_id=opportunity.archetype_id, mode=mode)
    if context.hard_commitment_blocking_now:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code="HARD_COMMITMENT_BLOCKING",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    cap = _capacity_reason(
        battery_band=battery_band, fatigue_band=fatigue_band, sleep_band=sleep_band
    )
    if cap is not None:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code=cap,
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    if not context.social_contact_physical_feasible:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code="PHYSICAL_BLOCKED",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    if not context.post_work_location_feasible:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code="LOCATION_BLOCKED",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    dmin, dmax = _policy_duration(social_policy, mode)
    assert dmin is not None and dmax is not None
    if context.available_window_min < dmin:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code="INSUFFICIENT_WINDOW",
                contact_mode=mode,
                duration_min=dmin,
                duration_max=dmax,
                rule_ids=rules,
            ),
            None,
            None,
        )
    duration_max = min(dmax, context.available_window_min)
    resolved = _resolved_contact(
        opportunity_id=opportunity.opportunity_id,
        mode=mode,
        archetype_id=opportunity.archetype_id,
    )
    return (
        _decision(
            opportunity_id=opportunity.opportunity_id,
            person_id=opportunity.person_id,
            opportunity_kind=opportunity.opportunity_kind,
            response="ACCEPT",
            reason_code="POST_WORK_READY",
            contact_mode=mode,
            duration_min=dmin,
            duration_max=duration_max,
            resolved_opportunity_key=resolved.opportunity_key,
            rule_ids=rules,
        ),
        resolved,
        None,
    )


def _eval_invite(
    *,
    context: SocialResponseContext,
    opportunity: SocialExogenousOpportunity,
    mode: str,
    social_policy: Mapping[str, Any],
    availability: SocialAvailabilityWindowFact | None,
    battery_band: str,
    fatigue_band: str,
    sleep_band: str,
) -> tuple[
    SocialResponseDecision,
    ResolvedOpportunity | None,
    SocialCommitmentProposal | None,
]:
    rules = _rule_ids(archetype_id=opportunity.archetype_id, mode=mode)
    timing = opportunity.invite_timing_kind
    if timing not in {"SAME_DAY_SOFT", "FUTURE_SOFT"}:
        _fail(f"invite missing/invalid invite_timing_kind: {timing}")
    if availability is None:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DEFER",
                reason_code="MISSING_AVAILABILITY",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    if availability.hard_conflict:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code="HARD_COMMITMENT_CONFLICT",
                contact_mode=mode,
                rule_ids=rules,
            ),
            None,
            None,
        )
    # SAME_DAY_SOFT may DEFER on current capacity; FUTURE_SOFT ignores it.
    if timing == "SAME_DAY_SOFT":
        cap = _capacity_reason(
            battery_band=battery_band, fatigue_band=fatigue_band, sleep_band=sleep_band
        )
        if cap is not None:
            return (
                _decision(
                    opportunity_id=opportunity.opportunity_id,
                    person_id=opportunity.person_id,
                    opportunity_kind=opportunity.opportunity_kind,
                    response="DEFER",
                    reason_code=cap,
                    contact_mode=mode,
                    rule_ids=rules,
                ),
                None,
                None,
            )
    dmin, dmax = _policy_duration(social_policy, mode)
    assert dmin is not None and dmax is not None
    window_minutes = _exact_minutes_between(
        availability.latest_end,
        availability.earliest_start,
        later_field="latest_end",
        earlier_field="earliest_start",
    )
    if window_minutes < dmin:
        return (
            _decision(
                opportunity_id=opportunity.opportunity_id,
                person_id=opportunity.person_id,
                opportunity_kind=opportunity.opportunity_kind,
                response="DECLINE",
                reason_code="INSUFFICIENT_WINDOW",
                contact_mode=mode,
                duration_min=dmin,
                duration_max=dmax,
                rule_ids=rules,
            ),
            None,
            None,
        )
    effective_max = min(dmax, window_minutes)
    proposal = _build_invite_commitment(
        context=context,
        opportunity=opportunity,
        availability=availability,
        duration_min=dmin,
        effective_duration_max=effective_max,
    )
    return (
        _decision(
            opportunity_id=opportunity.opportunity_id,
            person_id=opportunity.person_id,
            opportunity_kind=opportunity.opportunity_kind,
            response="ACCEPT",
            reason_code="INVITE_FEASIBLE",
            contact_mode=mode,
            duration_min=dmin,
            duration_max=effective_max,
            commitment_proposal_id=proposal.proposal_id,
            rule_ids=rules,
        ),
        None,
        proposal,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def adapt_social_responses(
    *,
    context: SocialResponseContext | Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    known_person_ids: Sequence[str],
    opportunities: Sequence[SocialExogenousOpportunity | Mapping[str, Any]],
    availability_windows: Sequence[
        SocialAvailabilityWindowFact | Mapping[str, Any]
    ] = (),
    prior_responses: Sequence[PriorSocialResponseFact | Mapping[str, Any]] = (),
) -> SocialResponseAdapterResult:
    """Pure social response adapter for one decision snapshot.

    Input-order independent. No RNG. Does not mutate inputs or runtime stores.
    """
    ctx = parse_social_response_context(context)
    policy = require_v2_production_format(behavior_policy)
    reject_binary_floats(dict(policy))
    social_policy = policy["social_policy"]
    receptivity = policy.get("behavior_modes", {}).get("invitation_receptivity")
    if receptivity != SUPPORTED_RECEPTIVITY:
        _fail(
            f"unsupported invitation_receptivity: {receptivity!r} "
            f"(expected {SUPPORTED_RECEPTIVITY})"
        )
    known = set(_parse_known_person_ids(known_person_ids))

    parsed_opps: list[SocialExogenousOpportunity] = []
    seen_opp_ids: set[str] = set()
    for raw in opportunities:
        opp = parse_social_exogenous_opportunity(raw)
        if opp.opportunity_id in seen_opp_ids:
            _fail(f"duplicate opportunity_id: {opp.opportunity_id}")
        seen_opp_ids.add(opp.opportunity_id)
        if opp.person_id not in known:
            _fail(f"unknown person_id: {opp.person_id}")
        parsed_opps.append(opp)

    # Availability: one per opportunity_id; only for invites.
    avail_by_opp: dict[str, SocialAvailabilityWindowFact] = {}
    seen_avail_ids: set[str] = set()
    opp_by_id = {o.opportunity_id: o for o in parsed_opps}
    for raw in availability_windows:
        # Peek opportunity_id for lookup before full parse.
        if isinstance(raw, SocialAvailabilityWindowFact):
            oid = raw.opportunity_id
        elif isinstance(raw, Mapping):
            oid = raw.get("opportunity_id")
        else:
            _fail("availability must be a mapping")
            raise AssertionError("unreachable")
        oid_s = _require_nonempty_str(oid, "opportunity_id")
        if oid_s not in opp_by_id:
            _fail(f"availability for unknown opportunity_id: {oid_s}")
        avail = parse_availability_window(raw, now=ctx.now, opportunity=opp_by_id[oid_s])
        if avail.availability_id in seen_avail_ids:
            _fail(f"duplicate availability_id: {avail.availability_id}")
        seen_avail_ids.add(avail.availability_id)
        if avail.opportunity_id in avail_by_opp:
            _fail(f"duplicate availability for opportunity_id: {avail.opportunity_id}")
        avail_by_opp[avail.opportunity_id] = avail

    # Priors
    parsed_priors: list[PriorSocialResponseFact] = []
    seen_prior_ids: set[str] = set()
    terminal_by_opp: dict[str, PriorSocialResponseFact] = {}
    for raw in prior_responses:
        prior = parse_prior_response(raw, now=ctx.now, known_opp_ids=seen_opp_ids)
        if prior.response_id in seen_prior_ids:
            _fail(f"duplicate prior response_id: {prior.response_id}")
        seen_prior_ids.add(prior.response_id)
        if prior.response in TERMINAL_RESPONSES:
            if prior.opportunity_id in terminal_by_opp:
                _fail(
                    f"multiple terminal prior responses for opportunity_id: "
                    f"{prior.opportunity_id}"
                )
            terminal_by_opp[prior.opportunity_id] = prior
        parsed_priors.append(prior)

    battery_band = classify_social_battery_band(ctx.human_state["social_battery"], policy)
    fatigue_band = classify_physical_fatigue_band(
        ctx.human_state["physical_fatigue"], policy
    )
    sleep_band = classify_sleep_pressure_band(ctx.sleep_pressure, policy)

    now_date = _tokyo_local_date(ctx.now, field="now")
    now_d = _parse_iso_date(now_date, label="now_local_date")

    responses: list[SocialResponseDecision] = []
    resolved: list[ResolvedOpportunity] = []
    proposals: list[SocialCommitmentProposal] = []
    diagnostics: list[SocialResponseDiagnostic] = []

    # Process in deterministic opportunity_id order (input-order independent).
    for opportunity in sorted(parsed_opps, key=lambda o: o.opportunity_id):
        mode = _mode_for(opportunity)
        rules = _rule_ids(archetype_id=opportunity.archetype_id, mode=mode)

        # Prior terminal → no new candidate/proposal.
        if opportunity.opportunity_id in terminal_by_opp:
            prior = terminal_by_opp[opportunity.opportunity_id]
            responses.append(
                _decision(
                    opportunity_id=opportunity.opportunity_id,
                    person_id=opportunity.person_id,
                    opportunity_kind=opportunity.opportunity_kind,
                    response=prior.response,
                    reason_code="PRIOR_TERMINAL_RESPONSE",
                    contact_mode=mode,
                    rule_ids=rules,
                )
            )
            continue

        available_d = _parse_iso_date(
            opportunity.available_local_date, label="available_local_date"
        )
        if now_d < available_d:
            diagnostics.append(
                SocialResponseDiagnostic(
                    code="NOT_YET_AVAILABLE",
                    opportunity_id=opportunity.opportunity_id,
                    rule_ids=rules,
                )
            )
            continue

        kind = opportunity.opportunity_kind
        if kind in {"CONTACT_OPPORTUNITY", "POST_WORK_OPPORTUNITY"}:
            if now_d > available_d:
                responses.append(
                    _decision(
                        opportunity_id=opportunity.opportunity_id,
                        person_id=opportunity.person_id,
                        opportunity_kind=kind,
                        response="DECLINE",
                        reason_code="OPPORTUNITY_EXPIRED",
                        contact_mode=mode,
                        rule_ids=rules,
                    )
                )
                continue
        elif kind == "INVITE_OPPORTUNITY":
            if opportunity.target_local_date is None:
                _fail("invite missing target_local_date")
            target_d = _parse_iso_date(
                opportunity.target_local_date, label="target_local_date"
            )
            if now_d > target_d:
                responses.append(
                    _decision(
                        opportunity_id=opportunity.opportunity_id,
                        person_id=opportunity.person_id,
                        opportunity_kind=kind,
                        response="DECLINE",
                        reason_code="OPPORTUNITY_EXPIRED",
                        contact_mode=mode,
                        rule_ids=rules,
                    )
                )
                continue
        else:
            _fail(f"unsupported opportunity_kind: {kind}")

        if kind == "CONTACT_OPPORTUNITY":
            decision, ro, prop = _eval_contact(
                context=ctx,
                opportunity=opportunity,
                mode=mode,
                social_policy=social_policy,
                battery_band=battery_band,
                fatigue_band=fatigue_band,
                sleep_band=sleep_band,
            )
        elif kind == "POST_WORK_OPPORTUNITY":
            decision, ro, prop = _eval_post_work(
                context=ctx,
                opportunity=opportunity,
                mode=mode,
                social_policy=social_policy,
                battery_band=battery_band,
                fatigue_band=fatigue_band,
                sleep_band=sleep_band,
            )
        else:
            decision, ro, prop = _eval_invite(
                context=ctx,
                opportunity=opportunity,
                mode=mode,
                social_policy=social_policy,
                availability=avail_by_opp.get(opportunity.opportunity_id),
                battery_band=battery_band,
                fatigue_band=fatigue_band,
                sleep_band=sleep_band,
            )
        responses.append(decision)
        if ro is not None:
            resolved.append(ro)
        if prop is not None:
            proposals.append(prop)

    # Output uniqueness guards.
    resp_ids = [r.response_id for r in responses]
    if len(resp_ids) != len(set(resp_ids)):
        _fail("duplicate response_id in output")
    opp_keys = [o.opportunity_key for o in resolved]
    if len(opp_keys) != len(set(opp_keys)):
        _fail("duplicate resolved opportunity_key in output")
    prop_ids = [p.proposal_id for p in proposals]
    if len(prop_ids) != len(set(prop_ids)):
        _fail("duplicate proposal_id in output")
    cmt_ids = [p.commitment["commitment_id"] for p in proposals]
    if len(cmt_ids) != len(set(cmt_ids)):
        _fail("duplicate commitment_id in output")

    return SocialResponseAdapterResult(
        responses=_sort_responses(responses),
        opportunities=_sort_opportunities(resolved),
        commitment_proposals=_sort_proposals(proposals),
        diagnostics=_sort_diagnostics(diagnostics),
    )
