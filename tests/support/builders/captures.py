"""Synthetic capture builders shared by public tests."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from engine.life.captures import (
    build_capture_decision_evidence,
    build_capture_fact,
    derive_capture_occurrence_key_from_opportunity,
    derive_capture_opportunity_key_from_source,
)

_TEST_RANDOM_KEY = "a" * 64


def synthetic_capture_bundle(
    *,
    activity_instance_id: str,
    source_key: str,
    policy_version: str,
    activation_permille: int = 500,
    rule_ids: Sequence[str] | None = None,
    input_state_refs: Sequence[str] | None = None,
    random_key: str = _TEST_RANDOM_KEY,
) -> tuple[str, dict[str, Any]]:
    """Return deterministic synthetic occurrence/evidence for Capture facts."""
    opp = derive_capture_opportunity_key_from_source(activity_instance_id, source_key)
    occ = derive_capture_occurrence_key_from_opportunity(activity_instance_id, opp)
    evidence = build_capture_decision_evidence(
        activity_instance_id=activity_instance_id,
        source_key=source_key,
        policy_version=policy_version,
        activation_permille=activation_permille,
        rule_ids=list(rule_ids) if rule_ids is not None else ["test.synthetic.capture"],
        input_state_refs=list(input_state_refs) if input_state_refs is not None else ["state:test"],
        random_key=random_key,
        opportunity_key=opp,
    )
    return occ, evidence


def build_test_capture_fact(
    *,
    activity_instance_id: str,
    occurrence_key: str,
    captured_at: str,
    capture_kind: str,
    subject_refs: Sequence[str],
    visual_context: Mapping[str, Any],
    source_key: str | None = None,
    capture_decision_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Capture with required synthetic TAKE evidence."""
    vc = dict(visual_context)
    sk = source_key if source_key is not None else f"source:{occurrence_key}"
    if capture_decision_evidence is None:
        occ, evidence = synthetic_capture_bundle(
            activity_instance_id=activity_instance_id,
            source_key=sk,
            policy_version=vc["behavior_policy_version"],
        )
    else:
        evidence = dict(capture_decision_evidence)
        occ = derive_capture_occurrence_key_from_opportunity(
            activity_instance_id, evidence["opportunity_key"]
        )
    return build_capture_fact(
        activity_instance_id=activity_instance_id,
        occurrence_key=occ,
        captured_at=captured_at,
        capture_kind=capture_kind,
        subject_refs=subject_refs,
        visual_context=vc,
        capture_decision_evidence=evidence,
    )
