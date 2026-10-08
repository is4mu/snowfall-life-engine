"""Public safety and ownership boundaries for runtime persistence."""

from __future__ import annotations

import inspect

import pytest

import engine.life.runtime_persistence as persistence
from engine.life.runtime_persistence import is_allowed_runtime_relpath

pytestmark = pytest.mark.contract


def test_persistence_owns_no_git_network_or_shell_execution() -> None:
    source = inspect.getsource(persistence)
    for banned in (
        "import git",
        "from git",
        "git.Repo",
        "subprocess",
        "os.system",
        "git push",
        "update-ref",
        "refs/heads/",
        "requests.",
        "urllib.",
    ):
        assert banned not in source


def test_public_runtime_path_contract_is_closed() -> None:
    for rel in (
        "state/current.json",
        "state/social-responses.json",
        "schedule/state.json",
        "relations/state.json",
        "home/state.json",
        "consumables/state.json",
        "wardrobe/state.json",
        "finance/state.json",
        "timeline/2026-04-01.json",
        "camera-roll/records/2026-04.json",
    ):
        assert is_allowed_runtime_relpath(rel), rel

    for rel in (
        "engine/life/runtime_persistence.py",
        ".github/workflows/ci.yml",
        "state/debug.json",
        "timeline/debug.json",
        "timeline/2026-04-01.json.tmp",
        "camera-roll/debug.txt",
        "camera-roll/records/debug.json",
        "README.md",
    ):
        assert not is_allowed_runtime_relpath(rel), rel


def test_persistence_does_not_materialize_images_or_activate_production() -> None:
    source = inspect.getsource(persistence)
    for banned in (
        "PIL",
        "Image.save",
        "materialize_image",
        "publish_image",
        "activate_production",
        "production_launcher",
    ):
        assert banned not in source


def test_candidate_builder_keeps_baseline_and_candidate_authorities_distinct() -> None:
    source = inspect.getsource(persistence._build_runtime_candidate_tree_core)
    assert "candidate_root must be distinct from baseline_root" in source
    assert "candidate_root must not be inside baseline_root" in source
    assert "baseline_root must not be inside candidate_root" in source
    assert "baseline root mutated" in source
