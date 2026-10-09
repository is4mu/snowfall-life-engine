"""Synthetic *full RuntimeBundle* provider-driven persistent candidate example.

No test-builder imports, private character data, network access, Git writes, or
real production activation. Intended as a pre-v1 interface/conformance probe,
not a declaration that these preview canonical imports are stable v1 APIs.

Run from the repository checkout:
    python -m examples.provider_runtime.run
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

from engine.life.activity_materialization import RuntimeMaterializationContext
from engine.life.checkpoint import empty_checkpoint
from engine.life.derived import default_synthetic_sleep_profile
from engine.life.policy import load_policy
from snowfall_life.runtime import (
    RuntimeBundle,
    RuntimeReferenceSets,
    RuntimeDecisionProviderResult,
    RuntimeFactRequestContext,
    RuntimeTargetRequest,
)
from snowfall_life.persistence import (
    build_runtime_candidate_tree_with_provider_factory,
    load_runtime_persistent_snapshot,
)
from engine.life.runtime_decision import RuntimeDecisionFacts, RuntimeDecisionFrame, RuntimeDecisionTrigger
from engine.life.social_runtime import SocialResponseState
# Fixture setup and private test-assertion utilities are deliberately not
# part of the proposed stable public facade.
from engine.life.runtime_persistence import snapshot_tree_bytes, write_runtime_json
from engine.life.schedule_state import build_schedule_state
from engine.life.social_runtime import build_social_response_state
from engine.life.wakeups import FutureCandidate, build_v2_decision_wakeup_event
from engine.life.world_state import (
    project_initial_consumables_state,
    project_initial_finance_state,
    project_initial_home_state,
    project_initial_relation_state,
    project_initial_wardrobe_state,
)

AS_OF = "2026-04-01T12:00:00+09:00"
HORIZON = "2026-04-02T12:00:00+09:00"
CHARACTER = "fixture-character"
ENGINE_SHA = "a" * 40
LIFE_BASE_SHA = "b" * 40
POLICY_FILE = Path(__file__).resolve().parents[2] / "tests/fixtures/policy/v2_synthetic_candidate.json"


def _reference_sets() -> RuntimeReferenceSets:
    return RuntimeReferenceSets(
        known_person_ids=frozenset({"person-a"}),
        known_home_entity_ids=frozenset({"entity-lamp"}),
        approved_wardrobe_item_ids=frozenset({"item-tee"}),
        approved_consumable_ids=frozenset({"food-staple"}),
    )


def _write_baseline(root: Path, policy_version: str) -> None:
    """Create eight valid synthetic runtime documents without test scaffolding."""
    refs = _reference_sets()
    provenance = {"origin": "SIMULATION_BOOTSTRAP", "notes": "synthetic-provider-consumer"}
    relation = project_initial_relation_state(
        character_id=CHARACTER,
        as_of=AS_OF,
        provenance=provenance,
        known_person_ids=refs.known_person_ids,
        people=[{"person_id": "person-a", "open_promises": []}],
    )
    home = project_initial_home_state(
        character_id=CHARACTER,
        as_of=AS_OF,
        provenance=provenance,
        known_entity_ids=refs.known_home_entity_ids,
        entities=[{"entity_id": "entity-lamp", "status": "NORMAL"}],
    )
    consumables = project_initial_consumables_state(
        character_id=CHARACTER,
        as_of=AS_OF,
        provenance=provenance,
        approved_consumable_ids=refs.approved_consumable_ids,
        stocks=[{"consumable_id": "food-staple", "status": "AVAILABLE"}],
    )
    wardrobe = project_initial_wardrobe_state(
        character_id=CHARACTER,
        as_of=AS_OF,
        provenance=provenance,
        approved_item_ids=refs.approved_wardrobe_item_ids,
        items=[{
            "item_id": "item-tee",
            "lifecycle_state": "CLEAN_AVAILABLE",
            "wear_count_since_clean": 0,
        }],
    )
    finance = project_initial_finance_state(
        character_id=CHARACTER,
        as_of=AS_OF,
        provenance=provenance,
        balance_minor=None,
    )
    schedule = build_schedule_state(character_id=CHARACTER, as_of=AS_OF)
    social = build_social_response_state(character_id=CHARACTER, as_of=AS_OF)

    state = empty_checkpoint(
        character_id=CHARACTER,
        life_epoch="2026-04-01T00:00:00+09:00",
        world_seed="public-provider-example-seed",
        behavior_policy_version=policy_version,
        engine_commit_sha=ENGINE_SHA,
        location_id="fixture-home",
        human_state={
            "sleep_debt_min": 0,
            "hunger": 200,
            "physical_fatigue": 100,
            "affect_valence": 0,
            "stress": 100,
            "social_battery": 800,
        },
    )
    state["processed_through"] = AS_OF
    state["domain_refs"] = {
        "home_revision": home["revision"],
        "social_graph_revision": None,
        "finance_revision": finance["revision"],
        "wardrobe_revision": wardrobe["revision"],
        "relation_state_revision": relation["revision"],
        "consumables_revision": consumables["revision"],
        "schedule_revision": schedule["revision"],
        "schedule_hash": schedule["state_hash"],
        "social_response_revision": social.revision,
    }
    wakeup = build_v2_decision_wakeup_event(
        character_id=CHARACTER,
        candidate=FutureCandidate(
            due_at=AS_OF,
            trigger_kind="IDLE",
            decision_key="idle:example-household",
            reason_code="IDLE_REASSESSMENT",
            metric=None,
            target_band=None,
            direction=None,
            source_kind=None,
            source_ref=None,
        ),
    )
    state["pending_queue"] = {"cursor": 0, "events": [wakeup]}

    for relative, document in (
        ("state/current.json", state),
        ("schedule/state.json", schedule),
        ("relations/state.json", relation),
        ("home/state.json", home),
        ("consumables/state.json", consumables),
        ("wardrobe/state.json", wardrobe),
        ("finance/state.json", finance),
        ("state/social-responses.json", social.as_dict()),
    ):
        write_runtime_json(root / relative, document)


def _decision_facts() -> dict[str, Any]:
    """Application-supplied, wholly synthetic facts for one HOUSEHOLD choice."""
    return {
        "available_window_min": 120,
        "route_profiles": [],
        "structural_physical_feasible_by_action": {
            "WORK": True,
            "HARD_COMMITMENT_ACTIVITY": True,
            "TRAVEL_TO_COMMITMENT": True,
            "SOCIAL_PROMISE": True,
            "ERRAND": True,
            "STUDY": True,
        },
        "household_tasks": [{
            "household_task_id": "hh-1",
            "created_at": "2026-03-30T12:00:00+09:00",
            "status": "OPEN",
            "location_id": "fixture-home",
        }],
        "kickboxing_sessions": [],
        "routine_physical_feasible_by_action": {
            "HOUSEHOLD": True, "KICKBOXING": False,
        },
        "kickboxing_location_feasible": False,
        "kickboxing_self_generated_today": 0,
        "kickboxing_equivalent_pending_plans": 0,
        "continuation_feasible": None,
        "meal_feasible": False,
        "rest_feasible": False,
        "sleep_pressure": 200,
        "sleep_opportunity": "NORMAL",
        "last_meal_at": None,
        "meal_extent": None,
        "soft_guards": [],
        "free_window_key": None,
        "free_window_min": None,
        "feasible_leisure_categories": [],
    }


class SyntheticFactProvider:
    """An explicit call-local data provider; no external authority or I/O."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.context_hashes: list[str] = []

    def _record(self, operation: str, request_context: RuntimeFactRequestContext) -> None:
        self.calls.append(operation)
        self.context_hashes.append(request_context.history_context_hash)
        if request_context.finalized_events_since_base:
            raise AssertionError("this synthetic one-trigger example has no finalized history")

    def decision_inputs(
        self,
        *,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        trigger: RuntimeDecisionTrigger,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        target_request: RuntimeTargetRequest,
        request_context: RuntimeFactRequestContext,
    ) -> RuntimeDecisionProviderResult:
        self._record("decision_inputs", request_context)
        return RuntimeDecisionProviderResult(
            decision_facts=_decision_facts(),
            social_facts={
                "archetype_assignments": [],
                "contact_facts": [],
                "post_work_contexts": [],
                "availability_windows": [],
                "hard_commitment_blocking_now": False,
                "social_contact_physical_feasible": False,
                "post_work_location_feasible": False,
            },
        )

    def materialization_facts(
        self,
        *,
        bundle: RuntimeBundle,
        social_response_state: SocialResponseState,
        frame: RuntimeDecisionFrame,
        context: RuntimeMaterializationContext,
        decision_facts: RuntimeDecisionFacts | Mapping[str, Any],
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        target_request: RuntimeTargetRequest,
        request_context: RuntimeFactRequestContext,
    ) -> Mapping[str, Any]:
        self._record("materialization_facts", request_context)
        if frame.selected_candidate is None:
            raise AssertionError("expected one selected household candidate")
        if frame.selected_candidate.action_kind != "HOUSEHOLD":
            raise AssertionError("synthetic input should select HOUSEHOLD")
        if context.pending_immediate_accept is not None:
            raise AssertionError("synthetic household path cannot accept social invitation")
        return {
            "selected_candidate_id": frame.selected_candidate.candidate_id,
            "base_activity_class": "LIGHT_ACTIVE",
            "social_exposure": "NONE",
            "meal_source": None,
            "meal_extent": None,
            "social_contact_mode": None,
            "sleep_profile": None,
        }

    def wakeup_facts(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        as_of: str,
        behavior_policy: Mapping[str, Any],
        reference_sets: RuntimeReferenceSets,
        target_request: RuntimeTargetRequest,
        request_context: RuntimeFactRequestContext,
    ) -> Mapping[str, Any]:
        self._record("wakeup_facts", request_context)
        return {
            "sleep_profile": dict(default_synthetic_sleep_profile()._asdict()),
            "elapsed_since_last_main_sleep_end_seconds": 8 * 3600,
            "total_nap_awake_credit_min": 0,
            "fatigue_high_relevant": False,
            "stress_relevant": False,
            "social_low_relevant": False,
            "known_boundaries": [],
            "projection_horizon_end": HORIZON,
        }

    def capture_source_moments(
        self,
        *,
        bundle: RuntimeBundle,
        active_activity: Mapping[str, Any],
        from_time: str,
        target_time: str,
        character_source_sha: str,
        target_request: RuntimeTargetRequest,
    ) -> Sequence[Mapping[str, Any]]:
        self.calls.append("capture_source_moments")
        return ()


