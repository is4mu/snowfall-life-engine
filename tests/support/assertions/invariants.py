"""Reusable semantic invariant assertions for public tests."""

from __future__ import annotations

from typing import Any, Mapping


def assert_partition_equivalent(
    *,
    whole_state: Mapping[str, Any],
    whole_remainders: Mapping[str, Any],
    partitioned_state: Mapping[str, Any],
    partitioned_remainders: Mapping[str, Any],
) -> None:
    """Assert that partitioned integration is semantically identical to one-shot integration."""
    if dict(partitioned_state) != dict(whole_state):
        raise AssertionError(
            "partitioned state differs from one-shot state: "
            f"whole={dict(whole_state)!r}, partitioned={dict(partitioned_state)!r}"
        )
    if dict(partitioned_remainders) != dict(whole_remainders):
        raise AssertionError(
            "partitioned rate remainders differ from one-shot remainders: "
            f"whole={dict(whole_remainders)!r}, "
            f"partitioned={dict(partitioned_remainders)!r}"
        )
