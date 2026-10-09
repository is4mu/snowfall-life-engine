"""JSON Schema load/validate for Life Engine objects."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import ErrorCode, LifeEngineError

# An installed wheel includes the exact public registry in this package.
# A source checkout continues to use schemas/life as its single authority.
# If packaged resources are present but invalid/incomplete, validation fails
# closed; do not silently fall back to a different schema set.
_PACKAGE_SCHEMAS = Path(__file__).resolve().parent / "_schemas"
_SOURCE_SCHEMAS = Path(__file__).resolve().parents[2] / "schemas" / "life"
SCHEMA_DIR = _PACKAGE_SCHEMAS if _PACKAGE_SCHEMAS.is_dir() else _SOURCE_SCHEMAS

SCHEMA_NAMES = {
    "behavior_policy": "behavior_policy.schema.json",
    "behavior_policy_fixture_v1": "behavior_policy_fixture_v1.schema.json",
    "behavior_policy_v2": "behavior_policy_v2.schema.json",
    "behavior_policy_approval": "behavior_policy_approval.schema.json",
    "bootstrap_proposal": "bootstrap_proposal.schema.json",
    "bootstrap_approval": "bootstrap_approval.schema.json",
    "commitment": "commitment.schema.json",
    "obligation_task": "obligation_task.schema.json",
    "route_profile": "route_profile.schema.json",
    "spatial_context": "spatial_context.schema.json",
    "social_archetype_assignment": "social_archetype_assignment.schema.json",
    "current_state": "current_state.schema.json",
    "active_activity": "active_activity.schema.json",
    "queued_internal_event": "queued_internal_event.schema.json",
    "actual_event": "actual_event.schema.json",
    "capture": "capture.schema.json",
    "capture_opportunity": "capture_opportunity.schema.json",
    "capture_source_moment": "capture_source_moment.schema.json",
    "capture_decision_evidence": "capture_decision_evidence.schema.json",
    "camera_roll_record": "camera_roll_record.schema.json",
    "camera_roll_shard": "camera_roll_shard.schema.json",
    "effect": "effect.schema.json",
    "timeline_day": "timeline_day.schema.json",
    "advance_result": "advance_result.schema.json",
    "provenance": "provenance.schema.json",
    "relation_state": "relation_state.schema.json",
    "home_state": "home_state.schema.json",
    "wardrobe_state": "wardrobe_state.schema.json",
    "consumables_state": "consumables_state.schema.json",
    "finance_state": "finance_state.schema.json",
    "schedule_state": "schedule_state.schema.json",
    "social_response_state": "social_response_state.schema.json",
    "runtime_last_run": "runtime_last_run.schema.json",
    "operator_history": "operator_history.schema.json",
    "operator_request": "operator_request.schema.json",
    "operator_action": "operator_action.schema.json",
    "last_operator_action": "last_operator_action.schema.json",
}


@lru_cache(maxsize=None)
def _registry():
    try:
        import jsonschema
        from jsonschema import Draft202012Validator
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT202012
    except ImportError as exc:  # pragma: no cover
        raise LifeEngineError(
            ErrorCode.INVALID_STATE,
            "jsonschema package required; pip install -r engine/life/requirements.txt",
        ) from exc

    resources = []
    for filename in SCHEMA_NAMES.values():
        path = SCHEMA_DIR / filename
        raw = json.loads(path.read_text(encoding="utf-8"))
        resources.append((raw["$id"], Resource.from_contents(raw, default_specification=DRAFT202012)))
    registry = Registry().with_resources(resources)
    return Draft202012Validator, registry, jsonschema


@lru_cache(maxsize=None)
def _schema_document_text(name: str) -> str:
    if name not in SCHEMA_NAMES:
        raise LifeEngineError(ErrorCode.SCHEMA_INVALID, f"unknown schema name: {name}")
    path = SCHEMA_DIR / SCHEMA_NAMES[name]
    return path.read_text(encoding="utf-8")


def load_schema_document(name: str) -> dict[str, Any]:
    # Fresh view: callers cannot mutate the process-pinned schema authority.
    return json.loads(_schema_document_text(name))


def preload_schema_validation() -> None:
    """Initialize pinned code/schema resources before pure source construction."""
    _registry()
    for name in SCHEMA_NAMES:
        _schema_document_text(name)


def validate_instance(instance: Any, schema_name: str) -> None:
    Draft202012Validator, registry, jsonschema = _registry()
    schema = load_schema_document(schema_name)
    validator = Draft202012Validator(schema, registry=registry)
    errors = sorted(validator.iter_errors(instance), key=lambda e: list(e.path))
    if errors:
        msgs = "; ".join(f"{list(e.path)}: {e.message}" for e in errors[:5])
        raise LifeEngineError(ErrorCode.SCHEMA_INVALID, msgs)


def reject_binary_floats(obj: Any, *, path: str = "$") -> None:
    if isinstance(obj, float):
        raise LifeEngineError(ErrorCode.INVALID_STATE, f"binary float at {path}")
    if isinstance(obj, dict):
        for k, v in obj.items():
            reject_binary_floats(v, path=f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            reject_binary_floats(v, path=f"{path}[{i}]")
