"""Public ownership and dependency boundaries for runtime orchestration."""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

import engine.life.runtime_orchestrator as orchestrator
from engine.life.runtime_orchestrator import (
    RuntimeDecisionStepInput,
    RuntimeTargetInputs,
    RuntimeWakeupStepInput,
)

pytestmark = pytest.mark.contract


def test_orchestrator_owns_no_persistence_git_or_image_materialization() -> None:
    source = inspect.getsource(orchestrator)
    for banned in (
        "from .runtime_persistence",
        "from .runtime_git_transaction",
        "from .runtime_remote_publisher",
        "subprocess",
        "git.Repo",
        "write_text(",
        "open(",
        "PIL",
        "Image.save",
    ):
        assert banned not in source


def test_orchestrator_owned_trigger_surface_is_closed() -> None:
    assert orchestrator._C3_OWNED_TRIGGERS == frozenset(
        {"POST_FINALIZE", "IMMEDIATE_REASSESSMENT"}
    )


def test_runtime_target_input_surface_is_closed() -> None:
    assert {f.name for f in fields(RuntimeTargetInputs)} == {
        "target_time",
        "event_budget",
        "character_source_sha",
        "capture_source_moments",
        "decision_steps",
        "wakeup_steps",
    }
    assert {f.name for f in fields(RuntimeDecisionStepInput)} == {
        "trigger_id",
        "decision_facts",
        "social_facts",
        "materialization_facts",
    }
    assert {f.name for f in fields(RuntimeWakeupStepInput)} == {
        "activity_instance_id",
        "as_of",
        "projection_facts",
    }