def _stage_once(
    *, baseline_root: Path, candidate_root: Path, behavior_policy: Mapping[str, Any]
) -> tuple[Any, SyntheticFactProvider]:
    created: list[SyntheticFactProvider] = []

    def provider_factory(snapshot: Any) -> SyntheticFactProvider:
        # The factory gets the exact fully verified frozen base snapshot.
        if snapshot.bundle.current_state["character_id"] != CHARACTER:
            raise AssertionError("provider factory received a different base")
        provider = SyntheticFactProvider()
        created.append(provider)
        return provider

    result = build_runtime_candidate_tree_with_provider_factory(
        baseline_root=baseline_root,
        candidate_root=candidate_root,
        reference_sets=_reference_sets(),
        behavior_policy=behavior_policy,
        target_request=RuntimeTargetRequest(
            target_time=AS_OF,
            event_budget=20,
            character_source_sha="synthetic-character-source-id",
        ),
        fact_provider_factory=provider_factory,
        executing_engine_commit_sha=ENGINE_SHA,
        life_base_sha=LIFE_BASE_SHA,
    )
    if len(created) != 1:
        raise AssertionError("provider must be constructed once per candidate")
    return result, created[0]


def demo() -> dict[str, Any]:
    """Stage twice from identical baseline, proving replay and immutability."""
    with TemporaryDirectory(prefix="snowfall-provider-example-") as tmp:
        work = Path(tmp)
        policy = load_policy(POLICY_FILE)
        if policy["character_id"] != CHARACTER:
            raise AssertionError("fixture policy character identity mismatch")
        baseline = work / "baseline"
        _write_baseline(baseline, policy["behavior_policy_version"])

        snapshot = load_runtime_persistent_snapshot(
            baseline, reference_sets=_reference_sets()
        )
        original_bytes = snapshot_tree_bytes(baseline)
        if snapshot.bundle.current_state["state_revision"] != 0:
            raise AssertionError("baseline revision must start at zero")

        first, first_provider = _stage_once(
            baseline_root=baseline, candidate_root=work / "candidate-a",
            behavior_policy=policy,
        )
        second, second_provider = _stage_once(
            baseline_root=baseline, candidate_root=work / "candidate-b",
            behavior_policy=policy,
        )
        if first.status != "CHANGE" or second.status != "CHANGE":
            raise AssertionError("one due decision must produce CHANGE")
        if snapshot_tree_bytes(baseline) != original_bytes:
            raise AssertionError("provider candidate staging mutated baseline")
        if first.state_after_hash != second.state_after_hash:
            raise AssertionError("same authority and facts must replay identically")
        if snapshot_tree_bytes(work / "candidate-a") != snapshot_tree_bytes(work / "candidate-b"):
            raise AssertionError("replay candidate files are not byte-identical")
        if first.candidate_snapshot is None or second.candidate_snapshot is None:
            raise AssertionError("CHANGE must be independently reloadable")
        if first.candidate_snapshot.bundle.current_state["state_revision"] != 1:
            raise AssertionError("CHANGE must bump revision exactly once")

        required_calls = {
            "decision_inputs",
            "materialization_facts",
            "wakeup_facts",
            "capture_source_moments",
        }
        for provider in (first_provider, second_provider):
            if not required_calls.issubset(set(provider.calls)):
                raise AssertionError(f"provider did not exercise all callbacks: {provider.calls}")
            if provider.context_hashes != [RuntimeFactRequestContext().history_context_hash] * 3:
                raise AssertionError("provider context hash is not call-local and stable")
        if first_provider.calls != second_provider.calls:
            raise AssertionError("provider callback order changed across replay")
        return {
            "status": "PASS",
            "scope": "full-provider-runtime-candidate",
            "result": first.status,
            "engine_version": snapshot.bundle.current_state["engine_version"],
            "state_revision_after": first.candidate_snapshot.bundle.current_state["state_revision"],
            "semantic_tree_hash": first.state_after_hash,
            "callback_sequence": first_provider.calls,
            "baseline_unchanged": True,
            "byte_identical_replay": True,
        }


def main() -> int:
    print(json.dumps(demo(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
