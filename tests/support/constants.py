"""Shared synthetic test fixture paths for the public test suite."""

from __future__ import annotations

from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = TEST_ROOT / "fixtures"

POLICY_PATH = FIXTURES / "policy" / "v1_test_fixture.json"
POLICY_V2_CANDIDATE_PATH = FIXTURES / "policy" / "v2_synthetic_candidate.json"
PROPOSAL_PATH = FIXTURES / "bootstrap" / "proposal.json"
CAMERA_ROLL_V2_SHARD_PATH = FIXTURES / "golden" / "camera_roll_v2_shard.json"
