"""Checkout-only capability evidence and read-only identity-copy planning.

No v1 writer selection, adapter/migration execution, staging, Git operation or
stable API. W1 here means the proven *baseline* format, not an approved v1 writer.
"""
from __future__ import annotations

import json
from pathlib import Path

from engine.life import ENGINE_VERSION, SUPPORTED_ENGINE_VERSIONS
from engine.life.canonical import canonical_hash
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import snapshot_tree_bytes
from engine.life.schema import load_schema_document
from tools.persistence_preflight import inspect_persistence

_TABLE = Path(__file__).resolve().parents[1] / 'oss/persistence-capabilities.experimental.json'
_BASELINE = '0.1.0-foundation'
_FAMILIES = frozenset({
    'current_state', 'schedule_state', 'relation_state', 'home_state',
    'consumables_state', 'wardrobe_state', 'finance_state', 'social_response_state',
    'timeline_day', 'actual_event', 'camera_roll_shard', 'camera_roll_record',
    'operator_history', 'operator_action', 'runtime_last_run', 'last_operator_action',
})


def _reject(detail: str, *, unsupported: bool = False) -> None:
    raise LifeEngineError(ErrorCode.UNSUPPORTED_VERSION if unsupported else ErrorCode.INVALID_STATE, detail)


def capability_table() -> dict:
    """Fresh detached baseline table; table edits cannot enable new formats."""
    try:
        table = json.loads(_TABLE.read_text(encoding='utf-8'))
        rows = table['families']
        if (ENGINE_VERSION != _BASELINE or SUPPORTED_ENGINE_VERSIONS != frozenset({_BASELINE})
            or table['capability_table_version'] != 1
            or table['status'] != 'experimental-current-format-evidence'
            or table['stable_v1_guarantee'] is not False
            or table['first_v1_writer'] is not None or table['migration_edges'] != []
            or len(rows) != len(_FAMILIES) or {r['family'] for r in rows} != _FAMILIES):
            _reject('capability authority is not the reviewed baseline')
        for row in rows:
            if (row['source_epoch'] != _BASELINE or row['schema_name'] != row['family']
                or row['accepted_schema_versions'] != [1]
                or type(row['accepted_schema_versions'][0]) is not int
                or type(row['oldest_readable_schema_version']) is not int
                or type(row['newest_writable_schema_version']) is not int
                or row['oldest_readable_schema_version'] != 1
                or row['newest_writable_schema_version'] != 1
                or row['explicit_migration_edges'] != []
                or row['v1_reader_epoch'] is not None or row['v1_writer_epoch'] is not None
                or row['schema_id'] != load_schema_document(row['schema_name'])['$id']
                or row['schema_document_hash'] != canonical_hash(load_schema_document(row['schema_name']))):
                _reject('unsupported capability row')
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _reject(f'unreadable capability authority: {exc}')
    return table


def _target(writer: str, target_engine: str, target_schema_versions: dict) -> dict:
    table = capability_table()
    # The mutable engine migration registry is intentionally never consulted.
    if writer != 'W1-baseline' or target_engine != _BASELINE:
        _reject('no implemented/approved target; W2 and v1 remain review gates', unsupported=True)
    if (not isinstance(target_schema_versions, dict)
        or set(target_schema_versions) != _FAMILIES
        or any(type(v) is not int or v != 1 for v in target_schema_versions.values())):
        _reject('target must explicitly name all 16 baseline schema versions', unsupported=True)
    return table


def plan_identity_copy(root: Path | str, *, writer: str, target_engine: str,
                       target_schema_versions: dict,
                       reference_sets: RuntimeReferenceSets) -> dict:
    """Describe a current-format copy only; never execute an operation.

    Reference identity authority is host-supplied, not inferred from saves.
    Callers must keep roots quiescent. Fingerprints are not locks/CAS authority.
    The returned detached plan is evidence, never approval or publication proof.
    """
    table = _target(writer, target_engine, target_schema_versions)
    evidence = inspect_persistence(root, target_engine=target_engine, reference_sets=reference_sets)
    return {
        'status': 'EXPERIMENTAL_IDENTITY_COPY_PLAN', 'writer': writer,
        'source_epoch': evidence['source_engine'], 'target_epoch': target_engine,
        'target_schema_versions': dict(sorted(target_schema_versions.items())),
        'capability_table_hash': canonical_hash(table),
        'references_hash': canonical_hash({k: sorted(v) for k, v in vars(reference_sets).items()}),
        'source_evidence': evidence, 'migration_id': None,
        'adapter_id': None, 'writes_performed': False,
        'publication_authorized': False, 'stable_v1_guarantee': False,
    }


def check_identity_copy(source: Path | str, candidate: Path | str, *, plan: dict,
                        reference_sets: RuntimeReferenceSets) -> dict:
    """Read-only conformance for externally staged byte-identical baseline copy.

    This is deliberately narrower than a real simulation writer or migration:
    no field/format/history/revision change is allowed. It creates no stage,
    commit or ref, and cannot turn a user-provided plan into authorization.
    """
    source, candidate = Path(source), Path(candidate)
    if source.is_symlink() or candidate.is_symlink():
        _reject('symlink roots are not authoritative snapshots')
    source, candidate = source.resolve(), candidate.resolve()
    if source == candidate or source in candidate.parents or candidate in source.parents:
        _reject('source and candidate roots must be disjoint')
    if not isinstance(plan, dict):
        _reject('plan must be detached evidence')
    fresh = plan_identity_copy(source, writer=plan.get('writer'),
        target_engine=plan.get('target_epoch'),
        target_schema_versions=plan.get('target_schema_versions'), reference_sets=reference_sets)
    if fresh != plan:
        _reject('stale or altered plan evidence')
    candidate_report = inspect_persistence(candidate, target_engine=plan['target_epoch'], reference_sets=reference_sets)
    if candidate_report != fresh['source_evidence']:
        _reject('candidate changed bytes, metadata or historical identity')
    # Check both roots again after both validations; ordinary source races reject.
    expected = fresh['source_evidence']['file_sha256']
    if snapshot_tree_bytes(source) != expected or snapshot_tree_bytes(candidate) != expected:
        _reject('source or candidate changed during conformance')
    return {'status': 'VERIFIED_BASELINE_IDENTITY_COPY', 'plan_hash': canonical_hash(fresh),
            'semantic_tree_hash': candidate_report['semantic_tree_hash'],
            'writes_performed': False, 'publication_authorized': False}
