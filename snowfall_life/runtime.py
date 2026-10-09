"""Pre-v1 canonical runtime/fact-provider facade (proposed exports only).

This module intentionally aliases the existing engine implementation; it does
not create another simulation engine, modify a policy or declare stable 1.x APIs.
Application-owned providers supply *approved* facts; no host/network/Git API is
provided by this module. The names and types need a v1 compatibility review.
"""

from __future__ import annotations

from engine.life.runtime_bundle import RuntimeBundle, RuntimeReferenceSets
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
    "parse_runtime_target_request",
    "advance_runtime_to_target_with_provider",
]
