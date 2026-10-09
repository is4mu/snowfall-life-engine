"""Pre-v1 canonical persistent-runtime facade: verify or stage, never publish.

The reader validates a W1 snapshot without writing it. The provider factory
candidate builder writes only to a caller-owned, separate *candidate* tree.
Neither function creates/updates Git refs, authorizes production publication,
migrates old saves, or establishes a stable 1.x contract. Callers must keep
source authority quiescent and supply explicit approved identity sets/policy.
"""

from __future__ import annotations

from engine.life.runtime_persistence import (
    RuntimePersistenceResult,
    RuntimePersistentSnapshot,
    build_runtime_candidate_tree_with_provider_factory,
    load_runtime_persistent_snapshot,
)

__all__ = [
    "RuntimePersistentSnapshot",
    "RuntimePersistenceResult",
    "load_runtime_persistent_snapshot",
    "build_runtime_candidate_tree_with_provider_factory",
]
