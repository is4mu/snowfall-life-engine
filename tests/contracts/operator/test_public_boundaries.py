"""Public operator-module boundaries for the OSS export."""

from __future__ import annotations

import inspect

import pytest

from engine.life import operator_git_transaction, operator_upgrade

pytestmark = pytest.mark.contract


def test_operator_modules_do_not_import_private_production_components() -> None:
    sources = (
        inspect.getsource(operator_git_transaction),
        inspect.getsource(operator_upgrade),
    )
    forbidden = (
        "runtime_production_authority",
        "runtime_production_launcher",
        "runtime_operator_launcher",
        "project-snowfall",
        "private-index",
        "Yukino",
    )
    for source in sources:
        for token in forbidden:
            assert token not in source


def test_operator_commit_message_uses_public_identity() -> None:
    manifest = {
        "action_id": "a" * 64,
        "action_type": "CORRECTION",
        "state_revision_before": 1,
        "state_revision_after": 2,
    }
    message = operator_git_transaction.operator_commit_message(manifest)
    assert message.startswith(f"Life Engine operator correction {'a' * 64}\n")
    assert "Snowfall operator" not in message
    assert message.endswith("\n")
    assert not message.endswith("\n\n")


def test_upgrade_environment_boundary_is_explicit() -> None:
    source = inspect.getsource(operator_upgrade.UpgradeEnvironmentAdapter)
    for name in (
        "load_checkout_policy_material",
        "materialize_pinned_engine_checkout",
        "cleanup_materialized_checkout",
        "probe_target_compatibility",
    ):
        assert name in source
