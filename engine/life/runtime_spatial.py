"""Spatial C — pure projection of approved SpatialContext routes into runtime facts.

Projects the complete validated SpatialContext.route_profiles collection into
RuntimeDecisionFacts / RuntimeTargetInputs without filtering, reverse inference,
duration recalculation, or production wiring.

Existing parse_runtime_decision_facts remains the generic/synthetic parser.
Spatial C is the stricter production-authority projection path for later 5D
composition. Does not modify C3, 5C1, SpatialContext, or bootstrap semantics.

Production application state is not activated by this module.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .errors import ErrorCode, LifeEngineError
from .runtime_decision import RuntimeDecisionFacts, parse_runtime_decision_facts
from .runtime_orchestrator import (
    RuntimeDecisionStepInput,
    RuntimeTargetInputs,
    parse_runtime_target_inputs,
)
from .spatial_context import validate_spatial_context


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _require_explicit_character_id(character_id: Any) -> str:
    if not isinstance(character_id, str) or not character_id:
        _fail("character_id: expected non-empty string")
    return character_id


def _reject_caller_route_profiles(mapping: Mapping[str, Any], *, label: str) -> None:
    if "route_profiles" in mapping:
        _fail(
            f"{label} must not supply route_profiles "
            "(SpatialContext is the sole route authority for Spatial C)"
        )


def _validated_bound_spatial_context(
    spatial_context: Any,
    *,
    character_id: str,
) -> dict[str, Any]:
    if not isinstance(spatial_context, Mapping):
        _fail("spatial_context: expected object")
    ctx = validate_spatial_context(spatial_context)
    if character_id != ctx["character_id"]:
        _fail(
            "character_id does not match spatial_context.character_id "
            f"({character_id!r} != {ctx['character_id']!r})"
        )
    return ctx


def _route_set_from_spatial_context(ctx: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Deep-copy the canonical SpatialContext route set (route_profile_id order)."""
    routes = ctx["route_profiles"]
    if not isinstance(routes, list):
        _fail("spatial_context.route_profiles: expected list")
    return [deepcopy(dict(route)) for route in routes]


def project_spatial_runtime_decision_facts(
    *,
    spatial_context: Mapping[str, Any],
    character_id: str,
    facts_without_routes: Mapping[str, Any],
    now: str | None = None,
) -> RuntimeDecisionFacts:
    """Project complete approved SpatialContext routes into RuntimeDecisionFacts.

    Caller must not supply ``route_profiles`` at all. Structural validation is
    delegated to existing ``parse_runtime_decision_facts(..., now=now)``.
    """
    char = _require_explicit_character_id(character_id)
    if not isinstance(facts_without_routes, Mapping):
        _fail("facts_without_routes must be a mapping")
    _reject_caller_route_profiles(
        facts_without_routes, label="facts_without_routes"
    )

    ctx = _validated_bound_spatial_context(spatial_context, character_id=char)
    projected = deepcopy(dict(facts_without_routes))
    projected["route_profiles"] = _route_set_from_spatial_context(ctx)
    return parse_runtime_decision_facts(projected, now=now)


def project_spatial_runtime_target_inputs(
    *,
    spatial_context: Mapping[str, Any],
    character_id: str,
    target_inputs_without_routes: Mapping[str, Any] | RuntimeTargetInputs,
) -> RuntimeTargetInputs:
    """Project complete approved routes into every RuntimeTargetInputs decision step.

    C3 remains authoritative for per-decision now validation. Delegates final
    structure to parse_runtime_target_inputs only.
    """
    char = _require_explicit_character_id(character_id)
    ctx = _validated_bound_spatial_context(spatial_context, character_id=char)
    approved_routes = _route_set_from_spatial_context(ctx)

    if isinstance(target_inputs_without_routes, RuntimeTargetInputs):
        data = target_inputs_without_routes.as_dict()
    elif isinstance(target_inputs_without_routes, Mapping):
        data = deepcopy(dict(target_inputs_without_routes))
    else:
        _fail(
            "target_inputs_without_routes must be a mapping or RuntimeTargetInputs"
        )

    steps_raw = data.get("decision_steps")
    if steps_raw is None:
        _fail("target_inputs_without_routes missing decision_steps")
    if not isinstance(steps_raw, (list, tuple)) or isinstance(
        steps_raw, (str, bytes)
    ):
        _fail("decision_steps must be a list/tuple")

    projected_steps: list[dict[str, Any]] = []
    for i, step in enumerate(steps_raw):
        if isinstance(step, RuntimeDecisionStepInput):
            facts = step.decision_facts
            if isinstance(facts, RuntimeDecisionFacts):
                _fail(
                    f"decision_steps[{i}].decision_facts: already-built "
                    "RuntimeDecisionFacts embeds route authority"
                )
            if not isinstance(facts, Mapping):
                _fail(f"decision_steps[{i}].decision_facts must be a mapping")
            _reject_caller_route_profiles(
                facts, label=f"decision_steps[{i}].decision_facts"
            )
            step_data = {
                "trigger_id": step.trigger_id,
                "decision_facts": dict(facts),
                "social_facts": dict(step.social_facts),
                "materialization_facts": (
                    None
                    if step.materialization_facts is None
                    else step.materialization_facts
                ),
            }
        elif isinstance(step, Mapping):
            step_data = dict(step)
        else:
            _fail(f"decision_steps[{i}] must be a mapping")

        facts = step_data.get("decision_facts")
        if isinstance(facts, RuntimeDecisionFacts):
            _fail(
                f"decision_steps[{i}].decision_facts: already-built "
                "RuntimeDecisionFacts embeds route authority"
            )
        if not isinstance(facts, Mapping):
            _fail(f"decision_steps[{i}].decision_facts must be a mapping")
        _reject_caller_route_profiles(
            facts, label=f"decision_steps[{i}].decision_facts"
        )

        facts_projected = deepcopy(dict(facts))
        facts_projected["route_profiles"] = [
            deepcopy(route) for route in approved_routes
        ]

        projected_step = {
            key: deepcopy(value)
            for key, value in step_data.items()
            if key != "decision_facts"
        }
        projected_step["decision_facts"] = facts_projected
        projected_steps.append(projected_step)

    projected: dict[str, Any] = {
        key: deepcopy(value) for key, value in data.items() if key != "decision_steps"
    }
    projected["decision_steps"] = projected_steps
    return parse_runtime_target_inputs(projected)
