"""Exercise the immutable v0.1.0 reader consumer on source checkout.

Installed wheel and sdist import isolation + hashseed/timezone matrix run in
.github/workflows/package.yml. Neither suite is yet a 1.0 release promise.
"""

from __future__ import annotations

import pytest

from examples.legacy_v010_reader.run import verify_installed_reader

pytestmark = pytest.mark.contract


def test_pre_v1_reader_preserves_16_family_legacy_snapshot() -> None:
    result = verify_installed_reader()
    assert result["status"] == "PASS"
    assert result["scope"] == "pre-v1-installed-reader-v0.1.0"
    assert result["old_engine_version"] == "0.1.0-foundation"
    assert result["families"] == 16
    assert result["files"] == 15
    assert len(result["semantic_tree_hash"]) == 64
    assert result["source_unchanged"]
    assert result["rejects_future_engine_version"]
