"""72-hour continuous-vs-restart semantic equivalence soak."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

import pytest

from engine.life.canonical import canonical_hash
from engine.life.clock import advance, semantic_state_view, snapshot_workspace_bytes
from engine.life.ids import stable_id
from engine.life.timeutil import format_rfc3339, parse_rfc3339
from tests.support.builders.workspace import (
    load_state,
    make_workspace,
    policy_path,
    queue_activity_end,
)

pytestmark = [pytest.mark.soak, pytest.mark.slow]

HORIZON_START = "2026-01-01T00:00:00+09:00"
HORIZON_END = "2026-01-04T00:00:00+09:00"
PARTITION_HOURS = 6
PARTITION_COUNT = 12


def _seed_workspace(root: Path) -> Path:
    """Create the same synthetic persisted starting point for both executions."""
    workspace = make_workspace(root)
    activity = {
        "schema_version": 1,
        "activity_instance_id": stable_id("act", "restart-equivalence", "72h"),
        "activity_type": "fixture_meal",
        "actual_start": HORIZON_START,
        "planned_end": "2026-01-01T05:00:00+09:00",
        "location_id": "fixture-home",
        "summary": "72h restart-equivalence fixture",
        "effects_on_end": [
            {
                "schema_version": 1,
                "effect_type": "HUMAN_STATE_DELTA",
                "payload": {"hunger": -50, "stress": -20},
            }
        ],
    }
    queue_activity_end(
        workspace,
        activity=activity,
        due_at="2026-01-01T05:00:00+09:00",
    )
    return workspace


def _restart_from_persisted_bytes(source: Path, destination: Path) -> Path:
    """Simulate a process restart using only serialized workspace bytes."""
    if destination.exists():
        raise AssertionError(f"restart destination already exists: {destination}")

    before = snapshot_workspace_bytes(source)
    if "current_state.json" not in before:
        raise AssertionError("restart source has no serialized checkpoint")

    for rel, data in before.items():
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    after = snapshot_workspace_bytes(destination)
    if after != before:
        raise AssertionError("restart serialization roundtrip changed persisted bytes")
    return destination


def _timeline_semantics(workspace: Path) -> dict[str, object]:
    timeline = workspace / "timeline"
    if not timeline.exists():
        return {}
    return {
        path.name: json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(timeline.glob("*.json"))
    }


class RestartEquivalence72HourTests(unittest.TestCase):
    def test_restart_equivalence_72h(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            continuous = _seed_workspace(root / "continuous")
            partitioned = _seed_workspace(root / "partitioned")

            advance(
                continuous,
                target=HORIZON_END,
                policy_path=policy_path(),
            )

            start = parse_rfc3339(HORIZON_START)
            partition_targets = tuple(
                format_rfc3339(start + timedelta(hours=PARTITION_HOURS * step))
                for step in range(1, PARTITION_COUNT + 1)
            )
            self.assertEqual(len(partition_targets), 12)
            self.assertEqual(partition_targets[-1], HORIZON_END)

            for index, target in enumerate(partition_targets, start=1):
                advance(
                    partitioned,
                    target=target,
                    policy_path=policy_path(),
                )
                partitioned = _restart_from_persisted_bytes(
                    partitioned,
                    root / f"restart-{index:02d}",
                )

            continuous_state = semantic_state_view(load_state(continuous))
            restarted_state = semantic_state_view(load_state(partitioned))

            self.assertEqual(continuous_state, restarted_state)
            self.assertEqual(
                canonical_hash(continuous_state),
                canonical_hash(restarted_state),
            )
            self.assertEqual(
                _timeline_semantics(continuous),
                _timeline_semantics(partitioned),
            )
            self.assertEqual(restarted_state["processed_through"], HORIZON_END)


if __name__ == "__main__":
    unittest.main()
