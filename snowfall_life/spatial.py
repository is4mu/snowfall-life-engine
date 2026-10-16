"""Pre-v1 spatial *data* boundary; no host geography, routing service or I/O.

Only validated, sealed SpatialContext data and full approved route projection
are exposed. No SpatialAdapter protocol is invented. These exact identity
aliases remain proposals until the owner approves stable v1 public imports.
"""

from __future__ import annotations

from engine.life.spatial_context import build_spatial_context, validate_spatial_context
from engine.life.runtime_spatial import (
    project_spatial_runtime_decision_facts,
    project_spatial_runtime_target_inputs,
)

__all__ = [
    "build_spatial_context",
    "validate_spatial_context",
    "project_spatial_runtime_decision_facts",
    "project_spatial_runtime_target_inputs",
]
