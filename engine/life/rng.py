"""Deterministic keyed randomness (no module-global PRNG)."""

from __future__ import annotations

import hashlib
import hmac
from typing import Union

SeedLike = Union[str, bytes, int]


def _seed_bytes(world_seed: SeedLike) -> bytes:
    if isinstance(world_seed, bytes):
        return world_seed
    if isinstance(world_seed, int):
        return str(world_seed).encode("utf-8")
    return str(world_seed).encode("utf-8")


def keyed_digest(
    world_seed: SeedLike,
    namespace: str,
    entity_id: str,
    decision_type: str,
    decision_instance: str,
) -> bytes:
    """HMAC-SHA256 over a stable key tuple; insertion of unrelated keys does not shift."""
    key = _seed_bytes(world_seed)
    msg = "\0".join(
        [
            "life.v2.rng",
            namespace,
            entity_id,
            decision_type,
            decision_instance,
        ]
    ).encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).digest()


def u01(
    world_seed: SeedLike,
    namespace: str,
    entity_id: str,
    decision_type: str,
    decision_instance: str,
) -> int:
    """Return an integer in [0, 10**9] representing a U[0,1) draw * 1e9 (no binary float persist)."""
    digest = keyed_digest(world_seed, namespace, entity_id, decision_type, decision_instance)
    # 53 bits of uniformity mapped into [0, 1_000_000_000]
    n = int.from_bytes(digest[:7], "big") >> 3  # 53 bits
    return (n * 1_000_000_000) // (1 << 53)


def u01_millionths(
    world_seed: SeedLike,
    namespace: str,
    entity_id: str,
    decision_type: str,
    decision_instance: str,
) -> int:
    """Alias: integer millionths in [0, 1_000_000]."""
    return u01(world_seed, namespace, entity_id, decision_type, decision_instance) // 1000
