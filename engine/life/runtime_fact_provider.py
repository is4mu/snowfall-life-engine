"""Slice 5D2A1 — pure on-demand runtime fact-provider contracts.

The provider is a call-local delivery boundary only. Production authority for
facts/configuration is defined by Slice 5D2A2; this module intentionally owns no
production-character values, file I/O, network access, Git access, or wall-clock defaults.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import json
from typing import Any, Mapping, Protocol, Sequence, TYPE_CHECKING

from .canonical import canonical_hash, canonical_json
from .errors import ErrorCode, LifeEngineError
from .events import normalize_actual_event_for_persistence
from .schema import preload_schema_validation, reject_binary_floats
from .timeutil import require_canonical_timestamp

if TYPE_CHECKING:
    from .activity_lifecycle import RuntimeWakeupProjectionFacts
    from .activity_materialization import RuntimeActivityMaterializationFacts, RuntimeMaterializationContext
    from .runtime_bundle import RuntimeBundle, RuntimeReferenceSets
    from .runtime_decision import RuntimeDecisionFacts, RuntimeDecisionFrame, RuntimeDecisionTrigger
    from .social_runtime import RuntimeSocialFacts, SocialResponseState


# Pin code/schema resources before call-local provider request construction.
# RuntimeFactRequestContext methods themselves perform no file I/O.
preload_schema_validation()

_TARGET_REQUEST_FIELDS = frozenset(
    {"target_time", "event_budget", "character_source_sha"}
)


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_nonempty_str(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label} must be a non-empty string")
    return value


def _require_non_neg_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{label} must be int (no bool/float)")
    if value < 0:
        _fail(f"{label} must be >= 0")
    return value


@dataclass(frozen=True)
class RuntimeTargetRequest:
    """Operational target envelope for provider-driven C3 execution."""

    target_time: str
    event_budget: int
    character_source_sha: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_time": self.target_time,
            "event_budget": self.event_budget,
            "character_source_sha": self.character_source_sha,
        }


@dataclass(frozen=True, init=False)
class RuntimeFactRequestContext:
    """Immutable ActualEvents finalized earlier in this C3 invocation.

    This is a call-local delta since the provider's persistent frozen history,
    never a second persisted history store. Event order is preserved exactly.
    """

    history_context_hash: str
    _events_json: str = field(repr=False)

    def __init__(
        self,
        finalized_events_since_base: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        if isinstance(finalized_events_since_base, (str, bytes)) or not isinstance(
            finalized_events_since_base, (list, tuple)
        ):
            _fail("finalized_events_since_base must be a list/tuple")
        events: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for i, raw in enumerate(finalized_events_since_base):
            if not isinstance(raw, Mapping):
                _fail(f"finalized_events_since_base[{i}] must be a mapping")
            event = normalize_actual_event_for_persistence(deepcopy(dict(raw)))
            event_id = event["event_id"]
            if event_id in seen_ids:
                _fail(f"duplicate call-local ActualEvent event_id: {event_id!r}")
            seen_ids.add(event_id)
            events.append(event)
        serialized = canonical_json(events)
        object.__setattr__(self, "_events_json", serialized)
        object.__setattr__(
            self,
            "history_context_hash",
            canonical_hash({"finalized_events_since_base": events}),
        )

    @property
    def finalized_events_since_base(self) -> tuple[dict[str, Any], ...]:
        return tuple(json.loads(self._events_json))

    def as_dict(self) -> dict[str, Any]:
        return {
            "history_context_hash": self.history_context_hash,
            "finalized_events_since_base": [
                dict(event) for event in self.finalized_events_since_base
            ],
        }


def parse_runtime_target_request(
    raw: RuntimeTargetRequest | Mapping[str, Any],
) -> RuntimeTargetRequest:
    if isinstance(raw, RuntimeTargetRequest):
        data = raw.as_dict()
    elif isinstance(raw, Mapping):
        data = dict(raw)
    else:
        _fail("RuntimeTargetRequest must be mapping or dataclass")

    reject_binary_floats(data)
    unknown = set(data) - _TARGET_REQUEST_FIELDS
    if unknown:
        _fail(f"RuntimeTargetRequest unknown fields: {sorted(unknown)}")
    missing = _TARGET_REQUEST_FIELDS - set(data)
    if missing:
        _fail(f"RuntimeTargetRequest missing fields: {sorted(missing)}")

    return RuntimeTargetRequest(
        target_time=require_canonical_timestamp(
            data["target_time"], field="target_time"
        ),
        event_budget=_require_non_neg_int(
            data["event_budget"], label="event_budget"
        ),
        character_source_sha=_require_nonempty_str(
            data["character_source_sha"], label="character_source_sha"
        ),
    )


@dataclass(frozen=True)
class RuntimeDecisionProviderResult:
    """Provider output for one authoritative decision trigger."""

    decision_facts: RuntimeDecisionFacts | Mapping[str, Any]
    social_facts: RuntimeSocialFacts | Mapping[str, Any]


class RuntimeFactProvider(Protocol):
    """Pure call-local fact delivery protocol.

    Implementations used by C3 must be deterministic over already-loaded frozen
    authority objects. The protocol deliberately provides no file/network/Git
    handles and no wall-clock facility.
    """

    def decision_inputs(
        self,
        *,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        trigger: RuntimeDecisionTrigger,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        target_request: RuntimeTargetRequest,
        request_context: RuntimeFactRequestContext,
    ) -> RuntimeDecisionProviderResult:
        ...

    def materialization_facts(
        self,
        *,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        frame: RuntimeDecisionFrame,
        context: RuntimeMaterializationContext,
        decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        target_request: RuntimeTargetRequest,
        request_context: RuntimeFactRequestContext,
    ) -> RuntimeActivityMaterializationFacts | Mapping[str, Any]:
        """Bind selected ID and immediate social mode to the exact context.

        For immediate social, return context.pending_immediate_accept.contact_mode
        explicitly. C3 validates it and bridges to C1's legacy null field, where
        the unchanged pending object remains the contact-mode authority.
        """
        ...

    def wakeup_facts(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        as_of: str,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        target_request: RuntimeTargetRequest,
        request_context: RuntimeFactRequestContext,
    ) -> RuntimeWakeupProjectionFacts | Mapping[str, Any]:
        ...

    def capture_source_moments(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        from_time: str,
        target_time: str,
        character_source_sha: str,
        target_request: RuntimeTargetRequest,
    ) -> Sequence[Mapping[str, Any]]:
        ...


__all__ = [
    "RuntimeDecisionProviderResult",
    "RuntimeFactProvider",
    "RuntimeFactRequestContext",
    "RuntimeTargetRequest",
    "parse_runtime_target_request",
]
