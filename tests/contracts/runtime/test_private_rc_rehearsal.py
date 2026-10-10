"""Protect temporary RC smoke workflow from source/release side effects."""

from __future__ import annotations
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]


def test_ephemeral_rc_workflow_is_pr_only_readonly_and_unpublished() -> None:
    text = (ROOT / ".github/workflows/p1-private-rc.yml").read_text()
    assert "on:\n  pull_request:" in text
    assert "permissions:\n  contents: read" in text
    assert "persist-credentials: false" in text
    assert "github.event.pull_request.head.sha" in text
    assert "startsWith(github.head_ref, 'p1/v1-rc-')" in text
    assert "git archive --format=tar HEAD" in text
    assert 'version = "1.0.0rc1"' in text
    assert "3 hash seeds x 2 timezones" in text
    assert "actions/upload-artifact" not in text
    assert "twine upload" not in text
    assert "gh release" not in text
    assert "git push" not in text


def test_development_source_stays_at_dev0_until_release_is_approved() -> None:
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert toml.count('version = "0.2.0.dev0"') == 1
    assert 'version = "1.0.0rc1"' not in toml
    assert 'requires-python = ">=3.12,<3.13"' in toml
