"""Fail-closed pre-v1 check for the injected upgrade host boundary."""

from __future__ import annotations

import pytest

from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.operator_upgrade import load_checkout_policy_identity

pytestmark = pytest.mark.contract


def test_upgrade_requires_complete_host_adapter_before_policy_loading(tmp_path) -> None:
    # With no host adapter implementation, do not inspect/read implicit private
    # policy files and do not silently accept a checkout as approved.
    with pytest.raises(LifeEngineError) as err:
        load_checkout_policy_identity(tmp_path, upgrade_adapter=object())
    assert err.value.code == ErrorCode.INVALID_STATE
    assert "upgrade_adapter." in err.value.detail
