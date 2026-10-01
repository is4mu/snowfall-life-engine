"""Life Engine v2 Slice 5B2B — SocialResponseState + runtime social integration.

Pure in-memory ownership of social responses and one-shot composition of
Slice 2G/2H into the production decision path. No persistence, activity
start/finalize, wakeup reconcile, relation mutation, capture, or disk/Git.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from .activity_runtime import derive_activity_instance_id
from .canonical import canonical_hash, canonical_json
from .decisions import candidate_id_for
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .events import validate_active_activity
from .ids import stable_id
from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets, validate_runtime_bundle
from .runtime_decision import (
    RuntimeDecisionFacts,
    RuntimeDecisionFrame,
    RuntimeDecisionTrigger,
    _mapping_snapshot,
    _opportunity_dict,
    build_runtime_decision_frame_with_extras,
    parse_runtime_decision_facts,
)
from .schedule_reducer import insert_commitment
from .schema import reject_binary_floats
from .social_opportunities import (
    PostWorkContextFact,
    SocialContactFact,
    SocialExogenousOpportunity,
    SocialGenerationResult,
    generate_social_opportunities,
    parse_post_work_context_fact,
    parse_social_contact_fact,
)
from .social_response import (
    LIGHTWEIGHT_MODE,
    REASON_CODES,
    RESPONSES,
    TERMINAL_RESPONSES,
    PriorSocialResponseFact,
    SocialAvailabilityWindowFact,
    SocialResponseAdapterResult,
    SocialResponseContext,
    SocialResponseDecision,
    adapt_social_responses,
    parse_social_exogenous_opportunity,
)
from .timeutil import TOKYO, parse_rfc3339, require_canonical_timestamp

PRIOR_TERMINAL_REASON = "PRIOR_TERMINAL_RESPONSE"

_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "character_id",
        "as_of",
        "revision",
        "state_hash",
        "responses",
    }
)
_RESPONSE_FIELDS = frozenset(
    {
        "response_id",
        "opportunity_id",
        "response",
        "decided_at",
        "reason_code",
    }
)
_SOCIAL_FACTS_FIELDS = frozenset(
    {
        "archetype_assignments",
        "contact_facts",
        "post_work_contexts",
        "availability_windows",
        "hard_commitment_blocking_now",
        "social_contact_physical_feasible",
        "post_work_location_feasible",
    }
)
_PENDING_FIELDS = frozenset(
    {
        "opportunity_id",
        "response_id",
        "decided_at",
        "reason_code",
        "selected_opportunity_key",
        "selected_candidate_id",
        "selected_candidate_key",
        "decision_key",
        "character_id",
        "person_id",
        "opportunity_kind",
        "contact_mode",
        "duration_min",
        "duration_max",
        "selected_response_hash",
    }
)

_IMMEDIATE_ACCEPT_OPPORTUNITY_KINDS = frozenset(
    {"CONTACT_OPPORTUNITY", "POST_WORK_OPPORTUNITY"}
)

# Immediate ACCEPT modes only (LOCAL_OUTING belongs to INVITE -> Commitment).
_CONTACT_IMMEDIATE_MODES = frozenset({"CALL", LIGHTWEIGHT_MODE})
_POST_WORK_IMMEDIATE_MODE = "POST_WORK"

_CANONICAL_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

SOCIAL_RESPONSE_STATE_HASH_EXCLUSIONS = frozenset({"revision", "state_hash"})


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_nonempty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _require_bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        _fail(f"{label} must be a bool")
    return value


def _require_true_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be a true int (no bool/float/string)")
    return value


def _reject_unknown(
    data: Mapping[str, Any], allowed: frozenset[str], label: str
) -> None:
    unknown = set(data) - allowed
    if unknown:
        _fail(f"{label} unknown fields: {sorted(unknown)}")


def _require_list_or_tuple(value: object, label: str) -> list[Any] | tuple[Any, ...]:
    if isinstance(value, (str, bytes)):
        _fail(f"{label} must be a list/tuple (bare string/bytes rejected)")
    if not isinstance(value, (list, tuple)):
        _fail(f"{label} must be a list/tuple")
    return value


def _tokyo_local_date(ts: str, *, field: str) -> str:
    return parse_rfc3339(ts, field=field).astimezone(TOKYO).date().isoformat()


def _parse_iso_date(value: object, *, label: str) -> date:
    text = _require_nonempty_str(value, label)
    try:
        d = date.fromisoformat(text)
    except ValueError as exc:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE, f"{label} must be YYYY-MM-DD: {text}"
        ) from exc
    if text != d.isoformat():
        _fail(f"{label} must be exact lexical YYYY-MM-DD (got {text!r})")
    return d


def _monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _seconds_between(
    later: str, earlier: str, *, later_field: str, earlier_field: str
) -> int:
    later_dt = parse_rfc3339(later, field=later_field)
    earlier_dt = parse_rfc3339(earlier, field=earlier_field)
    return int((later_dt - earlier_dt).total_seconds())


# ---------------------------------------------------------------------------
# SocialResponseState
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SocialResponseState:
    schema_version: int
    character_id: str
    as_of: str
    revision: str
    state_hash: str
    responses: tuple[PriorSocialResponseFact, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "character_id": self.character_id,
            "as_of": self.as_of,
            "revision": self.revision,
            "state_hash": self.state_hash,
            "responses": [_response_row_dict(r) for r in self.responses],
        }


def _response_row_dict(row: PriorSocialResponseFact) -> dict[str, Any]:
    return {
        "response_id": row.response_id,
        "opportunity_id": row.opportunity_id,
        "response": row.response,
        "decided_at": row.decided_at,
        "reason_code": row.reason_code,
    }


def _parse_response_row(
    raw: PriorSocialResponseFact | Mapping[str, Any],
    *,
    as_of: str,
) -> PriorSocialResponseFact:
    """Validate one authoritative SocialResponseState row (no known-opp gate)."""
    if isinstance(raw, PriorSocialResponseFact):
        payload = _response_row_dict(raw)
    elif isinstance(raw, Mapping):
        payload = dict(raw)
    else:
        _fail("SocialResponseState response row must be a mapping")
    reject_binary_floats(payload)
    _reject_unknown(payload, _RESPONSE_FIELDS, "SocialResponseState.responses[]")
    response = _require_nonempty_str(payload.get("response"), "response")
    if response not in RESPONSES:
        _fail(f"unknown response: {response}")
    reason = _require_nonempty_str(payload.get("reason_code"), "reason_code")
    if reason not in REASON_CODES:
        _fail(f"unknown reason_code: {reason}")
    if reason == PRIOR_TERMINAL_REASON:
        _fail(
            "PRIOR_TERMINAL_RESPONSE must not be persisted as an authoritative "
            "SocialResponseState row"
        )
    decided_at = require_canonical_timestamp(
        _require_nonempty_str(payload.get("decided_at"), "decided_at"),
        field="decided_at",
    )
    if (
        _seconds_between(
            as_of, decided_at, later_field="as_of", earlier_field="decided_at"
        )
        < 0
    ):
        _fail("decided_at must be <= SocialResponseState.as_of")
    opp_id = _require_nonempty_str(payload.get("opportunity_id"), "opportunity_id")
    response_id = _require_nonempty_str(payload.get("response_id"), "response_id")
    expected_id = stable_id("social-response", opp_id, response)
    if response_id != expected_id:
        _fail(
            "response_id must equal "
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


def compute_social_response_state_hash(state: Mapping[str, Any]) -> str:
    """SHA-256 over normalized semantic payload (excludes revision/state_hash)."""
    semantic = {
        k: state[k]
        for k in sorted(state.keys())
        if k not in SOCIAL_RESPONSE_STATE_HASH_EXCLUSIONS
    }
    responses = semantic.get("responses")
    if isinstance(responses, list):
        semantic = dict(semantic)
        semantic["responses"] = [
            dict(sorted(row.items())) if isinstance(row, Mapping) else row
            for row in responses
        ]
    return canonical_hash(semantic)


def validate_social_response_state(
    raw: SocialResponseState | Mapping[str, Any],
) -> SocialResponseState:
    if isinstance(raw, SocialResponseState):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("SocialResponseState must be mapping or dataclass")
    reject_binary_floats(data)
    _reject_unknown(data, _STATE_FIELDS, "SocialResponseState")
    missing = _STATE_FIELDS - set(data)
    if missing:
        _fail(f"SocialResponseState missing fields: {sorted(missing)}")

    schema_version = data["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        _fail("schema_version must be a true int")
    if schema_version != 1:
        _fail(f"unsupported SocialResponseState schema_version: {schema_version}")

    character_id = _require_nonempty_str(data["character_id"], "character_id")
    as_of = require_canonical_timestamp(data["as_of"], field="as_of")

    rows_raw = _require_list_or_tuple(data["responses"], "responses")
    rows = [_parse_response_row(item, as_of=as_of) for item in rows_raw]
    opp_ids = [r.opportunity_id for r in rows]
    if len(opp_ids) != len(set(opp_ids)):
        dups = sorted({oid for oid in opp_ids if opp_ids.count(oid) > 1})
        _fail(f"duplicate opportunity_id in SocialResponseState: {dups}")
    ordered = tuple(sorted(rows, key=lambda r: r.opportunity_id))
    if [r.opportunity_id for r in rows] != [r.opportunity_id for r in ordered]:
        _fail("SocialResponseState.responses must be sorted by opportunity_id")

    draft = {
        "schema_version": 1,
        "character_id": character_id,
        "as_of": as_of,
        "responses": [_response_row_dict(r) for r in ordered],
    }
    digest = compute_social_response_state_hash(draft)
    revision = _require_nonempty_str(data["revision"], "revision")
    state_hash = _require_nonempty_str(data["state_hash"], "state_hash")
    if state_hash != digest:
        _fail("SocialResponseState state_hash does not match semantic content")
    if revision != digest:
        _fail("SocialResponseState revision does not match semantic content")
    if revision != state_hash:
        _fail("SocialResponseState revision must equal state_hash")

    return SocialResponseState(
        schema_version=1,
        character_id=character_id,
        as_of=as_of,
        revision=revision,
        state_hash=state_hash,
        responses=ordered,
    )


def build_social_response_state(
    *,
    character_id: str,
    as_of: str,
    responses: Sequence[PriorSocialResponseFact | Mapping[str, Any]] = (),
) -> SocialResponseState:
    as_of_c = require_canonical_timestamp(as_of, field="as_of")
    cid = _require_nonempty_str(character_id, "character_id")
    rows_in = list(responses)
    rows = [_parse_response_row(item, as_of=as_of_c) for item in rows_in]
    opp_ids = [r.opportunity_id for r in rows]
    if len(opp_ids) != len(set(opp_ids)):
        dups = sorted({oid for oid in opp_ids if opp_ids.count(oid) > 1})
        _fail(f"duplicate opportunity_id in SocialResponseState: {dups}")
    ordered = tuple(sorted(rows, key=lambda r: r.opportunity_id))
    draft = {
        "schema_version": 1,
        "character_id": cid,
        "as_of": as_of_c,
        "responses": [_response_row_dict(r) for r in ordered],
    }
    digest = compute_social_response_state_hash(draft)
    return SocialResponseState(
        schema_version=1,
        character_id=cid,
        as_of=as_of_c,
        revision=digest,
        state_hash=digest,
        responses=ordered,
    )


def apply_social_response_decision(
    state: SocialResponseState | Mapping[str, Any],
    decision: PriorSocialResponseFact | Mapping[str, Any] | SocialResponseDecision,
    *,
    as_of: str | None = None,
) -> SocialResponseState:
    """Apply one response transition. Terminal immutable; DEFER NOOP/replace rules."""
    prior = validate_social_response_state(state)
    as_of_c = require_canonical_timestamp(
        as_of if as_of is not None else prior.as_of, field="as_of"
    )
    if (
        _seconds_between(
            as_of_c, prior.as_of, later_field="as_of", earlier_field="prior.as_of"
        )
        < 0
    ):
        _fail("apply_social_response_decision as_of before prior.as_of")

    if isinstance(decision, SocialResponseDecision):
        raw_decision: dict[str, Any] = {
            "response_id": decision.response_id,
            "opportunity_id": decision.opportunity_id,
            "response": decision.response,
            "decided_at": as_of_c,
            "reason_code": decision.reason_code,
        }
    elif isinstance(decision, PriorSocialResponseFact):
        raw_decision = _response_row_dict(decision)
    elif isinstance(decision, Mapping):
        raw_decision = dict(decision)
    else:
        _fail(
            "decision must be mapping, PriorSocialResponseFact, or SocialResponseDecision"
        )

    if raw_decision.get("reason_code") == PRIOR_TERMINAL_REASON:
        _fail(
            "PRIOR_TERMINAL_RESPONSE must not be persisted as an authoritative "
            "SocialResponseState row"
        )

    incoming = _parse_response_row(raw_decision, as_of=as_of_c)
    by_opp = {r.opportunity_id: r for r in prior.responses}
    existing = by_opp.get(incoming.opportunity_id)

    if existing is None:
        by_opp[incoming.opportunity_id] = incoming
    elif existing.response in TERMINAL_RESPONSES:
        # Exact authoritative terminal replay only → NOOP.
        if (
            incoming.response == existing.response
            and incoming.reason_code == existing.reason_code
            and incoming.decided_at == existing.decided_at
            and incoming.response_id == existing.response_id
        ):
            return prior
        _fail(
            "terminal SocialResponseState row is immutable for opportunity_id="
            f"{incoming.opportunity_id!r} (exact semantic replay required for NOOP)"
        )
    else:
        # Existing DEFER.
        if incoming.decided_at == existing.decided_at:
            if (
                incoming.response == existing.response
                and incoming.reason_code == existing.reason_code
            ):
                return prior
            _fail(
                "equal decided_at with conflicting SocialResponseState semantics "
                f"for opportunity_id={incoming.opportunity_id!r}"
            )
        if (
            _seconds_between(
                incoming.decided_at,
                existing.decided_at,
                later_field="incoming.decided_at",
                earlier_field="existing.decided_at",
            )
            < 0
        ):
            _fail(
                "incoming decided_at before existing DEFER decided_at for "
                f"opportunity_id={incoming.opportunity_id!r}"
            )
        if (
            incoming.response == "DEFER"
            and existing.response == "DEFER"
            and incoming.reason_code == existing.reason_code
        ):
            # Same DEFER reason later → NOOP; do not rewrite decided_at / hash.
            return prior
        by_opp[incoming.opportunity_id] = incoming

    return build_social_response_state(
        character_id=prior.character_id,
        as_of=as_of_c,
        responses=list(by_opp.values()),
    )


_SUCCESSFUL_START_FIELDS = frozenset({"active_activity"})


@dataclass(frozen=True)
class SuccessfulImmediateSocialStart:
    """Authoritative 5B1 activity-start evidence for 5B2C handoff.

    Caller supplies a materialized ActiveActivity produced by the Slice 5B1
    selection→activity bridge. 5B2B validates SOCIAL_CONTACT provenance and
    deterministic identity bindings; it does not start/finalize activities.
    """

    active_activity: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {"active_activity": dict(self.active_activity)}


def parse_successful_immediate_social_start(
    raw: SuccessfulImmediateSocialStart | Mapping[str, Any],
) -> SuccessfulImmediateSocialStart:
    if isinstance(raw, SuccessfulImmediateSocialStart):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("SuccessfulImmediateSocialStart must be mapping or dataclass")
    reject_binary_floats(data)
    _reject_unknown(data, _SUCCESSFUL_START_FIELDS, "SuccessfulImmediateSocialStart")
    missing = _SUCCESSFUL_START_FIELDS - set(data)
    if missing:
        _fail(f"SuccessfulImmediateSocialStart missing fields: {sorted(missing)}")
    activity_raw = data["active_activity"]
    if not isinstance(activity_raw, Mapping):
        _fail("SuccessfulImmediateSocialStart.active_activity must be a mapping")
    return SuccessfulImmediateSocialStart(active_activity=dict(activity_raw))


def _expected_social_resolved_opportunity_key(opportunity_id: str) -> str:
    return stable_id("social-resolved-opportunity", opportunity_id)


def validate_pending_immediate_accept_bindings(
    pending: PendingImmediateAccept,
) -> PendingImmediateAccept:
    """Fail closed on forged/stale pending identity or selection bindings."""
    expected_response_id = stable_id("social-response", pending.opportunity_id, "ACCEPT")
    if pending.response_id != expected_response_id:
        _fail(
            "PendingImmediateAccept.response_id must equal "
            'stable_id("social-response", opportunity_id, "ACCEPT") '
            f"(got {pending.response_id!r}, expected {expected_response_id!r})"
        )
    if pending.reason_code not in REASON_CODES:
        _fail(f"PendingImmediateAccept unknown reason_code: {pending.reason_code}")
    if pending.reason_code == PRIOR_TERMINAL_REASON:
        _fail(
            "PendingImmediateAccept must not carry PRIOR_TERMINAL_RESPONSE "
            "as the original 2H reason_code"
        )
    expected_opp_key = _expected_social_resolved_opportunity_key(pending.opportunity_id)
    if pending.selected_opportunity_key != expected_opp_key:
        _fail(
            "PendingImmediateAccept.selected_opportunity_key must equal "
            'stable_id("social-resolved-opportunity", opportunity_id) '
            f"(got {pending.selected_opportunity_key!r}, expected {expected_opp_key!r})"
        )
    # Slice 2D maps social ResolvedOpportunity.opportunity_key → candidate_key.
    if pending.selected_candidate_key != pending.selected_opportunity_key:
        _fail(
            "PendingImmediateAccept.selected_candidate_key must equal "
            "selected_opportunity_key for SOCIAL_CONTACT candidates"
        )
    expected_candidate_id = candidate_id_for(
        character_id=pending.character_id,
        decision_key=pending.decision_key,
        candidate_key=pending.selected_candidate_key,
    )
    if pending.selected_candidate_id != expected_candidate_id:
        _fail(
            "PendingImmediateAccept.selected_candidate_id must equal "
            "candidate_id_for(character_id, decision_key, selected_candidate_key) "
            f"(got {pending.selected_candidate_id!r}, expected {expected_candidate_id!r})"
        )

    # Policy-independent Slice 2H immediate ACCEPT kind/mode/reason matrix.
    if pending.opportunity_kind == "CONTACT_OPPORTUNITY":
        if pending.reason_code != "CONTACT_READY":
            _fail(
                "CONTACT_OPPORTUNITY PendingImmediateAccept.reason_code "
                "must be CONTACT_READY "
                f"(got {pending.reason_code!r})"
            )
        if pending.contact_mode not in _CONTACT_IMMEDIATE_MODES:
            _fail(
                "CONTACT_OPPORTUNITY PendingImmediateAccept.contact_mode "
                "must be CALL or LIGHTWEIGHT "
                f"(got {pending.contact_mode!r}; LOCAL_OUTING is invite/commitment-only)"
            )
        if pending.contact_mode == LIGHTWEIGHT_MODE:
            if pending.duration_min is not None or pending.duration_max is not None:
                _fail("LIGHTWEIGHT PendingImmediateAccept must have null durations")
        else:
            if pending.duration_min is None or pending.duration_max is None:
                _fail("CALL PendingImmediateAccept requires duration_min/max")
    elif pending.opportunity_kind == "POST_WORK_OPPORTUNITY":
        if pending.reason_code != "POST_WORK_READY":
            _fail(
                "POST_WORK_OPPORTUNITY PendingImmediateAccept.reason_code "
                "must be POST_WORK_READY "
                f"(got {pending.reason_code!r})"
            )
        if pending.contact_mode != _POST_WORK_IMMEDIATE_MODE:
            _fail(
                "POST_WORK_OPPORTUNITY PendingImmediateAccept.contact_mode "
                "must be POST_WORK "
                f"(got {pending.contact_mode!r})"
            )
        if pending.duration_min is None or pending.duration_max is None:
            _fail("POST_WORK PendingImmediateAccept requires duration_min/max")
    else:
        _fail(
            "PendingImmediateAccept.opportunity_kind must be "
            "CONTACT_OPPORTUNITY or POST_WORK_OPPORTUNITY "
            f"(got {pending.opportunity_kind!r})"
        )
    return pending


def _validate_successful_start_against_pending(
    *,
    pending: PendingImmediateAccept,
    successful_start: SuccessfulImmediateSocialStart,
    social_character_id: str,
) -> Mapping[str, Any]:
    """Bind terminalization to authoritative ActiveActivity / runtime_context."""
    if pending.character_id != social_character_id:
        _fail(
            "PendingImmediateAccept.character_id must equal "
            "SocialResponseState.character_id"
        )
    activity = validate_active_activity(dict(successful_start.active_activity))
    assert activity is not None
    if activity.get("activity_type") != "SOCIAL_CONTACT":
        _fail(
            "successful-start ActiveActivity.activity_type must be SOCIAL_CONTACT "
            f"(got {activity.get('activity_type')!r})"
        )
    ctx = activity.get("runtime_context")
    if not isinstance(ctx, Mapping):
        _fail(
            "successful-start ActiveActivity.runtime_context required "
            "(5B1 activity-start evidence)"
        )
    if ctx.get("action_kind") != "SOCIAL_CONTACT":
        _fail(
            "successful-start runtime_context.action_kind must be SOCIAL_CONTACT "
            f"(got {ctx.get('action_kind')!r})"
        )
    if ctx.get("source_kind") != "SOCIAL":
        _fail(
            "successful-start runtime_context.source_kind must be SOCIAL "
            f"(got {ctx.get('source_kind')!r})"
        )
    if ctx.get("source_ref") != pending.opportunity_id:
        _fail(
            "successful-start runtime_context.source_ref must equal "
            "PendingImmediateAccept.opportunity_id"
        )
    if ctx.get("decision_key") != pending.decision_key:
        _fail(
            "successful-start runtime_context.decision_key must equal "
            "PendingImmediateAccept.decision_key"
        )
    if ctx.get("selected_candidate_key") != pending.selected_candidate_key:
        _fail(
            "successful-start runtime_context.selected_candidate_key must equal "
            "PendingImmediateAccept.selected_candidate_key"
        )
    if ctx.get("selected_candidate_id") != pending.selected_candidate_id:
        _fail(
            "successful-start runtime_context.selected_candidate_id must equal "
            "PendingImmediateAccept.selected_candidate_id"
        )
    expected_candidate_id = candidate_id_for(
        character_id=pending.character_id,
        decision_key=pending.decision_key,
        candidate_key=pending.selected_candidate_key,
    )
    if ctx.get("selected_candidate_id") != expected_candidate_id:
        _fail(
            "successful-start selected_candidate_id must equal "
            "candidate_id_for(character_id, decision_key, selected_candidate_key)"
        )
    expected_activity_id = derive_activity_instance_id(
        character_id=pending.character_id,
        decision_key=pending.decision_key,
        selected_candidate_id=pending.selected_candidate_id,
    )
    if activity.get("activity_instance_id") != expected_activity_id:
        _fail(
            "successful-start activity_instance_id must equal "
            "derive_activity_instance_id(character_id, decision_key, "
            "selected_candidate_id)"
        )
    evidence = activity.get("decision_evidence")
    if not isinstance(evidence, Mapping):
        _fail(
            "successful-start ActiveActivity.decision_evidence required "
            "(5B1 activity-start evidence)"
        )
    if evidence.get("decision_type") != "ACTION_SELECTION":
        _fail(
            "successful-start decision_evidence.decision_type must be "
            "ACTION_SELECTION"
        )
    if evidence.get("selected_result") != pending.selected_candidate_id:
        _fail(
            "successful-start decision_evidence.selected_result must equal "
            "PendingImmediateAccept.selected_candidate_id"
        )
    causes = activity.get("causes")
    if not isinstance(causes, (list, tuple)) or not causes:
        _fail(
            "successful-start ActiveActivity.causes required "
            "(5B1 activity-start evidence)"
        )
    cause_pairs = {
        (row.get("cause_type"), row.get("ref"))
        for row in causes
        if isinstance(row, Mapping)
    }
    if ("DECISION", pending.decision_key) not in cause_pairs:
        _fail(
            "successful-start causes must include DECISION ref equal to "
            "PendingImmediateAccept.decision_key"
        )
    if ("SOCIAL", pending.opportunity_id) not in cause_pairs:
        _fail(
            "successful-start causes must include SOCIAL ref equal to "
            "PendingImmediateAccept.opportunity_id"
        )
    return activity


def apply_pending_immediate_accept_after_successful_start(
    state: SocialResponseState | Mapping[str, Any],
    pending: PendingImmediateAccept | Mapping[str, Any],
    *,
    successful_start: SuccessfulImmediateSocialStart | Mapping[str, Any],
    as_of: str | None = None,
) -> SocialResponseState:
    """5B2C handoff helper: terminalize provisional ACCEPT at most once.

    Requires authoritative ActiveActivity evidence (SOCIAL_CONTACT + SOCIAL
    source_ref + deterministic candidate/activity ids). Does not start activities.
    """
    prior = validate_social_response_state(state)
    pending_obj = parse_pending_immediate_accept(pending)
    start = parse_successful_immediate_social_start(successful_start)
    _validate_successful_start_against_pending(
        pending=pending_obj,
        successful_start=start,
        social_character_id=prior.character_id,
    )
    return apply_social_response_decision(
        prior,
        {
            "response_id": pending_obj.response_id,
            "opportunity_id": pending_obj.opportunity_id,
            "response": "ACCEPT",
            "decided_at": pending_obj.decided_at,
            "reason_code": pending_obj.reason_code,
        },
        as_of=as_of if as_of is not None else pending_obj.decided_at,
    )


# ---------------------------------------------------------------------------
# Runtime social facts / pending handoff / result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeSocialFacts:
    archetype_assignments: tuple[Mapping[str, Any], ...]
    contact_facts: tuple[SocialContactFact | Mapping[str, Any], ...]
    post_work_contexts: tuple[PostWorkContextFact | Mapping[str, Any], ...]
    availability_windows: tuple[SocialAvailabilityWindowFact | Mapping[str, Any], ...]
    hard_commitment_blocking_now: bool
    social_contact_physical_feasible: bool
    post_work_location_feasible: bool


@dataclass(frozen=True)
class PendingImmediateAccept:
    opportunity_id: str
    response_id: str
    decided_at: str
    reason_code: str
    selected_opportunity_key: str
    selected_candidate_id: str
    selected_candidate_key: str
    decision_key: str
    character_id: str
    person_id: str
    opportunity_kind: str
    contact_mode: str
    duration_min: int | None
    duration_max: int | None
    selected_response_hash: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "response_id": self.response_id,
            "decided_at": self.decided_at,
            "reason_code": self.reason_code,
            "selected_opportunity_key": self.selected_opportunity_key,
            "selected_candidate_id": self.selected_candidate_id,
            "selected_candidate_key": self.selected_candidate_key,
            "decision_key": self.decision_key,
            "character_id": self.character_id,
            "person_id": self.person_id,
            "opportunity_kind": self.opportunity_kind,
            "contact_mode": self.contact_mode,
            "duration_min": self.duration_min,
            "duration_max": self.duration_max,
            "selected_response_hash": self.selected_response_hash,
        }


def build_selected_social_response_hash(decision: SocialResponseDecision) -> str:
    """Deterministic hash over the exact selected 2H decision semantics."""
    rule_ids = decision.rule_ids
    if isinstance(rule_ids, (str, bytes)) or not isinstance(rule_ids, (list, tuple)):
        _fail("SocialResponseDecision.rule_ids must be list/tuple for hash")
    return canonical_hash(
        {
            "response_id": decision.response_id,
            "opportunity_id": decision.opportunity_id,
            "person_id": decision.person_id,
            "opportunity_kind": decision.opportunity_kind,
            "response": decision.response,
            "reason_code": decision.reason_code,
            "contact_mode": decision.contact_mode,
            "duration_min": decision.duration_min,
            "duration_max": decision.duration_max,
            "resolved_opportunity_key": decision.resolved_opportunity_key,
            "commitment_proposal_id": decision.commitment_proposal_id,
            "rule_ids": list(rule_ids),
        }
    )


@dataclass(frozen=True)
class RuntimeSocialDecisionResult:
    frame: RuntimeDecisionFrame
    working_bundle: RuntimeBundle
    social_response_state: SocialResponseState
    social_generation: SocialGenerationResult
    social_response: SocialResponseAdapterResult
    pending_immediate_accept: PendingImmediateAccept | None


def parse_pending_immediate_accept(
    raw: PendingImmediateAccept | Mapping[str, Any],
) -> PendingImmediateAccept:
    if isinstance(raw, PendingImmediateAccept):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("PendingImmediateAccept must be mapping or dataclass")
    reject_binary_floats(data)
    _reject_unknown(data, _PENDING_FIELDS, "PendingImmediateAccept")
    missing = _PENDING_FIELDS - set(data)
    if missing:
        _fail(f"PendingImmediateAccept missing fields: {sorted(missing)}")

    contact_mode = _require_nonempty_str(data["contact_mode"], "contact_mode")
    # LOCAL_OUTING and other invite/commitment modes are rejected by the
    # kind/mode matrix in validate_pending_immediate_accept_bindings.
    if contact_mode == "LOCAL_OUTING":
        _fail(
            "PendingImmediateAccept.contact_mode must not be LOCAL_OUTING "
            "(LOCAL_OUTING belongs to INVITE_OPPORTUNITY -> Commitment)"
        )
    allowed_modes = _CONTACT_IMMEDIATE_MODES | {_POST_WORK_IMMEDIATE_MODE}
    if contact_mode not in allowed_modes:
        _fail(f"PendingImmediateAccept.contact_mode invalid: {contact_mode!r}")

    duration_min = data["duration_min"]
    duration_max = data["duration_max"]
    if contact_mode == LIGHTWEIGHT_MODE:
        if duration_min is not None or duration_max is not None:
            _fail("LIGHTWEIGHT PendingImmediateAccept must have null durations")
        duration_min_i: int | None = None
        duration_max_i: int | None = None
    else:
        if isinstance(duration_min, bool) or not isinstance(duration_min, int):
            _fail("PendingImmediateAccept.duration_min must be int")
        if isinstance(duration_max, bool) or not isinstance(duration_max, int):
            _fail("PendingImmediateAccept.duration_max must be int")
        if duration_min < 1 or duration_max < duration_min:
            _fail("PendingImmediateAccept duration_min/max invalid")
        duration_min_i = duration_min
        duration_max_i = duration_max

    opportunity_kind = _require_nonempty_str(data["opportunity_kind"], "opportunity_kind")
    if opportunity_kind not in _IMMEDIATE_ACCEPT_OPPORTUNITY_KINDS:
        _fail(
            "PendingImmediateAccept.opportunity_kind must be "
            "CONTACT_OPPORTUNITY or POST_WORK_OPPORTUNITY "
            f"(got {opportunity_kind!r})"
        )

    response_hash = data["selected_response_hash"]
    if response_hash is not None:
        response_hash = _require_nonempty_str(
            response_hash, "selected_response_hash"
        )
        if not _CANONICAL_HASH_RE.fullmatch(response_hash):
            _fail(
                "PendingImmediateAccept.selected_response_hash must be 64 hex chars "
                "([0-9a-f]{64})"
            )

    pending = PendingImmediateAccept(
        opportunity_id=_require_nonempty_str(data["opportunity_id"], "opportunity_id"),
        response_id=_require_nonempty_str(data["response_id"], "response_id"),
        decided_at=require_canonical_timestamp(data["decided_at"], field="decided_at"),
        reason_code=_require_nonempty_str(data["reason_code"], "reason_code"),
        selected_opportunity_key=_require_nonempty_str(
            data["selected_opportunity_key"], "selected_opportunity_key"
        ),
        selected_candidate_id=_require_nonempty_str(
            data["selected_candidate_id"], "selected_candidate_id"
        ),
        selected_candidate_key=_require_nonempty_str(
            data["selected_candidate_key"], "selected_candidate_key"
        ),
        decision_key=_require_nonempty_str(data["decision_key"], "decision_key"),
        character_id=_require_nonempty_str(data["character_id"], "character_id"),
        person_id=_require_nonempty_str(data["person_id"], "person_id"),
        opportunity_kind=opportunity_kind,
        contact_mode=contact_mode,
        duration_min=duration_min_i,
        duration_max=duration_max_i,
        selected_response_hash=response_hash,
    )
    return validate_pending_immediate_accept_bindings(pending)


def parse_runtime_social_facts(
    raw: RuntimeSocialFacts | Mapping[str, Any],
) -> RuntimeSocialFacts:
    if isinstance(raw, RuntimeSocialFacts):
        data = {
            "archetype_assignments": list(raw.archetype_assignments),
            "contact_facts": list(raw.contact_facts),
            "post_work_contexts": list(raw.post_work_contexts),
            "availability_windows": list(raw.availability_windows),
            "hard_commitment_blocking_now": raw.hard_commitment_blocking_now,
            "social_contact_physical_feasible": raw.social_contact_physical_feasible,
            "post_work_location_feasible": raw.post_work_location_feasible,
        }
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeSocialFacts must be mapping or dataclass")
    reject_binary_floats(data)
    _reject_unknown(data, _SOCIAL_FACTS_FIELDS, "RuntimeSocialFacts")
    missing = _SOCIAL_FACTS_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeSocialFacts missing fields: {sorted(missing)}")

    assignments_raw = _require_list_or_tuple(
        data["archetype_assignments"], "archetype_assignments"
    )
    assignments: list[dict[str, Any]] = []
    for i, item in enumerate(assignments_raw):
        if not isinstance(item, Mapping):
            _fail(f"archetype_assignments[{i}] must be a mapping")
        assignments.append(deepcopy(dict(item)))

    contacts_raw = _require_list_or_tuple(data["contact_facts"], "contact_facts")
    work_raw = _require_list_or_tuple(data["post_work_contexts"], "post_work_contexts")
    avail_raw = _require_list_or_tuple(
        data["availability_windows"], "availability_windows"
    )

    return RuntimeSocialFacts(
        archetype_assignments=tuple(assignments),
        contact_facts=tuple(contacts_raw),
        post_work_contexts=tuple(work_raw),
        availability_windows=tuple(avail_raw),
        hard_commitment_blocking_now=_require_bool(
            data["hard_commitment_blocking_now"], "hard_commitment_blocking_now"
        ),
        social_contact_physical_feasible=_require_bool(
            data["social_contact_physical_feasible"],
            "social_contact_physical_feasible",
        ),
        post_work_location_feasible=_require_bool(
            data["post_work_location_feasible"], "post_work_location_feasible"
        ),
    )


def _contact_dict(parsed: SocialContactFact) -> dict[str, Any]:
    return {
        "contact_id": parsed.contact_id,
        "person_id": parsed.person_id,
        "actual_end": parsed.actual_end,
        "finalized_at": parsed.finalized_at,
    }


def _work_dict(parsed: PostWorkContextFact) -> dict[str, Any]:
    return {
        "work_context_id": parsed.work_context_id,
        "actual_end": parsed.actual_end,
        "finalized_at": parsed.finalized_at,
        "participant_person_ids": list(parsed.participant_person_ids),
    }


def _normalized_social_facts_dict(
    facts: RuntimeSocialFacts,
    *,
    known_person_ids: Sequence[str],
    as_of: str,
) -> dict[str, Any]:
    known = set(known_person_ids)
    contacts = []
    for c in facts.contact_facts:
        parsed = (
            c
            if isinstance(c, SocialContactFact)
            else parse_social_contact_fact(c, known=known, as_of=as_of)
        )
        contacts.append(_contact_dict(parsed))
    contacts.sort(key=lambda row: row["contact_id"])

    works = []
    for w in facts.post_work_contexts:
        parsed = (
            w
            if isinstance(w, PostWorkContextFact)
            else parse_post_work_context_fact(w, known=known, as_of=as_of)
        )
        works.append(_work_dict(parsed))
    works.sort(key=lambda row: row["work_context_id"])

    assignments = sorted(
        (dict(sorted(a.items())) for a in facts.archetype_assignments),
        key=lambda a: a.get("person_id", canonical_json(a)),
    )

    avails: list[dict[str, Any]] = []
    for item in facts.availability_windows:
        if isinstance(item, SocialAvailabilityWindowFact):
            avails.append(
                {
                    "availability_id": item.availability_id,
                    "opportunity_id": item.opportunity_id,
                    "earliest_start": item.earliest_start,
                    "latest_end": item.latest_end,
                    "location_id": item.location_id,
                    "hard_conflict": item.hard_conflict,
                }
            )
        elif isinstance(item, Mapping):
            avails.append(dict(sorted(dict(item).items())))
        else:
            _fail("availability_windows item must be mapping")
    avails.sort(key=lambda a: a.get("availability_id", canonical_json(a)))

    return {
        "archetype_assignments": assignments,
        "contact_facts": contacts,
        "post_work_contexts": works,
        "availability_windows": avails,
        "hard_commitment_blocking_now": facts.hard_commitment_blocking_now,
        "social_contact_physical_feasible": facts.social_contact_physical_feasible,
        "post_work_location_feasible": facts.post_work_location_feasible,
    }


# ---------------------------------------------------------------------------
# Historical-week 2G projection
# ---------------------------------------------------------------------------


def relevant_week_start_dates(
    *, decision_local_date: str, horizon_days: int
) -> tuple[str, ...]:
    """Monday week starts covering [D - H, D] inclusive."""
    d = _parse_iso_date(decision_local_date, label="decision_local_date")
    h = _require_true_int(horizon_days, "horizon_days")
    if h < 0:
        _fail("horizon_days must be >= 0")
    earliest = d - timedelta(days=h)
    starts: list[date] = []
    cursor = _monday_on_or_before(earliest)
    last = _monday_on_or_before(d)
    while cursor <= last:
        starts.append(cursor)
        cursor = cursor + timedelta(days=7)
    return tuple(s.isoformat() for s in starts)


def is_response_eligible_opportunity(
    opp: SocialExogenousOpportunity | Mapping[str, Any],
    *,
    decision_local_date: str,
) -> bool:
    parsed = (
        opp
        if isinstance(opp, SocialExogenousOpportunity)
        else parse_social_exogenous_opportunity(opp)
    )
    d = _parse_iso_date(decision_local_date, label="decision_local_date")
    available = _parse_iso_date(
        parsed.available_local_date, label="available_local_date"
    )
    kind = parsed.opportunity_kind
    if kind in {"CONTACT_OPPORTUNITY", "POST_WORK_OPPORTUNITY"}:
        return available == d
    if kind == "INVITE_OPPORTUNITY":
        if parsed.target_local_date is None:
            _fail("invite missing target_local_date")
        target = _parse_iso_date(parsed.target_local_date, label="target_local_date")
        return available <= d <= target
    _fail(f"unsupported opportunity_kind: {kind}")
    raise AssertionError("unreachable")


def merge_social_exogenous_opportunities(
    *groups: Sequence[SocialExogenousOpportunity | Mapping[str, Any]],
) -> tuple[SocialExogenousOpportunity, ...]:
    """Merge 2G opportunity groups by opportunity_id; conflicting payloads fail closed."""
    by_opp: dict[str, SocialExogenousOpportunity] = {}
    for group in groups:
        if isinstance(group, (str, bytes)) or not isinstance(group, (list, tuple)):
            _fail("opportunity group must be a list/tuple")
        for raw in group:
            opp = (
                raw
                if isinstance(raw, SocialExogenousOpportunity)
                else parse_social_exogenous_opportunity(raw)
            )
            existing = by_opp.get(opp.opportunity_id)
            if existing is not None:
                if canonical_json(existing.as_dict()) != canonical_json(opp.as_dict()):
                    _fail(
                        "duplicate opportunity_id with different payload: "
                        f"{opp.opportunity_id}"
                    )
                continue
            by_opp[opp.opportunity_id] = opp
    return tuple(sorted(by_opp.values(), key=lambda o: o.opportunity_id))


def generate_historical_social_opportunities(
    *,
    character_id: str,
    world_seed: Any,
    as_of: str,
    behavior_policy: Mapping[str, Any],
    known_person_ids: Sequence[str],
    assignments: Sequence[Mapping[str, Any]],
    contacts: Sequence[SocialContactFact | Mapping[str, Any]] = (),
    post_work_contexts: Sequence[PostWorkContextFact | Mapping[str, Any]] = (),
) -> SocialGenerationResult:
    """Run 2G across policy-derived historical weeks; post-work once (current week)."""
    policy = require_v2_production_format(behavior_policy)
    as_of_c = require_canonical_timestamp(as_of, field="as_of")
    decision_date = _tokyo_local_date(as_of_c, field="as_of")
    invite = policy["social_policy"]["archetypes"]["LOCAL_CLOSE_INVITER"]["invite"]
    horizon = _require_true_int(
        invite["future_horizon_max_days"], "future_horizon_max_days"
    )
    week_starts = relevant_week_start_dates(
        decision_local_date=decision_date, horizon_days=horizon
    )
    if not week_starts:
        _fail("historical-week projection produced no week_start_dates")
    current_week = week_starts[-1]

    merged_slots: list[Any] = []
    merged_slot_res: list[Any] = []
    merged_post: list[Any] = []
    merged_diags: list[Any] = []
    week_opportunity_groups: list[tuple[SocialExogenousOpportunity, ...]] = []

    for week_start in week_starts:
        work_in = post_work_contexts if week_start == current_week else ()
        result = generate_social_opportunities(
            context={
                "character_id": character_id,
                "world_seed": world_seed,
                "week_start_date": week_start,
                "as_of": as_of_c,
            },
            behavior_policy=policy,
            known_person_ids=known_person_ids,
            assignments=assignments,
            contacts=contacts,
            post_work_contexts=work_in,
        )
        merged_slots.extend(result.slots)
        merged_slot_res.extend(result.slot_resolutions)
        merged_post.extend(result.post_work_resolutions)
        merged_diags.extend(result.diagnostics)
        week_opportunity_groups.append(result.opportunities)

    merged_opps = merge_social_exogenous_opportunities(*week_opportunity_groups)

    slots_by_id = {s.slot_id: s for s in merged_slots}
    slot_res_by_id = {r.slot_id: r for r in merged_slot_res}
    post_ids = [f"{r.work_context_id}:{r.person_id}" for r in merged_post]
    if len(post_ids) != len(set(post_ids)):
        _fail("post-work resolutions evaluated more than once")

    eligible = tuple(
        o
        for o in merged_opps
        if is_response_eligible_opportunity(o, decision_local_date=decision_date)
    )

    return SocialGenerationResult(
        slots=tuple(sorted(slots_by_id.values(), key=lambda s: s.slot_id)),
        slot_resolutions=tuple(
            sorted(slot_res_by_id.values(), key=lambda r: r.slot_id)
        ),
        post_work_resolutions=tuple(
            sorted(merged_post, key=lambda r: (r.work_context_id, r.person_id))
        ),
        opportunities=eligible,
        diagnostics=tuple(
            sorted(
                merged_diags,
                key=lambda d: (d.source_kind, d.source_ref, d.code),
            )
        ),
    )


# ---------------------------------------------------------------------------
# Working bundle helpers
# ---------------------------------------------------------------------------


def _clone_bundle_with_schedule(
    bundle: RuntimeBundle,
    *,
    schedule_state: Mapping[str, Any],
    current_state: Mapping[str, Any],
) -> RuntimeBundle:
    return RuntimeBundle(
        current_state=deepcopy(dict(current_state)),
        schedule_state=deepcopy(dict(schedule_state)),
        relation_state=deepcopy(dict(bundle.relation_state)),
        home_state=deepcopy(dict(bundle.home_state)),
        consumables_state=deepcopy(dict(bundle.consumables_state)),
        wardrobe_state=deepcopy(dict(bundle.wardrobe_state)),
        finance_state=deepcopy(dict(bundle.finance_state)),
    )


def _update_current_schedule_refs(
    current: Mapping[str, Any],
    schedule: Mapping[str, Any],
) -> dict[str, Any]:
    out = deepcopy(dict(current))
    refs = dict(out.get("domain_refs") or {})
    refs["schedule_revision"] = schedule["revision"]
    refs["schedule_hash"] = schedule["state_hash"]
    out["domain_refs"] = refs
    return out


# ---------------------------------------------------------------------------
# Production social decision composition
# ---------------------------------------------------------------------------


def build_runtime_social_decision(
    *,
    bundle: RuntimeBundle | Mapping[str, Any],
    reference_sets: RuntimeReferenceSets,
    behavior_policy: Mapping[str, Any],
    trigger: RuntimeDecisionTrigger | Mapping[str, Any],
    facts: RuntimeDecisionFacts | Mapping[str, Any],
    social_response_state: SocialResponseState | Mapping[str, Any],
    social_facts: RuntimeSocialFacts | Mapping[str, Any],
) -> RuntimeSocialDecisionResult:
    """Compose one social-aware RuntimeDecisionFrame with working state updates.

    Order follows Issue #73 §8. Single Slice 2D resolver. No persistence.
    """
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
    social_facts_before = (
        None
        if isinstance(social_facts, RuntimeSocialFacts)
        else canonical_json(_mapping_snapshot(dict(social_facts)))
    )
    social_state_before = (
        None
        if isinstance(social_response_state, SocialResponseState)
        else canonical_json(_mapping_snapshot(dict(social_response_state)))
    )

    validated = validate_runtime_bundle(bundle, reference_sets=reference_sets)
    input_bundle = validated
    current = validated.current_state
    schedule = validated.schedule_state
    character_id = current["character_id"]
    processed_through = require_canonical_timestamp(
        current["processed_through"], field="processed_through"
    )
    state_revision_before = current["state_revision"]
    relation_before = canonical_json(validated.relation_state)
    queue_before = canonical_json(current["pending_queue"])
    open_promises_before = canonical_json(
        [
            {"person_id": p.get("person_id"), "open_promises": p.get("open_promises")}
            for p in validated.relation_state.get("people", [])
        ]
    )

    policy = require_v2_production_format(behavior_policy)
    if policy["behavior_policy_version"] != current["behavior_policy_version"]:
        raise LifeEngineError(
            ErrorCode.UNSUPPORTED_VERSION,
            "behavior_policy_version mismatch: "
            f"policy={policy['behavior_policy_version']!r} "
            f"current={current['behavior_policy_version']!r}",
        )

    pre_social_state = validate_social_response_state(social_response_state)
    if pre_social_state.character_id != character_id:
        _fail(
            "SocialResponseState.character_id mismatch: "
            f"{pre_social_state.character_id!r} vs {character_id!r}"
        )
    if (
        _seconds_between(
            processed_through,
            pre_social_state.as_of,
            later_field="processed_through",
            earlier_field="SocialResponseState.as_of",
        )
        < 0
    ):
        _fail("SocialResponseState.as_of after CurrentState.processed_through")

    parsed_facts = parse_runtime_decision_facts(facts, now=processed_through)
    parsed_social = parse_runtime_social_facts(social_facts)
    known_persons = sorted(reference_sets.known_person_ids)

    generation = generate_historical_social_opportunities(
        character_id=character_id,
        world_seed=current["world_seed"],
        as_of=processed_through,
        behavior_policy=policy,
        known_person_ids=known_persons,
        assignments=parsed_social.archetype_assignments,
        contacts=parsed_social.contact_facts,
        post_work_contexts=parsed_social.post_work_contexts,
    )
    eligible_opps = generation.opportunities

    if parsed_facts.sleep_pressure is None:
        _fail(
            "RuntimeDecisionFacts.sleep_pressure required for social-aware decision"
        )

    social_ctx = SocialResponseContext(
        now=processed_through,
        human_state=dict(current["human_state"]),
        sleep_pressure=parsed_facts.sleep_pressure,
        available_window_min=parsed_facts.available_window_min,
        hard_commitment_blocking_now=parsed_social.hard_commitment_blocking_now,
        social_contact_physical_feasible=parsed_social.social_contact_physical_feasible,
        post_work_location_feasible=parsed_social.post_work_location_feasible,
    )
    eligible_ids = {o.opportunity_id for o in eligible_opps}
    prior_rows = [
        _response_row_dict(r)
        for r in pre_social_state.responses
        if r.opportunity_id in eligible_ids
    ]
    social_response = adapt_social_responses(
        context=social_ctx,
        behavior_policy=policy,
        known_person_ids=known_persons,
        opportunities=eligible_opps,
        availability_windows=parsed_social.availability_windows,
        prior_responses=prior_rows,
    )

    working_social = pre_social_state
    working_schedule = deepcopy(dict(schedule))
    working_current = deepcopy(dict(current))

    proposals_by_opp = {
        p.source_opportunity_id: p for p in social_response.commitment_proposals
    }

    for decision in sorted(social_response.responses, key=lambda d: d.opportunity_id):
        if decision.reason_code == PRIOR_TERMINAL_REASON:
            continue
        if decision.response in {"DECLINE", "DEFER"}:
            working_social = apply_social_response_decision(
                working_social,
                decision,
                as_of=processed_through,
            )

    invite_accepts = [
        d
        for d in social_response.responses
        if d.response == "ACCEPT"
        and d.reason_code != PRIOR_TERMINAL_REASON
        and d.opportunity_kind == "INVITE_OPPORTUNITY"
    ]
    for decision in sorted(invite_accepts, key=lambda d: d.opportunity_id):
        proposal = proposals_by_opp.get(decision.opportunity_id)
        if proposal is None:
            _fail(
                "INVITE ACCEPT missing SocialCommitmentProposal for "
                f"{decision.opportunity_id}"
            )
        social_before_insert = working_social
        try:
            working_schedule = insert_commitment(
                working_schedule,
                proposal.commitment,
                as_of=processed_through,
            )
        except LifeEngineError:
            if social_before_insert.state_hash != working_social.state_hash:
                _fail("invite insert failure must leave SocialResponseState unchanged")
            raise
        working_current = _update_current_schedule_refs(
            working_current, working_schedule
        )
        working_social = apply_social_response_decision(
            working_social,
            decision,
            as_of=processed_through,
        )

    working_bundle = validate_runtime_bundle(
        _clone_bundle_with_schedule(
            input_bundle,
            schedule_state=working_schedule,
            current_state=working_current,
        ),
        reference_sets=reference_sets,
    )

    immediate_accept_by_key: dict[str, SocialResponseDecision] = {}
    for decision in social_response.responses:
        if decision.reason_code == PRIOR_TERMINAL_REASON:
            continue
        if decision.response != "ACCEPT":
            continue
        if decision.opportunity_kind not in {
            "CONTACT_OPPORTUNITY",
            "POST_WORK_OPPORTUNITY",
        }:
            continue
        if decision.resolved_opportunity_key is None:
            _fail(
                "immediate ACCEPT missing resolved_opportunity_key for "
                f"{decision.opportunity_id}"
            )
        immediate_accept_by_key[decision.resolved_opportunity_key] = decision

    social_resolved = social_response.opportunities

    snapshot_extensions = {
        "social_response_state_pre": {
            "revision": pre_social_state.revision,
            "state_hash": pre_social_state.state_hash,
        },
        "runtime_social_facts": _normalized_social_facts_dict(
            parsed_social,
            known_person_ids=known_persons,
            as_of=processed_through,
        ),
        "response_eligible_opportunities": [
            dict(sorted(o.as_dict().items())) for o in eligible_opps
        ],
        "social_response_state_working": {
            "revision": working_social.revision,
            "state_hash": working_social.state_hash,
        },
        "schedule_state_working": {
            "revision": working_schedule["revision"],
            "state_hash": working_schedule["state_hash"],
        },
        "merged_social_opportunities": [_opportunity_dict(o) for o in social_resolved],
    }

    frame = build_runtime_decision_frame_with_extras(
        bundle=working_bundle,
        reference_sets=reference_sets,
        behavior_policy=policy,
        trigger=trigger,
        facts=parsed_facts,
        extra_opportunity_groups=(social_resolved,),
        snapshot_extensions=snapshot_extensions,
    )

    pending: PendingImmediateAccept | None = None
    selected = frame.selected_candidate
    if selected is not None and selected.action_kind == "SOCIAL_CONTACT":
        matched_opp = next(
            (
                o
                for o in social_resolved
                if o.source_kind == selected.source_kind
                and o.source_ref == selected.source_ref
                and o.action_kind == selected.action_kind
            ),
            None,
        )
        if matched_opp is not None:
            matched_decision = immediate_accept_by_key.get(matched_opp.opportunity_key)
            if matched_decision is not None:
                if matched_decision.contact_mode is None:
                    _fail(
                        "immediate ACCEPT SocialResponseDecision requires contact_mode"
                    )
                mode = matched_decision.contact_mode
                if mode == LIGHTWEIGHT_MODE:
                    pending_dmin: int | None = None
                    pending_dmax: int | None = None
                elif mode in {"CALL", "POST_WORK"}:
                    duration_block = policy["social_policy"]["contact_mode_durations"][
                        mode
                    ]
                    if not isinstance(duration_block, Mapping):
                        _fail(
                            f"social_policy.contact_mode_durations.{mode} "
                            "must be a mapping"
                        )
                    policy_dmin = duration_block["duration_min"]
                    policy_dmax = duration_block["duration_max"]
                    if (
                        isinstance(policy_dmin, bool)
                        or not isinstance(policy_dmin, int)
                        or isinstance(policy_dmax, bool)
                        or not isinstance(policy_dmax, int)
                    ):
                        _fail(
                            f"social_policy.contact_mode_durations.{mode} "
                            "must have int duration_min/max"
                        )
                    # Slice 2H ACCEPT: min=policy min, max=min(policy max, window).
                    if parsed_facts.available_window_min < policy_dmin:
                        _fail(
                            "available_window_min < policy duration_min; "
                            "immediate ACCEPT PendingImmediateAccept must not exist"
                        )
                    pending_dmin = policy_dmin
                    pending_dmax = min(
                        policy_dmax, parsed_facts.available_window_min
                    )
                    if (
                        matched_decision.duration_min != pending_dmin
                        or matched_decision.duration_max != pending_dmax
                    ):
                        _fail(
                            "matched SocialResponseDecision durations must equal "
                            "2H window-capped policy "
                            f"(decision={matched_decision.duration_min}.."
                            f"{matched_decision.duration_max}, "
                            f"expected={pending_dmin}..{pending_dmax})"
                        )
                else:
                    _fail(
                        "immediate ACCEPT PendingImmediateAccept contact_mode "
                        f"unsupported: {mode!r}"
                    )
                pending = parse_pending_immediate_accept(
                    {
                        "opportunity_id": matched_decision.opportunity_id,
                        "response_id": matched_decision.response_id,
                        "decided_at": processed_through,
                        "reason_code": matched_decision.reason_code,
                        "selected_opportunity_key": matched_opp.opportunity_key,
                        "selected_candidate_id": selected.candidate_id,
                        "selected_candidate_key": selected.candidate_key,
                        "decision_key": frame.resolution.decision_key,
                        "character_id": character_id,
                        "person_id": matched_decision.person_id,
                        "opportunity_kind": matched_decision.opportunity_kind,
                        "contact_mode": mode,
                        "duration_min": pending_dmin,
                        "duration_max": pending_dmax,
                        "selected_response_hash": build_selected_social_response_hash(
                            matched_decision
                        ),
                    }
                )

    if working_bundle.current_state["state_revision"] != state_revision_before:
        _fail("social decision must not bump state_revision")
    if canonical_json(working_bundle.relation_state) != relation_before:
        _fail("social decision must not mutate RelationState")
    open_promises_after = canonical_json(
        [
            {"person_id": p.get("person_id"), "open_promises": p.get("open_promises")}
            for p in working_bundle.relation_state.get("people", [])
        ]
    )
    if open_promises_after != open_promises_before:
        _fail("social decision must not mutate open_promises")
    if canonical_json(input_bundle.current_state["pending_queue"]) != queue_before:
        _fail("social decision must not mutate input pending_queue")
    if canonical_json(dict(behavior_policy)) != policy_before:
        _fail("social decision must not mutate behavior_policy")
    if trigger_before is not None and canonical_json(dict(trigger)) != trigger_before:
        _fail("social decision must not mutate trigger mapping")
    if facts_before is not None and canonical_json(dict(facts)) != facts_before:
        _fail("social decision must not mutate facts mapping")
    if (
        social_facts_before is not None
        and canonical_json(dict(social_facts)) != social_facts_before
    ):
        _fail("social decision must not mutate social_facts mapping")
    if (
        social_state_before is not None
        and canonical_json(dict(social_response_state)) != social_state_before
    ):
        _fail("social decision must not mutate input SocialResponseState mapping")

    return RuntimeSocialDecisionResult(
        frame=frame,
        working_bundle=working_bundle,
        social_response_state=working_social,
        social_generation=generation,
        social_response=social_response,
        pending_immediate_accept=pending,
    )
