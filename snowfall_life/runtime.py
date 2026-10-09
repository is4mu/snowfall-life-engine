"""Pre-v1 canonical runtime/fact-provider facade (proposed exports only).

This module intentionally aliases the existing engine implementation; it does
not create another simulation engine, modify a policy or declare stable 1.x APIs.
Application-owned providers supply *approved* facts; no host/network/Git API is
provided by this module. The names and types need a v1 compatibility review.
"""

from __future__ import annotations

from engine.life.activity_lifecycle import RuntimeWakeupProjectionFacts
from engine.life.activity_materialization import (
    RuntimeActivityMaterializationFacts,
    RuntimeMaterializationContext,
)
from engine.life.runtime_bundle import RuntimeBundle, RuntimeReferenceSets
from engine.life.runtime_decision import (
    RuntimeDecisionFacts,
    RuntimeDecisionFrame,
    RuntimeDecisionTrigger,
)
from engine.life.social_runtime import SocialResponseState
from engine.life.runtime_fact_provider import (
    RuntimeDecisionProviderResult,
    RuntimeFactProvider,
    RuntimeFactRequestContext,
    RuntimeTargetRequest,
    parse_runtime_target_request,
)
from engine.life.runtime_orchestrator import (
    RuntimeTargetAdvanceResult,
    advance_runtime_to_target_with_provider,
)

__all__ = [
    "RuntimeBundle",
    "RuntimeReferenceSets",
    "RuntimeTargetRequest",
    "RuntimeFactRequestContext",
    "RuntimeFactProvider",
    "RuntimeDecisionProviderResult",
    "RuntimeTargetAdvanceResult",
    "RuntimeDecisionFacts",
    "RuntimeDecisionFrame",
    "RuntimeDecisionTrigger",
    "RuntimeMaterializationContext",
    "RuntimeActivityMaterializationFacts",
    "RuntimeWakeupProjectionFacts",
    "SocialResponseState",
    "parse_runtime_target_request",
    "advance_runtime_to_target_with_provider",
]
