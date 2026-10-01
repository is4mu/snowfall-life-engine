"""Life Engine v2 Slice 2G — exogenous social slot generation (pure).

Generates date-scoped weekly CONTACT/INVITE slots and post-work activations
into SocialExogenousOpportunity ("opportunity arrived" only). Does not
accept/decline/defer, create ResolvedOpportunity / Commitment / ActualEvent,
or wire into clock/queue/resolver. No file I/O, network, or global PRNG.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from .contracts import validate_social_archetype_assignment
from .dynamics import require_v2_production_format
from .errors import ErrorCode, LifeEngineError
from .ids import stable_id
from .rng import SeedLike, keyed_digest, u01
from .schema import reject_binary_floats
from .timeutil import (
    TOKYO,
    format_rfc3339,
    parse_rfc3339,
    require_canonical_timestamp,
)

# ---------------------------------------------------------------------------
# Closed sets
# ---------------------------------------------------------------------------

SLOT_KINDS = frozenset({"CONTACT", "INVITE"})
SLOT_OUTCOMES = frozenset(
    {
        "PENDING",
        "INELIGIBLE_RECENCY_UNKNOWN",
        "NO_OPPORTUNITY",
        "CONTACT_OPPORTUNITY",
        "INVITE_OPPORTUNITY",
    }
)
POST_WORK_OUTCOMES = frozenset(
    {
        "NO_OPPORTUNITY",
        "POST_WORK_OPPORTUNITY",
    }
)
OPPORTUNITY_KINDS = frozenset(
    {
        "CONTACT_OPPORTUNITY",
        "INVITE_OPPORTUNITY",
        "POST_WORK_OPPORTUNITY",
    }
)
INVITE_TIMING_KINDS = frozenset({"SAME_DAY_SOFT", "FUTURE_SOFT"})
SOURCE_KINDS = frozenset({"WEEKLY_SLOT", "POST_WORK_CONTEXT"})

SOCIAL_DIAGNOSTIC_CODES = frozenset(
    {
        "RECENCY_UNKNOWN",
        "SLOT_PENDING",
        "CONTACT_NOT_ACTIVATED",
        "INVITE_NOT_ACTIVATED",
        "POST_WORK_NOT_ACTIVATED",
        "WORK_CONTEXT_PERSON_NOT_PRESENT",
    }
)

ARCHETYPE_REMOTE = "REMOTE_CLOSE_BURSTY"
ARCHETYPE_LOCAL = "LOCAL_CLOSE_INVITER"
ARCHETYPE_WORK = "WORK_CONTEXTUAL"

RNG_SLOT_DAY = "social/slot-day"
RNG_ACTIVATION = "social/activation"
RNG_INVITE_TIMING = "social/invite-timing"
RNG_INVITE_FUTURE = "social/invite-future-day"
RNG_POST_WORK = "social/post-work-activation"

_SLOT_FACT_FIELDS = frozenset(
    {
        "slot_id",
        "person_id",
        "archetype_id",
        "slot_kind",
        "week_start_date",
        "local_date",
        "slot_anchor_at",
        "slot_index",
        "rule_ids",
    }
)
_CONTACT_FACT_FIELDS = frozenset(
    {"contact_id", "person_id", "actual_end", "finalized_at"}
)
_POST_WORK_FACT_FIELDS = frozenset(
    {"work_context_id", "actual_end", "finalized_at", "participant_person_ids"}
)
_FORBIDDEN_CONTACT_FIELDS = frozenset(
    {
        "message",
        "text",
        "topic",
        "sentiment",
        "closeness",
        "trust",
        "relationship_score",
        "friendship_score",
        "name",
    }
)
_FORBIDDEN_OUTPUT_FIELD_NAMES = frozenset(
    {
        "name",
        "message",
        "text",
        "topic",
        "friendship_score",
        "closeness",
        "trust",
    }
)


# ---------------------------------------------------------------------------
# Transient structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SocialGenerationContext:
    character_id: str
    world_seed: SeedLike
    week_start_date: str
    as_of: str


@dataclass(frozen=True)
class SocialContactFact:
    contact_id: str
    person_id: str
    actual_end: str
    finalized_at: str


@dataclass(frozen=True)
class PostWorkContextFact:
    work_context_id: str
    actual_end: str
    finalized_at: str
    participant_person_ids: tuple[str, ...]


@dataclass(frozen=True)
class SocialSlot:
    slot_id: str
    person_id: str
    archetype_id: str
    slot_kind: str
    week_start_date: str
    local_date: str
    slot_anchor_at: str
    slot_index: int
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "person_id": self.person_id,
            "archetype_id": self.archetype_id,
            "slot_kind": self.slot_kind,
            "week_start_date": self.week_start_date,
            "local_date": self.local_date,
            "slot_anchor_at": self.slot_anchor_at,
            "slot_index": self.slot_index,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class SocialSlotResolution:
    slot_id: str
    person_id: str
    archetype_id: str
    slot_kind: str
    outcome: str
    activation_threshold_permille: int | None
    activation_draw_permille: int | None
    recency_seconds: int | None
    recency_band_key: str | None
    recency_multiplier_permille: int | None
    random_key: str | None
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "person_id": self.person_id,
            "archetype_id": self.archetype_id,
            "slot_kind": self.slot_kind,
            "outcome": self.outcome,
            "activation_threshold_permille": self.activation_threshold_permille,
            "activation_draw_permille": self.activation_draw_permille,
            "recency_seconds": self.recency_seconds,
            "recency_band_key": self.recency_band_key,
            "recency_multiplier_permille": self.recency_multiplier_permille,
            "random_key": self.random_key,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class PostWorkResolution:
    work_context_id: str
    person_id: str
    archetype_id: str
    outcome: str
    activation_threshold_permille: int | None
    activation_draw_permille: int | None
    random_key: str | None
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_context_id": self.work_context_id,
            "person_id": self.person_id,
            "archetype_id": self.archetype_id,
            "outcome": self.outcome,
            "activation_threshold_permille": self.activation_threshold_permille,
            "activation_draw_permille": self.activation_draw_permille,
            "random_key": self.random_key,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class SocialExogenousOpportunity:
    opportunity_id: str
    opportunity_kind: str
    person_id: str
    archetype_id: str
    source_kind: str
    source_ref: str
    available_local_date: str
    invite_timing_kind: str | None
    target_local_date: str | None
    activation_random_key: str
    timing_random_key: str | None
    future_day_random_key: str | None
    rule_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "opportunity_kind": self.opportunity_kind,
            "person_id": self.person_id,
            "archetype_id": self.archetype_id,
            "source_kind": self.source_kind,
            "source_ref": self.source_ref,
            "available_local_date": self.available_local_date,
            "invite_timing_kind": self.invite_timing_kind,
            "target_local_date": self.target_local_date,
            "activation_random_key": self.activation_random_key,
            "timing_random_key": self.timing_random_key,
            "future_day_random_key": self.future_day_random_key,
            "rule_ids": list(self.rule_ids),
        }


@dataclass(frozen=True)
class SocialDiagnostic:
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
class SocialGenerationResult:
    slots: tuple[SocialSlot, ...]
    slot_resolutions: tuple[SocialSlotResolution, ...]
    post_work_resolutions: tuple[PostWorkResolution, ...]
    opportunities: tuple[SocialExogenousOpportunity, ...]
    diagnostics: tuple[SocialDiagnostic, ...]


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


def _dt(ts: str, *, field: str) -> datetime:
    return parse_rfc3339(ts, field=field)


def _seconds_between(later: str, earlier: str, *, later_field: str, earlier_field: str) -> int:
    """Exact integer seconds — no float / total_seconds()."""
    later_dt = _dt(later, field=later_field)
    earlier_dt = _dt(earlier, field=earlier_field)
    if later_dt.microsecond != 0 or earlier_dt.microsecond != 0:
        _fail("social recency math rejects sub-second timestamps")
    delta = later_dt - earlier_dt
    if delta.microseconds != 0:
        _fail("social recency delta has nonzero microseconds")
    return delta.days * 86400 + delta.seconds


def _parse_iso_date(value: object, *, label: str) -> date:
    text = _require_nonempty_str(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE, f"{label} must be YYYY-MM-DD: {text}"
        ) from exc


def _require_seed_like(value: object) -> SeedLike:
    """Strict SeedLike: non-empty str | non-empty bytes | non-bool int only."""
    if isinstance(value, bool):
        _fail("world_seed must not be bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value == "":
            _fail("world_seed must be non-empty str")
        return value
    if isinstance(value, bytes):
        if value == b"":
            _fail("world_seed must be non-empty bytes")
        return value
    _fail(
        "world_seed must be str|bytes|int "
        f"(got {type(value).__name__})"
    )
    raise AssertionError("unreachable")


def _require_monday_week_start(value: object) -> str:
    """Exact lexical YYYY-MM-DD Monday (must equal date.isoformat())."""
    text = _require_nonempty_str(value, "week_start_date")
    try:
        d = date.fromisoformat(text)
    except ValueError as exc:
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            f"week_start_date must be YYYY-MM-DD: {text}",
        ) from exc
    canonical = d.isoformat()
    if text != canonical:
        _fail(
            f"week_start_date must be exact lexical YYYY-MM-DD "
            f"(got {text!r}, expected {canonical!r})"
        )
    if d.weekday() != 0:
        _fail(f"week_start_date must be Monday Asia/Tokyo: {canonical}")
    return canonical


def _require_as_of_in_week(*, week_start_date: str, as_of: str) -> None:
    """Fail closed if as_of is before week_start T00:00:00+09:00."""
    week_anchor = _slot_anchor_at(week_start_date)
    require_canonical_timestamp(week_anchor, field="week_start_anchor")
    if (
        _seconds_between(
            as_of,
            week_anchor,
            later_field="as_of",
            earlier_field="week_start_anchor",
        )
        < 0
    ):
        _fail(
            f"as_of must be >= week_start T00:00:00+09:00 "
            f"(as_of={as_of}, week_start={week_start_date})"
        )


def _slot_anchor_at(local_date: str) -> str:
    return f"{local_date}T00:00:00+09:00"


def _tokyo_local_date(ts: str, *, field: str) -> str:
    return _dt(ts, field=field).astimezone(TOKYO).date().isoformat()


def _permille_draw(
    world_seed: SeedLike,
    namespace: str,
    entity_id: str,
    decision_type: str,
    decision_instance: str,
) -> tuple[int, str]:
    """Return (draw in 0..999, random_key hex)."""
    digest = keyed_digest(
        world_seed, namespace, entity_id, decision_type, decision_instance
    )
    raw = u01(world_seed, namespace, entity_id, decision_type, decision_instance)
    draw = (raw * 1000) // 1_000_000_000
    if draw >= 1000:
        draw = 999
    return draw, digest.hex()


def _inclusive_offset_draw(
    world_seed: SeedLike,
    namespace: str,
    entity_id: str,
    decision_type: str,
    decision_instance: str,
    *,
    min_inclusive: int,
    max_inclusive: int,
) -> tuple[int, str]:
    if min_inclusive > max_inclusive:
        _fail("invite future horizon min > max")
    span = max_inclusive - min_inclusive + 1
    digest = keyed_digest(
        world_seed, namespace, entity_id, decision_type, decision_instance
    )
    raw = u01(world_seed, namespace, entity_id, decision_type, decision_instance)
    offset = min_inclusive + (raw * span) // 1_000_000_000
    if offset > max_inclusive:
        offset = max_inclusive
    return offset, digest.hex()


def _recency_band_key(band: Mapping[str, Any]) -> str:
    lo = int(band["min_hours_inclusive"])
    hi = band["max_hours_exclusive"]
    if hi is None:
        return f"{lo}-inf"
    return f"{lo}-{int(hi)}"


def _match_recency_band(
    recency_seconds: int, bands: Sequence[Mapping[str, Any]]
) -> tuple[str, int]:
    for band in bands:
        lo_h = int(band["min_hours_inclusive"])
        hi = band["max_hours_exclusive"]
        lo_s = lo_h * 3600
        if recency_seconds < lo_s:
            continue
        if hi is None:
            return _recency_band_key(band), int(band["multiplier_permille"])
        hi_s = int(hi) * 3600
        if recency_seconds < hi_s:
            return _recency_band_key(band), int(band["multiplier_permille"])
    _fail(f"no recency band matched for seconds={recency_seconds}")
    raise AssertionError("unreachable")


def _contact_threshold(
    *,
    base_activation_permille: int,
    multiplier_permille: int,
    activation_cap_permille: int,
) -> int:
    scaled = (base_activation_permille * multiplier_permille) // 1000
    return min(activation_cap_permille, scaled)


def _make_diagnostic(
    *,
    source_kind: str,
    source_ref: str,
    code: str,
    rule_ids: Sequence[str] = (),
) -> SocialDiagnostic:
    if code not in SOCIAL_DIAGNOSTIC_CODES:
        _fail(f"unknown SocialDiagnostic code: {code}")
    return SocialDiagnostic(
        source_kind=_require_nonempty_str(source_kind, "source_kind"),
        source_ref=_require_nonempty_str(source_ref, "source_ref"),
        code=code,
        rule_ids=_canonical_sorted_strs(rule_ids),
    )


def _assert_no_forbidden_keys(data: Mapping[str, Any], *, label: str) -> None:
    bad = sorted(set(data.keys()) & _FORBIDDEN_OUTPUT_FIELD_NAMES)
    if bad:
        _fail(f"{label} forbids fields: {bad}")
    for key in _FORBIDDEN_CONTACT_FIELDS:
        if key in data:
            _fail(f"{label} forbids field: {key}")


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def parse_social_generation_context(
    data: SocialGenerationContext | Mapping[str, Any],
) -> SocialGenerationContext:
    if isinstance(data, SocialGenerationContext):
        ctx = data
        # Validate seed before float-scan so float seeds fail with seed error.
        world_seed = _require_seed_like(ctx.world_seed)
        reject_binary_floats(
            {
                "character_id": ctx.character_id,
                "week_start_date": ctx.week_start_date,
                "as_of": ctx.as_of,
            }
        )
        character_id = _require_nonempty_str(ctx.character_id, "character_id")
        week_start = _require_monday_week_start(ctx.week_start_date)
        as_of = require_canonical_timestamp(ctx.as_of, field="as_of")
        _require_as_of_in_week(week_start_date=week_start, as_of=as_of)
        return SocialGenerationContext(
            character_id=character_id,
            world_seed=world_seed,
            week_start_date=week_start,
            as_of=as_of,
        )
    reject_binary_floats(dict(data))
    allowed = {"character_id", "world_seed", "week_start_date", "as_of"}
    unknown = sorted(set(data.keys()) - allowed)
    if unknown:
        _fail(f"SocialGenerationContext unknown fields: {unknown}")
    world_seed = _require_seed_like(data.get("world_seed"))
    character_id = _require_nonempty_str(data.get("character_id"), "character_id")
    week_start = _require_monday_week_start(data.get("week_start_date"))
    as_of = require_canonical_timestamp(data.get("as_of"), field="as_of")
    _require_as_of_in_week(week_start_date=week_start, as_of=as_of)
    return SocialGenerationContext(
        character_id=character_id,
        world_seed=world_seed,
        week_start_date=week_start,
        as_of=as_of,
    )


def parse_known_person_ids(values: Sequence[str]) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        _fail("known_person_ids must be a sequence")
    out: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(values):
        pid = _require_nonempty_str(raw, f"known_person_ids[{i}]")
        if pid in seen:
            _fail(f"duplicate known_person_id: {pid}")
        seen.add(pid)
        out.append(pid)
    if not out:
        _fail("known_person_ids must be non-empty")
    return tuple(sorted(out))


def parse_social_contact_fact(
    data: SocialContactFact | Mapping[str, Any],
    *,
    known: set[str],
    as_of: str,
) -> SocialContactFact:
    if isinstance(data, SocialContactFact):
        payload = {
            "contact_id": data.contact_id,
            "person_id": data.person_id,
            "actual_end": data.actual_end,
            "finalized_at": data.finalized_at,
        }
    else:
        payload = dict(data)
    reject_binary_floats(payload)
    _assert_no_forbidden_keys(payload, label="SocialContactFact")
    unknown = sorted(set(payload.keys()) - _CONTACT_FACT_FIELDS)
    if unknown:
        _fail(f"SocialContactFact unknown fields: {unknown}")
    contact_id = _require_nonempty_str(payload.get("contact_id"), "contact_id")
    person_id = _require_nonempty_str(payload.get("person_id"), "person_id")
    if person_id not in known:
        _fail(f"unknown contact person_id: {person_id}")
    actual_end = require_canonical_timestamp(payload.get("actual_end"), field="actual_end")
    finalized_at = require_canonical_timestamp(
        payload.get("finalized_at"), field="finalized_at"
    )
    if _seconds_between(
        finalized_at, actual_end, later_field="finalized_at", earlier_field="actual_end"
    ) < 0:
        _fail("SocialContactFact actual_end after finalized_at")
    if _seconds_between(
        as_of, finalized_at, later_field="as_of", earlier_field="finalized_at"
    ) < 0:
        _fail("SocialContactFact finalized_at after as_of")
    return SocialContactFact(
        contact_id=contact_id,
        person_id=person_id,
        actual_end=actual_end,
        finalized_at=finalized_at,
    )


def parse_post_work_context_fact(
    data: PostWorkContextFact | Mapping[str, Any],
    *,
    known: set[str],
    as_of: str,
) -> PostWorkContextFact:
    if isinstance(data, PostWorkContextFact):
        payload = {
            "work_context_id": data.work_context_id,
            "actual_end": data.actual_end,
            "finalized_at": data.finalized_at,
            "participant_person_ids": list(data.participant_person_ids),
        }
    else:
        payload = dict(data)
    reject_binary_floats(payload)
    _assert_no_forbidden_keys(payload, label="PostWorkContextFact")
    unknown = sorted(set(payload.keys()) - _POST_WORK_FACT_FIELDS)
    if unknown:
        _fail(f"PostWorkContextFact unknown fields: {unknown}")
    work_context_id = _require_nonempty_str(
        payload.get("work_context_id"), "work_context_id"
    )
    actual_end = require_canonical_timestamp(payload.get("actual_end"), field="actual_end")
    finalized_at = require_canonical_timestamp(
        payload.get("finalized_at"), field="finalized_at"
    )
    if _seconds_between(
        finalized_at, actual_end, later_field="finalized_at", earlier_field="actual_end"
    ) < 0:
        _fail("PostWorkContextFact actual_end after finalized_at")
    if _seconds_between(
        as_of, actual_end, later_field="as_of", earlier_field="actual_end"
    ) < 0:
        _fail("PostWorkContextFact actual_end after as_of")
    if _seconds_between(
        as_of, finalized_at, later_field="as_of", earlier_field="finalized_at"
    ) < 0:
        _fail("PostWorkContextFact finalized_at after as_of")
    raw_parts = payload.get("participant_person_ids")
    if not isinstance(raw_parts, (list, tuple)):
        _fail("participant_person_ids must be a sequence")
    parts: list[str] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_parts):
        pid = _require_nonempty_str(raw, f"participant_person_ids[{i}]")
        if pid not in known:
            _fail(f"unknown post-work participant: {pid}")
        if pid in seen:
            _fail(f"duplicate participant_person_id: {pid}")
        seen.add(pid)
        parts.append(pid)
    return PostWorkContextFact(
        work_context_id=work_context_id,
        actual_end=actual_end,
        finalized_at=finalized_at,
        participant_person_ids=tuple(sorted(parts)),
    )


def _parse_assignment(
    data: Mapping[str, Any],
    *,
    known: set[str],
    archetypes: Mapping[str, Any],
) -> dict[str, Any]:
    reject_binary_floats(dict(data))
    obj = validate_social_archetype_assignment(data)
    person_id = obj["person_id"]
    if person_id not in known:
        _fail(f"unknown assignment person_id: {person_id}")
    archetype_id = obj["archetype_id"]
    if archetype_id not in archetypes:
        _fail(f"archetype not in policy: {archetype_id}")
    return obj


# ---------------------------------------------------------------------------
# Slot generation
# ---------------------------------------------------------------------------


def _rank_weekdays(
    *,
    world_seed: SeedLike,
    person_id: str,
    archetype_id: str,
    slot_kind: str,
    week_start_date: str,
) -> list[int]:
    ranked: list[tuple[bytes, int]] = []
    for weekday in range(7):
        digest = keyed_digest(
            world_seed,
            RNG_SLOT_DAY,
            person_id,
            f"{archetype_id}:{slot_kind}",
            f"{week_start_date}:weekday:{weekday}",
        )
        ranked.append((digest, weekday))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [w for _, w in ranked]


def _build_slots_for_kind(
    *,
    world_seed: SeedLike,
    person_id: str,
    archetype_id: str,
    slot_kind: str,
    week_start: date,
    week_start_date: str,
    count: int,
) -> list[SocialSlot]:
    if count > 7:
        _fail(f"slots_per_week > 7 forbidden: {archetype_id}:{slot_kind}={count}")
    if count < 0:
        _fail(f"slots_per_week must be >= 0: {archetype_id}:{slot_kind}")
    if count == 0:
        return []
    weekdays = _rank_weekdays(
        world_seed=world_seed,
        person_id=person_id,
        archetype_id=archetype_id,
        slot_kind=slot_kind,
        week_start_date=week_start_date,
    )[:count]
    slots: list[SocialSlot] = []
    seen_dates: set[str] = set()
    for slot_index, weekday in enumerate(weekdays):
        local = (week_start + timedelta(days=weekday)).isoformat()
        if local in seen_dates:
            _fail(f"duplicate local_date within person+slot_kind: {person_id}:{slot_kind}:{local}")
        seen_dates.add(local)
        anchor = _slot_anchor_at(local)
        require_canonical_timestamp(anchor, field="slot_anchor_at")
        rule_ids = _canonical_sorted_strs(
            [
                f"slice2g.slot.{archetype_id}.{slot_kind}",
                f"slice2g.slot_index.{slot_index}",
            ]
        )
        slot_id = stable_id(
            "social-slot",
            person_id,
            archetype_id,
            slot_kind,
            week_start_date,
            slot_index,
            local,
        )
        slots.append(
            SocialSlot(
                slot_id=slot_id,
                person_id=person_id,
                archetype_id=archetype_id,
                slot_kind=slot_kind,
                week_start_date=week_start_date,
                local_date=local,
                slot_anchor_at=anchor,
                slot_index=slot_index,
                rule_ids=rule_ids,
            )
        )
    return slots


def _generate_weekly_slots(
    *,
    world_seed: SeedLike,
    week_start_date: str,
    assignments: Sequence[Mapping[str, Any]],
    social_policy: Mapping[str, Any],
) -> list[SocialSlot]:
    week_start = date.fromisoformat(week_start_date)
    archetypes = social_policy["archetypes"]
    slots: list[SocialSlot] = []
    for asn in assignments:
        person_id = asn["person_id"]
        archetype_id = asn["archetype_id"]
        arch = archetypes[archetype_id]
        if archetype_id == ARCHETYPE_REMOTE:
            n = _require_int_ge0(arch["slots_per_week"], "REMOTE slots_per_week")
            slots.extend(
                _build_slots_for_kind(
                    world_seed=world_seed,
                    person_id=person_id,
                    archetype_id=archetype_id,
                    slot_kind="CONTACT",
                    week_start=week_start,
                    week_start_date=week_start_date,
                    count=n,
                )
            )
        elif archetype_id == ARCHETYPE_LOCAL:
            n_contact = _require_int_ge0(arch["slots_per_week"], "LOCAL slots_per_week")
            invite = arch["invite"]
            n_invite = _require_int_ge0(invite["slots_per_week"], "LOCAL invite.slots_per_week")
            slots.extend(
                _build_slots_for_kind(
                    world_seed=world_seed,
                    person_id=person_id,
                    archetype_id=archetype_id,
                    slot_kind="CONTACT",
                    week_start=week_start,
                    week_start_date=week_start_date,
                    count=n_contact,
                )
            )
            slots.extend(
                _build_slots_for_kind(
                    world_seed=world_seed,
                    person_id=person_id,
                    archetype_id=archetype_id,
                    slot_kind="INVITE",
                    week_start=week_start,
                    week_start_date=week_start_date,
                    count=n_invite,
                )
            )
        elif archetype_id == ARCHETYPE_WORK:
            free = _require_int_ge0(
                arch["free_running_slots_per_week"], "free_running_slots_per_week"
            )
            if free != 0:
                _fail("WORK_CONTEXTUAL free_running_slots_per_week must be 0")
        else:
            _fail(f"unsupported archetype_id: {archetype_id}")
    return slots


def _sort_slots(slots: Sequence[SocialSlot]) -> tuple[SocialSlot, ...]:
    return tuple(
        sorted(
            slots,
            key=lambda s: (
                s.slot_anchor_at,
                s.person_id,
                s.slot_kind,
                s.slot_index,
                s.slot_id,
            ),
        )
    )


def _sort_resolutions(
    items: Sequence[SocialSlotResolution],
) -> tuple[SocialSlotResolution, ...]:
    return tuple(
        sorted(
            items,
            key=lambda r: (
                r.slot_id,
                r.person_id,
                r.slot_kind,
                r.outcome,
            ),
        )
    )


def _sort_post_work(
    items: Sequence[PostWorkResolution],
) -> tuple[PostWorkResolution, ...]:
    return tuple(
        sorted(
            items,
            key=lambda r: (r.work_context_id, r.person_id, r.outcome),
        )
    )


def _sort_opportunities(
    items: Sequence[SocialExogenousOpportunity],
) -> tuple[SocialExogenousOpportunity, ...]:
    return tuple(
        sorted(
            items,
            key=lambda o: (
                o.available_local_date,
                o.person_id,
                o.opportunity_kind,
                o.source_ref,
                o.opportunity_id,
            ),
        )
    )


def _sort_diagnostics(
    items: Sequence[SocialDiagnostic],
) -> tuple[SocialDiagnostic, ...]:
    return tuple(
        sorted(
            items,
            key=lambda d: (d.source_kind, d.source_ref, d.code, d.rule_ids),
        )
    )


# ---------------------------------------------------------------------------
# Contact history / activation
# ---------------------------------------------------------------------------


def _latest_visible_contact_end(
    contacts: Sequence[SocialContactFact],
    *,
    person_id: str,
    slot_anchor_at: str,
) -> str | None:
    """Latest actual_end visible at slot_anchor_at (finalized_at and actual_end <= anchor)."""
    latest: str | None = None
    # Smaller age-from-anchor ⇒ later actual_end.
    best_age: int | None = None
    for c in contacts:
        if c.person_id != person_id:
            continue
        if (
            _seconds_between(
                slot_anchor_at,
                c.finalized_at,
                later_field="slot_anchor_at",
                earlier_field="finalized_at",
            )
            < 0
        ):
            continue
        if (
            _seconds_between(
                slot_anchor_at,
                c.actual_end,
                later_field="slot_anchor_at",
                earlier_field="actual_end",
            )
            < 0
        ):
            continue
        age = _seconds_between(
            slot_anchor_at,
            c.actual_end,
            later_field="slot_anchor_at",
            earlier_field="actual_end",
        )
        if best_age is None or age < best_age or (
            age == best_age and latest is not None and c.actual_end > latest
        ):
            latest = c.actual_end
            best_age = age
    return latest


def _resolve_contact_slot(
    *,
    slot: SocialSlot,
    ctx: SocialGenerationContext,
    arch_policy: Mapping[str, Any],
    contacts: Sequence[SocialContactFact],
    diagnostics: list[SocialDiagnostic],
) -> tuple[SocialSlotResolution, SocialExogenousOpportunity | None]:
    as_of_dt = _dt(ctx.as_of, field="as_of")
    anchor_dt = _dt(slot.slot_anchor_at, field="slot_anchor_at")
    if anchor_dt > as_of_dt:
        diagnostics.append(
            _make_diagnostic(
                source_kind="WEEKLY_SLOT",
                source_ref=slot.slot_id,
                code="SLOT_PENDING",
                rule_ids=slot.rule_ids,
            )
        )
        return (
            SocialSlotResolution(
                slot_id=slot.slot_id,
                person_id=slot.person_id,
                archetype_id=slot.archetype_id,
                slot_kind=slot.slot_kind,
                outcome="PENDING",
                activation_threshold_permille=None,
                activation_draw_permille=None,
                recency_seconds=None,
                recency_band_key=None,
                recency_multiplier_permille=None,
                random_key=None,
                rule_ids=slot.rule_ids,
            ),
            None,
        )

    latest_end = _latest_visible_contact_end(
        contacts, person_id=slot.person_id, slot_anchor_at=slot.slot_anchor_at
    )
    if latest_end is None:
        diagnostics.append(
            _make_diagnostic(
                source_kind="WEEKLY_SLOT",
                source_ref=slot.slot_id,
                code="RECENCY_UNKNOWN",
                rule_ids=slot.rule_ids,
            )
        )
        return (
            SocialSlotResolution(
                slot_id=slot.slot_id,
                person_id=slot.person_id,
                archetype_id=slot.archetype_id,
                slot_kind=slot.slot_kind,
                outcome="INELIGIBLE_RECENCY_UNKNOWN",
                activation_threshold_permille=None,
                activation_draw_permille=None,
                recency_seconds=None,
                recency_band_key=None,
                recency_multiplier_permille=None,
                random_key=None,
                rule_ids=slot.rule_ids,
            ),
            None,
        )

    recency_seconds = _seconds_between(
        slot.slot_anchor_at,
        latest_end,
        later_field="slot_anchor_at",
        earlier_field="actual_end",
    )
    if recency_seconds < 0:
        _fail("visible contact actual_end after slot_anchor_at")
    band_key, multiplier = _match_recency_band(
        recency_seconds, arch_policy["recency_multipliers"]
    )
    threshold = _contact_threshold(
        base_activation_permille=int(arch_policy["base_activation_permille"]),
        multiplier_permille=multiplier,
        activation_cap_permille=int(arch_policy["activation_cap_permille"]),
    )
    draw, random_key = _permille_draw(
        ctx.world_seed,
        RNG_ACTIVATION,
        slot.person_id,
        slot.slot_kind,
        slot.slot_id,
    )
    rule_ids = _canonical_sorted_strs(
        list(slot.rule_ids)
        + [
            f"slice2g.activation.{slot.slot_kind}",
            f"slice2g.recency.{band_key}",
            f"slice2g.archetype.{slot.archetype_id}",
        ]
    )
    if draw < threshold:
        opp = SocialExogenousOpportunity(
            opportunity_id=stable_id(
                "social-opportunity", slot.slot_id, "CONTACT_OPPORTUNITY"
            ),
            opportunity_kind="CONTACT_OPPORTUNITY",
            person_id=slot.person_id,
            archetype_id=slot.archetype_id,
            source_kind="WEEKLY_SLOT",
            source_ref=slot.slot_id,
            available_local_date=slot.local_date,
            invite_timing_kind=None,
            target_local_date=None,
            activation_random_key=random_key,
            timing_random_key=None,
            future_day_random_key=None,
            rule_ids=rule_ids,
        )
        return (
            SocialSlotResolution(
                slot_id=slot.slot_id,
                person_id=slot.person_id,
                archetype_id=slot.archetype_id,
                slot_kind=slot.slot_kind,
                outcome="CONTACT_OPPORTUNITY",
                activation_threshold_permille=threshold,
                activation_draw_permille=draw,
                recency_seconds=recency_seconds,
                recency_band_key=band_key,
                recency_multiplier_permille=multiplier,
                random_key=random_key,
                rule_ids=rule_ids,
            ),
            opp,
        )

    diagnostics.append(
        _make_diagnostic(
            source_kind="WEEKLY_SLOT",
            source_ref=slot.slot_id,
            code="CONTACT_NOT_ACTIVATED",
            rule_ids=rule_ids,
        )
    )
    return (
        SocialSlotResolution(
            slot_id=slot.slot_id,
            person_id=slot.person_id,
            archetype_id=slot.archetype_id,
            slot_kind=slot.slot_kind,
            outcome="NO_OPPORTUNITY",
            activation_threshold_permille=threshold,
            activation_draw_permille=draw,
            recency_seconds=recency_seconds,
            recency_band_key=band_key,
            recency_multiplier_permille=multiplier,
            random_key=random_key,
            rule_ids=rule_ids,
        ),
        None,
    )


def _resolve_invite_slot(
    *,
    slot: SocialSlot,
    ctx: SocialGenerationContext,
    arch_policy: Mapping[str, Any],
    diagnostics: list[SocialDiagnostic],
) -> tuple[SocialSlotResolution, SocialExogenousOpportunity | None]:
    as_of_dt = _dt(ctx.as_of, field="as_of")
    anchor_dt = _dt(slot.slot_anchor_at, field="slot_anchor_at")
    if anchor_dt > as_of_dt:
        diagnostics.append(
            _make_diagnostic(
                source_kind="WEEKLY_SLOT",
                source_ref=slot.slot_id,
                code="SLOT_PENDING",
                rule_ids=slot.rule_ids,
            )
        )
        return (
            SocialSlotResolution(
                slot_id=slot.slot_id,
                person_id=slot.person_id,
                archetype_id=slot.archetype_id,
                slot_kind=slot.slot_kind,
                outcome="PENDING",
                activation_threshold_permille=None,
                activation_draw_permille=None,
                recency_seconds=None,
                recency_band_key=None,
                recency_multiplier_permille=None,
                random_key=None,
                rule_ids=slot.rule_ids,
            ),
            None,
        )

    invite = arch_policy["invite"]
    same_day = int(invite["same_day_split_permille"])
    future = int(invite["future_split_permille"])
    if same_day + future != 1000:
        _fail("invite same_day_split + future_split must equal 1000")
    min_days = int(invite["future_horizon_min_days"])
    max_days = int(invite["future_horizon_max_days"])
    if min_days > max_days:
        _fail("invite future_horizon_min_days > max_days")
    threshold = int(invite["base_activation_permille"])
    draw, random_key = _permille_draw(
        ctx.world_seed,
        RNG_ACTIVATION,
        slot.person_id,
        slot.slot_kind,
        slot.slot_id,
    )
    base_rules = _canonical_sorted_strs(
        list(slot.rule_ids)
        + [
            f"slice2g.activation.{slot.slot_kind}",
            f"slice2g.archetype.{slot.archetype_id}",
        ]
    )
    if draw >= threshold:
        diagnostics.append(
            _make_diagnostic(
                source_kind="WEEKLY_SLOT",
                source_ref=slot.slot_id,
                code="INVITE_NOT_ACTIVATED",
                rule_ids=base_rules,
            )
        )
        return (
            SocialSlotResolution(
                slot_id=slot.slot_id,
                person_id=slot.person_id,
                archetype_id=slot.archetype_id,
                slot_kind=slot.slot_kind,
                outcome="NO_OPPORTUNITY",
                activation_threshold_permille=threshold,
                activation_draw_permille=draw,
                recency_seconds=None,
                recency_band_key=None,
                recency_multiplier_permille=None,
                random_key=random_key,
                rule_ids=base_rules,
            ),
            None,
        )

    timing_draw, timing_key = _permille_draw(
        ctx.world_seed,
        RNG_INVITE_TIMING,
        slot.person_id,
        slot.slot_kind,
        slot.slot_id,
    )
    future_day_key: str | None = None
    if timing_draw < same_day:
        timing_kind = "SAME_DAY_SOFT"
        target = slot.local_date
    else:
        timing_kind = "FUTURE_SOFT"
        offset, future_day_key = _inclusive_offset_draw(
            ctx.world_seed,
            RNG_INVITE_FUTURE,
            slot.person_id,
            slot.slot_kind,
            slot.slot_id,
            min_inclusive=min_days,
            max_inclusive=max_days,
        )
        target = (
            date.fromisoformat(slot.local_date) + timedelta(days=offset)
        ).isoformat()

    rule_ids = _canonical_sorted_strs(
        list(base_rules) + [f"slice2g.invite.{timing_kind}"]
    )
    opp = SocialExogenousOpportunity(
        opportunity_id=stable_id(
            "social-opportunity", slot.slot_id, "INVITE_OPPORTUNITY"
        ),
        opportunity_kind="INVITE_OPPORTUNITY",
        person_id=slot.person_id,
        archetype_id=slot.archetype_id,
        source_kind="WEEKLY_SLOT",
        source_ref=slot.slot_id,
        available_local_date=slot.local_date,
        invite_timing_kind=timing_kind,
        target_local_date=target,
        activation_random_key=random_key,
        timing_random_key=timing_key,
        future_day_random_key=future_day_key,
        rule_ids=rule_ids,
    )
    return (
        SocialSlotResolution(
            slot_id=slot.slot_id,
            person_id=slot.person_id,
            archetype_id=slot.archetype_id,
            slot_kind=slot.slot_kind,
            outcome="INVITE_OPPORTUNITY",
            activation_threshold_permille=threshold,
            activation_draw_permille=draw,
            recency_seconds=None,
            recency_band_key=None,
            recency_multiplier_permille=None,
            random_key=random_key,
            rule_ids=rule_ids,
        ),
        opp,
    )


# ---------------------------------------------------------------------------
# Post-work
# ---------------------------------------------------------------------------


def _resolve_post_work(
    *,
    ctx: SocialGenerationContext,
    work: PostWorkContextFact,
    person_id: str,
    arch_policy: Mapping[str, Any],
    diagnostics: list[SocialDiagnostic],
) -> tuple[PostWorkResolution | None, SocialExogenousOpportunity | None]:
    if person_id not in work.participant_person_ids:
        diagnostics.append(
            _make_diagnostic(
                source_kind="POST_WORK_CONTEXT",
                source_ref=work.work_context_id,
                code="WORK_CONTEXT_PERSON_NOT_PRESENT",
                rule_ids=[f"slice2g.post_work.{ARCHETYPE_WORK}", person_id],
            )
        )
        return None, None

    threshold = int(arch_policy["post_work_activation_permille"])
    decision_instance = f"{person_id}\0{work.work_context_id}"
    draw, random_key = _permille_draw(
        ctx.world_seed,
        RNG_POST_WORK,
        person_id,
        "POST_WORK",
        decision_instance,
    )
    rule_ids = _canonical_sorted_strs(
        [
            f"slice2g.post_work.{ARCHETYPE_WORK}",
            "slice2g.activation.POST_WORK",
        ]
    )
    if draw >= threshold:
        diagnostics.append(
            _make_diagnostic(
                source_kind="POST_WORK_CONTEXT",
                source_ref=work.work_context_id,
                code="POST_WORK_NOT_ACTIVATED",
                rule_ids=rule_ids,
            )
        )
        return (
            PostWorkResolution(
                work_context_id=work.work_context_id,
                person_id=person_id,
                archetype_id=ARCHETYPE_WORK,
                outcome="NO_OPPORTUNITY",
                activation_threshold_permille=threshold,
                activation_draw_permille=draw,
                random_key=random_key,
                rule_ids=rule_ids,
            ),
            None,
        )

    local_date = _tokyo_local_date(work.actual_end, field="actual_end")
    opp = SocialExogenousOpportunity(
        opportunity_id=stable_id(
            "social-opportunity",
            work.work_context_id,
            person_id,
            "POST_WORK_OPPORTUNITY",
        ),
        opportunity_kind="POST_WORK_OPPORTUNITY",
        person_id=person_id,
        archetype_id=ARCHETYPE_WORK,
        source_kind="POST_WORK_CONTEXT",
        source_ref=work.work_context_id,
        available_local_date=local_date,
        invite_timing_kind=None,
        target_local_date=local_date,
        activation_random_key=random_key,
        timing_random_key=None,
        future_day_random_key=None,
        rule_ids=rule_ids,
    )
    return (
        PostWorkResolution(
            work_context_id=work.work_context_id,
            person_id=person_id,
            archetype_id=ARCHETYPE_WORK,
            outcome="POST_WORK_OPPORTUNITY",
            activation_threshold_permille=threshold,
            activation_draw_permille=draw,
            random_key=random_key,
            rule_ids=rule_ids,
        ),
        opp,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_social_opportunities(
    *,
    context: SocialGenerationContext | Mapping[str, Any],
    behavior_policy: Mapping[str, Any],
    known_person_ids: Sequence[str],
    assignments: Sequence[Mapping[str, Any]],
    contacts: Sequence[SocialContactFact | Mapping[str, Any]] = (),
    post_work_contexts: Sequence[PostWorkContextFact | Mapping[str, Any]] = (),
) -> SocialGenerationResult:
    """Pure exogenous social opportunity generation for one week / as_of.

    Input-order independent. Does not mutate inputs. Does not call
    resolver/clock or invent people/messages/acceptance.
    """
    # Snapshot inputs for mutation checks by callers; we never write back.
    ctx = parse_social_generation_context(context)
    policy = require_v2_production_format(behavior_policy)
    social_policy = policy["social_policy"]
    archetypes = social_policy["archetypes"]
    known = set(parse_known_person_ids(known_person_ids))

    validated_assignments: list[dict[str, Any]] = []
    seen_persons: set[str] = set()
    for raw in assignments:
        asn = _parse_assignment(raw, known=known, archetypes=archetypes)
        pid = asn["person_id"]
        if pid in seen_persons:
            _fail(f"duplicate SocialArchetypeAssignment person_id: {pid}")
        seen_persons.add(pid)
        validated_assignments.append(asn)
    validated_assignments.sort(key=lambda a: a["person_id"])

    validated_contacts = [
        parse_social_contact_fact(c, known=known, as_of=ctx.as_of) for c in contacts
    ]
    contact_ids = [c.contact_id for c in validated_contacts]
    if len(contact_ids) != len(set(contact_ids)):
        _fail(f"duplicate contact_id: {sorted(contact_ids)}")
    validated_contacts.sort(key=lambda c: c.contact_id)

    validated_work = [
        parse_post_work_context_fact(w, known=known, as_of=ctx.as_of)
        for w in post_work_contexts
    ]
    work_ids = [w.work_context_id for w in validated_work]
    if len(work_ids) != len(set(work_ids)):
        _fail(f"duplicate work_context_id: {sorted(work_ids)}")
    validated_work.sort(key=lambda w: w.work_context_id)

    slots = _generate_weekly_slots(
        world_seed=ctx.world_seed,
        week_start_date=ctx.week_start_date,
        assignments=validated_assignments,
        social_policy=social_policy,
    )
    slots_sorted = list(_sort_slots(slots))

    diagnostics: list[SocialDiagnostic] = []
    resolutions: list[SocialSlotResolution] = []
    opportunities: list[SocialExogenousOpportunity] = []

    for slot in slots_sorted:
        arch = archetypes[slot.archetype_id]
        if slot.slot_kind == "CONTACT":
            resolution, opp = _resolve_contact_slot(
                slot=slot,
                ctx=ctx,
                arch_policy=arch,
                contacts=validated_contacts,
                diagnostics=diagnostics,
            )
        elif slot.slot_kind == "INVITE":
            resolution, opp = _resolve_invite_slot(
                slot=slot,
                ctx=ctx,
                arch_policy=arch,
                diagnostics=diagnostics,
            )
        else:
            _fail(f"unknown slot_kind: {slot.slot_kind}")
        resolutions.append(resolution)
        if opp is not None:
            opportunities.append(opp)

    post_resolutions: list[PostWorkResolution] = []
    work_persons = [
        a["person_id"]
        for a in validated_assignments
        if a["archetype_id"] == ARCHETYPE_WORK
    ]
    for work in validated_work:
        for person_id in work_persons:
            arch = archetypes[ARCHETYPE_WORK]
            resolution, opp = _resolve_post_work(
                ctx=ctx,
                work=work,
                person_id=person_id,
                arch_policy=arch,
                diagnostics=diagnostics,
            )
            if resolution is not None:
                post_resolutions.append(resolution)
            if opp is not None:
                opportunities.append(opp)

    # Guard: no forbidden output field names on as_dict payloads.
    for collection in (slots_sorted, resolutions, post_resolutions, opportunities):
        for item in collection:
            payload = item.as_dict()  # type: ignore[attr-defined]
            bad = sorted(set(payload.keys()) & _FORBIDDEN_OUTPUT_FIELD_NAMES)
            if bad:
                _fail(f"forbidden output fields present: {bad}")

    return SocialGenerationResult(
        slots=_sort_slots(slots_sorted),
        slot_resolutions=_sort_resolutions(resolutions),
        post_work_resolutions=_sort_post_work(post_resolutions),
        opportunities=_sort_opportunities(opportunities),
        diagnostics=_sort_diagnostics(diagnostics),
    )


# Expose helpers useful for tests / soaks without widening public surface much.
def recency_seconds_at_anchor(*, slot_anchor_at: str, actual_end: str) -> int:
    return _seconds_between(
        slot_anchor_at,
        actual_end,
        later_field="slot_anchor_at",
        earlier_field="actual_end",
    )


def contact_activation_threshold(
    *,
    base_activation_permille: int,
    multiplier_permille: int,
    activation_cap_permille: int,
) -> int:
    return _contact_threshold(
        base_activation_permille=base_activation_permille,
        multiplier_permille=multiplier_permille,
        activation_cap_permille=activation_cap_permille,
    )
