"""Slice 5A RuntimeBundle — read / validate boundary only.

Loads and verifies a synthetic persisted-runtime tree. No writes, no Git,
no network, no behavior orchestration, no Slice 3 reducer application, no
Camera Roll writes, no life branch. Does not read application-owned legacy
character or social-graph files as v2 mutable state.

Production application state is not activated by this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checkpoint import validate_checkpoint
from .clock import verify_workspace_history
from .errors import ErrorCode, LifeEngineError
from .history import EMPTY_HISTORY_HASH
from .schedule_state import validate_schedule_state
from .schema import reject_binary_floats
from .timeutil import parse_rfc3339
from .world_state import (
    validate_consumables_state,
    validate_finance_state,
    validate_home_state,
    validate_relation_state,
    validate_wardrobe_state,
)

# Exact required relative paths under a RuntimeBundle root (Issue #66 / D5A-1).
REQUIRED_BUNDLE_RELPATHS: tuple[str, ...] = (
    "state/current.json",
    "schedule/state.json",
    "relations/state.json",
    "home/state.json",
    "consumables/state.json",
    "wardrobe/state.json",
    "finance/state.json",
)

_ENGINE_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

# Paths that must never be consulted as v2 mutable runtime state by 5A loaders.
FORBIDDEN_LEGACY_RUNTIME_RELPATHS: tuple[str, ...] = (
    "application-runtime/character.json",
    "application-runtime/social-graph.json",
)


def _fail(detail: str) -> None:
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def _freeze_id_set(value: Any, *, label: str) -> frozenset[str]:
    """Strict set/frozenset of non-empty strings. No list/tuple coercion."""
    if not isinstance(value, (set, frozenset)):
        _fail(f"{label}: expected set/frozenset of non-empty strings")
    # Validate element types before any further set/hash construction.
    validated: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str) or not item:
            _fail(f"{label} element[{i}]: expected non-empty string")
        validated.append(item)
    return frozenset(validated)


@dataclass(frozen=True)
class RuntimeReferenceSets:
    """Caller-supplied approved identity sets for world-state validation.

    No Canon/Bible scraping, no legacy runtime file lookup, no defaults from
    loaded world states. Unknown IDs fail closed. Accepts only set/frozenset
    (no list/tuple coercion).
    """

    known_person_ids: frozenset[str]
    known_home_entity_ids: frozenset[str]
    approved_wardrobe_item_ids: frozenset[str]
    approved_consumable_ids: frozenset[str]

    def __init__(
        self,
        known_person_ids: set[str] | frozenset[str],
        known_home_entity_ids: set[str] | frozenset[str],
        approved_wardrobe_item_ids: set[str] | frozenset[str],
        approved_consumable_ids: set[str] | frozenset[str],
    ) -> None:
        object.__setattr__(
            self,
            "known_person_ids",
            _freeze_id_set(known_person_ids, label="known_person_ids"),
        )
        object.__setattr__(
            self,
            "known_home_entity_ids",
            _freeze_id_set(known_home_entity_ids, label="known_home_entity_ids"),
        )
        object.__setattr__(
            self,
            "approved_wardrobe_item_ids",
            _freeze_id_set(approved_wardrobe_item_ids, label="approved_wardrobe_item_ids"),
        )
        object.__setattr__(
            self,
            "approved_consumable_ids",
            _freeze_id_set(approved_consumable_ids, label="approved_consumable_ids"),
        )


@dataclass(frozen=True)
class RuntimeBundle:
    """Immutable in-memory view of a complete RuntimeBundle."""

    current_state: Mapping[str, Any]
    schedule_state: Mapping[str, Any]
    relation_state: Mapping[str, Any]
    home_state: Mapping[str, Any]
    consumables_state: Mapping[str, Any]
    wardrobe_state: Mapping[str, Any]
    finance_state: Mapping[str, Any]


@dataclass(frozen=True)
class RuntimeBundleVerification:
    """Successful verification result (fail-closed raises instead of ok=false)."""

    bundle: RuntimeBundle
    root: Path


def _read_json_object(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    if not isinstance(data, dict):
        _fail(f"{path}: expected JSON object")
    return data


def _require_files(root: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    missing: list[str] = []
    for rel in REQUIRED_BUNDLE_RELPATHS:
        path = root / rel
        if not path.is_file():
            missing.append(rel)
        else:
            paths[rel] = path
    if missing:
        _fail(f"RuntimeBundle missing required files: {missing}")
    return paths


def _require_non_null_str(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value:
        _fail(f"{label}: required non-null non-empty string")
    return value


def _require_as_of_not_after_processed(
    *,
    as_of: str,
    processed_through: str,
    label: str,
) -> None:
    as_of_dt = parse_rfc3339(as_of, field=f"{label}.as_of")
    processed = parse_rfc3339(processed_through, field="processed_through")
    if as_of_dt > processed:
        _fail(f"{label}.as_of after CurrentState.processed_through")


def _validate_engine_commit_sha(sha: Any) -> str:
    if not isinstance(sha, str) or not _ENGINE_COMMIT_SHA_RE.fullmatch(sha):
        _fail("CurrentState.engine_commit_sha must match ^[0-9a-f]{40}$ for RuntimeBundle")
    return sha


def _cross_file_identity(bundle_parts: Mapping[str, Mapping[str, Any]]) -> None:
    current = bundle_parts["current_state"]
    character_id = current["character_id"]
    for name in (
        "schedule_state",
        "relation_state",
        "home_state",
        "consumables_state",
        "wardrobe_state",
        "finance_state",
    ):
        other = bundle_parts[name]["character_id"]
        if other != character_id:
            _fail(f"character_id mismatch: CurrentState={character_id!r} {name}={other!r}")

    refs = current["domain_refs"]
    checks = (
        ("relation_state_revision", "relation_state", "revision"),
        ("home_revision", "home_state", "revision"),
        ("consumables_revision", "consumables_state", "revision"),
        ("wardrobe_revision", "wardrobe_state", "revision"),
        ("finance_revision", "finance_state", "revision"),
    )
    for ref_key, state_key, field in checks:
        expected = _require_non_null_str(refs.get(ref_key), label=f"domain_refs.{ref_key}")
        actual = bundle_parts[state_key][field]
        if expected != actual:
            _fail(
                f"domain_refs.{ref_key} mismatch: "
                f"CurrentState={expected!r} {state_key}.{field}={actual!r}"
            )

    schedule = bundle_parts["schedule_state"]
    sched_rev = _require_non_null_str(
        refs.get("schedule_revision"), label="domain_refs.schedule_revision"
    )
    sched_hash = _require_non_null_str(
        refs.get("schedule_hash"), label="domain_refs.schedule_hash"
    )
    if sched_rev != schedule["revision"]:
        _fail(
            f"schedule_revision mismatch: "
            f"CurrentState={sched_rev!r} ScheduleState.revision={schedule['revision']!r}"
        )
    if sched_hash != schedule["state_hash"]:
        _fail(
            f"schedule_hash mismatch: "
            f"CurrentState={sched_hash!r} ScheduleState.state_hash={schedule['state_hash']!r}"
        )

    processed = current["processed_through"]
    for name in (
        "schedule_state",
        "relation_state",
        "home_state",
        "consumables_state",
        "wardrobe_state",
        "finance_state",
    ):
        _require_as_of_not_after_processed(
            as_of=bundle_parts[name]["as_of"],
            processed_through=processed,
            label=name,
        )


def _verify_history_boundary(root: Path, current_state: Mapping[str, Any]) -> None:
    timeline_dir = root / "timeline"
    history = current_state["history"]
    if not timeline_dir.exists():
        # Absent timeline is valid only with empty-history checkpoint.
        if (
            history.get("head_event_id") is not None
            or int(history.get("event_count", -1)) != 0
            or history.get("history_hash") != EMPTY_HISTORY_HASH
        ):
            _fail(
                "timeline/ absent requires empty-history CurrentState "
                "(head_event_id=null, event_count=0, EMPTY_HISTORY_HASH)"
            )
        # Still run shared verifier against empty days for active/queue invariants.
        verify_workspace_history(root, dict(current_state))
        return
    verify_workspace_history(root, dict(current_state))


def validate_runtime_bundle(
    bundle: RuntimeBundle | Mapping[str, Any],
    *,
    reference_sets: RuntimeReferenceSets,
) -> RuntimeBundle:
    """Validate an in-memory RuntimeBundle with explicit reference sets.

    Does not write files. Does not scrape Canon or legacy runtime paths.
    """
    if isinstance(bundle, RuntimeBundle):
        parts = {
            "current_state": dict(bundle.current_state),
            "schedule_state": dict(bundle.schedule_state),
            "relation_state": dict(bundle.relation_state),
            "home_state": dict(bundle.home_state),
            "consumables_state": dict(bundle.consumables_state),
            "wardrobe_state": dict(bundle.wardrobe_state),
            "finance_state": dict(bundle.finance_state),
        }
    elif isinstance(bundle, Mapping):
        required_keys = (
            "current_state",
            "schedule_state",
            "relation_state",
            "home_state",
            "consumables_state",
            "wardrobe_state",
            "finance_state",
        )
        parts = {}
        for key in required_keys:
            if key not in bundle:
                _fail(f"RuntimeBundle missing key: {key}")
            value = bundle[key]
            if not isinstance(value, Mapping):
                _fail(f"RuntimeBundle.{key}: expected object")
            parts[key] = dict(value)
    else:
        _fail("validate_runtime_bundle: expected RuntimeBundle or mapping")

    for key, value in parts.items():
        reject_binary_floats(value, path=f"$.{key}")

    current = validate_checkpoint(deepcopy(parts["current_state"]))
    _validate_engine_commit_sha(current["engine_commit_sha"])

    schedule = validate_schedule_state(parts["schedule_state"])
    relation = validate_relation_state(
        parts["relation_state"],
        known_person_ids=reference_sets.known_person_ids,
    )
    home = validate_home_state(
        parts["home_state"],
        known_entity_ids=reference_sets.known_home_entity_ids,
    )
    consumables = validate_consumables_state(
        parts["consumables_state"],
        approved_consumable_ids=reference_sets.approved_consumable_ids,
    )
    wardrobe = validate_wardrobe_state(
        parts["wardrobe_state"],
        approved_item_ids=reference_sets.approved_wardrobe_item_ids,
    )
    finance = validate_finance_state(parts["finance_state"])

    validated_parts = {
        "current_state": current,
        "schedule_state": schedule,
        "relation_state": relation,
        "home_state": home,
        "consumables_state": consumables,
        "wardrobe_state": wardrobe,
        "finance_state": finance,
    }
    _cross_file_identity(validated_parts)

    return RuntimeBundle(
        current_state=current,
        schedule_state=schedule,
        relation_state=relation,
        home_state=home,
        consumables_state=consumables,
        wardrobe_state=wardrobe,
        finance_state=finance,
    )


def load_runtime_bundle(
    root: Path | str,
    *,
    reference_sets: RuntimeReferenceSets,
) -> RuntimeBundle:
    """Read required RuntimeBundle files and return a validated immutable bundle.

    Read-only: never creates, updates, or deletes files under ``root``.
    """
    root_path = Path(root)
    if not root_path.is_dir():
        _fail(f"RuntimeBundle root is not a directory: {root_path}")
    paths = _require_files(root_path)

    raw = {
        "current_state": _read_json_object(paths["state/current.json"]),
        "schedule_state": _read_json_object(paths["schedule/state.json"]),
        "relation_state": _read_json_object(paths["relations/state.json"]),
        "home_state": _read_json_object(paths["home/state.json"]),
        "consumables_state": _read_json_object(paths["consumables/state.json"]),
        "wardrobe_state": _read_json_object(paths["wardrobe/state.json"]),
        "finance_state": _read_json_object(paths["finance/state.json"]),
    }
    bundle = validate_runtime_bundle(raw, reference_sets=reference_sets)
    _verify_history_boundary(root_path, bundle.current_state)
    return bundle


def verify_runtime_bundle(
    root: Path | str,
    *,
    reference_sets: RuntimeReferenceSets,
) -> RuntimeBundleVerification:
    """Load + validate a RuntimeBundle root. Read-only; fail-closed on any defect."""
    root_path = Path(root)
    bundle = load_runtime_bundle(root_path, reference_sets=reference_sets)
    return RuntimeBundleVerification(bundle=bundle, root=root_path.resolve())


def iter_required_relpaths() -> Iterable[str]:
    """Public enumeration of exact required bundle relative paths."""
    return REQUIRED_BUNDLE_RELPATHS
