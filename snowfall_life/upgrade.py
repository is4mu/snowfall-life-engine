"""Pre-v1 host-injected upgrade *probe* boundary, not a publish command.

A host supplies pinned checkouts, approved policy material and one read-only
probe. The engine verifies evidence but never creates/updates remote refs.
No upgrade/migration registry, host credentials or live publication is exposed.
This review-only API is NOT an approved stable v1 upgrade facade.
"""

from __future__ import annotations

from engine.life.operator_upgrade import (
    CheckoutPolicyMaterial,
    CompatibilityProbeResult,
    UpgradeEnvironmentAdapter,
    run_target_compatibility_probe,
)

__all__ = [
    "UpgradeEnvironmentAdapter",
    "CheckoutPolicyMaterial",
    "CompatibilityProbeResult",
    "run_target_compatibility_probe",
]
