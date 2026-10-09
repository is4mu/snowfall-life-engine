"""Source-backed experimental table/planner; unchanged historic goldens."""
from __future__ import annotations

from copy import deepcopy
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from engine.life import ENGINE_VERSION
from engine.life.canonical import canonical_hash, normalize_persisted_object
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.operator_action import load_operator_ledger
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import snapshot_tree_bytes, write_runtime_json
from engine.life.schema import load_schema_document, validate_instance
from tools.persistence_capabilities import capability_table, check_identity_copy, plan_identity_copy

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]
GOLDEN = ROOT / 'tests/fixtures/golden/persistence-v0.1.0'
EXPECTED = json.loads((GOLDEN / 'expected.json').read_text())
ROWS = capability_table()['families']
VERSIONS = {row['family']: 1 for row in ROWS}


def refs():
    return RuntimeReferenceSets(**{k: set(v) for k, v in json.loads((GOLDEN / 'references.json').read_text()).items()})


def plan(root, **changes):
    args = dict(writer='W1-baseline', target_engine=ENGINE_VERSION,
                target_schema_versions=VERSIONS, reference_sets=refs())
    args.update(changes)
    return plan_identity_copy(root, **args)


def copy(root, target):
    return Path(shutil.copytree(root, target))


def select(row, root):
    fixture = row['historic_golden']
    full = json.loads((root / fixture['path']).read_text())
    selected = full
    for key in (fixture['pointer'] or '').strip('/').split('/'):
        if key:
            selected = selected[int(key)] if isinstance(selected, list) else selected[key]
    return full, selected


def test_table_matches_inventory_and_frozen_archive_without_filling_v1_promises():
    inventory = json.loads((ROOT / 'oss/persistence-contract-inventory.json').read_text())
    fixtures = json.loads((GOLDEN / 'artifacts.json').read_text())['artifacts']
    assert [r['historic_golden'] for r in ROWS] == fixtures
    for row, inv in zip(ROWS, inventory['artifacts'], strict=True):
        assert row['family'] == inv['family']
        assert row['decoder'] == inv['reader_anchor']
        assert row['path_pattern'] == inv['relative_path_pattern']
        assert row['embedded_pointer'] == inv['json_pointer']
        assert row['required_for_full_snapshot'] == inv['required_for_full_snapshot']
        assert row['semantic_tree_member'] == inv['included_in_semantic_tree']
        assert row['v1_reader_epoch'] is row['v1_writer_epoch'] is None
        assert inv['v1_oldest_readable_schema_version'] is None
        assert inv['v1_newest_writable_schema_version'] is None
        for anchor in (row['decoder'], row['serializer']):
            module, symbol = anchor.rsplit('.', 1)
            assert callable(getattr(importlib.import_module(module), symbol))
    returned = capability_table()
    returned['families'][0]['accepted_schema_versions'].append(99)
    assert capability_table()['families'][0]['accepted_schema_versions'] == [1]


@pytest.mark.parametrize('row', ROWS, ids=lambda r: r['family'])
def test_family_target_schema_and_serializer_conform_to_pinned_bytes(tmp_path, row):
    # Serializers encode schema-valid documents; generic JSON writer alone is
    # not a validated simulation writer. Test target schema separately.
    full, selected = select(row, GOLDEN / 'runtime')
    assert row['schema_id'] == load_schema_document(row['schema_name'])['$id']
    validate_instance(selected, row['schema_name'])
    assert canonical_hash(normalize_persisted_object(selected)) == row['historic_golden']['canonical_hash']
    module, symbol = row['serializer'].rsplit('.', 1)
    path = tmp_path / 'encoded.json'
    getattr(importlib.import_module(module), symbol)(path, full)
    assert path.read_bytes() == (GOLDEN / 'runtime' / row['historic_golden']['path']).read_bytes()
    for changed in ({**selected, 'schema_version': 99}, {**selected, 'format_envelope': {}}):
        with pytest.raises(LifeEngineError) as caught:
            validate_instance(changed, row['schema_name'])
        assert caught.value.code == ErrorCode.SCHEMA_INVALID


def test_identity_plan_is_detached_and_preserves_golden_evidence(tmp_path):
    source = copy(GOLDEN / 'runtime', tmp_path / 'source')
    candidate = copy(source, tmp_path / 'candidate')
    evidence = plan(source)
    assert evidence['source_evidence'] == EXPECTED
    assert evidence['migration_id'] is evidence['adapter_id'] is None
    assert evidence['stable_v1_guarantee'] is evidence['publication_authorized'] is False
    assert check_identity_copy(source, candidate, plan=evidence, reference_sets=refs())['status'] == 'VERIFIED_BASELINE_IDENTITY_COPY'
    assert snapshot_tree_bytes(source) == snapshot_tree_bytes(candidate) == EXPECTED['file_sha256']


@pytest.mark.parametrize('change', [
    {'writer': 'W2'}, {'writer': None}, {'target_engine': '1.0.0'},
    {'target_engine': '99.0.0-future'}, {'target_schema_versions': {}},
    {'target_schema_versions': {**VERSIONS, 'current_state': 99}},
    {'target_schema_versions': {**VERSIONS, 'current_state': True}},
    {'target_schema_versions': {**VERSIONS, 'current_state': 1.0}},
    {'target_schema_versions': {**VERSIONS, 'unknown_family': 1}},
])
def test_unimplemented_target_rejects_before_source_read_or_registry_call(tmp_path, monkeypatch, change):
    import tools.persistence_capabilities as tool
    from engine.life import versioning
    def forbidden(*args, **kwargs):
        pytest.fail('unapproved source read or migration callback')
    monkeypatch.setattr(tool, 'inspect_persistence', forbidden)
    monkeypatch.setattr(versioning, '_MIGRATIONS', {(ENGINE_VERSION, '1.0.0'): forbidden})
    with pytest.raises(LifeEngineError) as caught:
        plan(tmp_path / 'does-not-exist', **change)
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('change', ['drop', 'duplicate', 'future', 'writer', 'edge', 'schema_id', 'invalid_json'])
def test_corrupt_table_cannot_enable_capabilities(tmp_path, monkeypatch, change):
    import tools.persistence_capabilities as tool
    table = capability_table()
    if change == 'drop': table['families'].pop()
    elif change == 'duplicate': table['families'][-1] = deepcopy(table['families'][0])
    elif change == 'future': table['families'][0]['accepted_schema_versions'] = [99]
    elif change == 'writer': table['first_v1_writer'] = 'W2'
    elif change == 'edge': table['migration_edges'] = [{'to': '1.0.0'}]
    elif change == 'schema_id': table['families'][0]['schema_id'] = 'unknown'
    path = tmp_path / 'table.json'
    path.write_text('{broken' if change == 'invalid_json' else json.dumps(table))
    monkeypatch.setattr(tool, '_TABLE', path)
    with pytest.raises(LifeEngineError): capability_table()


@pytest.mark.parametrize('fault', ['plan', 'source', 'candidate_bytes', 'candidate_metadata', 'manifest', 'engine'])
def test_stale_plan_or_invalid_candidate_preserves_every_input(tmp_path, fault):
    source = copy(GOLDEN / 'runtime', tmp_path / 'source')
    candidate = copy(source, tmp_path / 'candidate')
    evidence = plan(source)
    if fault == 'plan': evidence['publication_authorized'] = True
    elif fault in {'source', 'candidate_bytes'}:
        root = source if fault == 'source' else candidate
        path = root / 'state/current.json'
        path.write_bytes(path.read_bytes() + b'\n')  # same semantic hash; different raw bytes
    elif fault == 'candidate_metadata':
        path = candidate / 'state/last-run.json'
        data = json.loads(path.read_text()); data['state_after_hash'] = '0' * 64
        write_runtime_json(path, data)
    elif fault == 'manifest': (candidate / 'format-manifest.json').write_text('{}')
    elif fault == 'engine':
        path = candidate / 'state/current.json'
        data = json.loads(path.read_text()); data['engine_version'] = '1.0.0'
        write_runtime_json(path, data)
    before = snapshot_tree_bytes(source), snapshot_tree_bytes(candidate)
    with pytest.raises(LifeEngineError):
        check_identity_copy(source, candidate, plan=evidence, reference_sets=refs())
    assert (snapshot_tree_bytes(source), snapshot_tree_bytes(candidate)) == before


def test_nested_stage_is_rejected_before_reads(tmp_path):
    with pytest.raises(LifeEngineError, match='disjoint'):
        check_identity_copy(tmp_path, tmp_path / 'stage', plan={}, reference_sets=refs())


def test_source_change_during_conformance_fails_closed(tmp_path, monkeypatch):
    import tools.persistence_capabilities as tool
    source = copy(GOLDEN / 'runtime', tmp_path / 'source')
    candidate = copy(source, tmp_path / 'candidate')
    evidence = plan(source)
    original = tool.snapshot_tree_bytes
    def changed(root):
        value = original(root)
        if root == source: value['state/current.json'] = '0' * 64
        return value
    monkeypatch.setattr(tool, 'snapshot_tree_bytes', changed)
    with pytest.raises(LifeEngineError, match='changed during conformance'):
        check_identity_copy(source, candidate, plan=evidence, reference_sets=refs())
    assert snapshot_tree_bytes(source) == snapshot_tree_bytes(candidate) == EXPECTED['file_sha256']


def test_operator_history_legacy_read_can_pass_whole_snapshot_without_resealing(tmp_path):
    source = copy(GOLDEN / 'runtime', tmp_path / 'source')
    # Execution summaries are optional. Removing synthetic summaries avoids
    # invalidating their historical tree binding; never reseal them silently.
    for rel in ['state/last-run.json', 'state/last-operator-action.json']:
        (source / rel).unlink()
    path = source / 'state/operator-history.json'
    raw = json.loads(path.read_text()); raw.pop('schema_version')
    write_runtime_json(path, raw)
    before = snapshot_tree_bytes(source)
    assert load_operator_ledger(source).as_history() == EXPECTED['operator_history']
    evidence = plan(source)
    assert evidence['source_evidence']['operator_history'] == EXPECTED['operator_history']
    assert snapshot_tree_bytes(source) == before
    assert 'schema_version' not in json.loads(path.read_text())


def test_plan_restart_and_six_environment_determinism(tmp_path):
    source = copy(GOLDEN / 'runtime', tmp_path / 'source')
    candidate = copy(source, tmp_path / 'candidate')
    expected_plan = plan(source)
    expected_check = check_identity_copy(source, candidate, plan=expected_plan, reference_sets=refs())
    # Paths are not part of evidence; fresh-process replay is bound to pinned
    # golden source hashes rather than a self-regenerated fixture archive.
    script = '''
import json, sys
from pathlib import Path
from engine.life.runtime_bundle import RuntimeReferenceSets
from tools.persistence_capabilities import plan_identity_copy, check_identity_copy, capability_table
raw=json.loads(Path(sys.argv[3]).read_text())
refs=RuntimeReferenceSets(**{k:set(v) for k,v in raw.items()})
p=plan_identity_copy(sys.argv[1], writer='W1-baseline', target_engine='0.1.0-foundation', target_schema_versions={r['family']:1 for r in capability_table()['families']}, reference_sets=refs)
print(json.dumps({'plan':p, 'check':check_identity_copy(sys.argv[1],sys.argv[2],plan=p,reference_sets=refs)},sort_keys=True))
'''
    for seed in ['0', '1', '8675309']:
        for tz in ['UTC', 'Asia/Tokyo']:
            result = subprocess.run([sys.executable, '-c', script, str(source), str(candidate), str(GOLDEN / 'references.json')],
                cwd=ROOT, env={**os.environ, 'PYTHONHASHSEED': seed, 'TZ': tz}, text=True, capture_output=True, check=True)
            assert json.loads(result.stdout) == {'plan': expected_plan, 'check': expected_check}
    assert snapshot_tree_bytes(source) == snapshot_tree_bytes(candidate) == EXPECTED['file_sha256']


def test_symlink_candidate_root_is_rejected(tmp_path):
    source = copy(GOLDEN / 'runtime', tmp_path / 'source')
    candidate = copy(source, tmp_path / 'candidate')
    link = tmp_path / 'link'
    link.symlink_to(candidate, target_is_directory=True)
    with pytest.raises(LifeEngineError, match='symlink roots'):
        check_identity_copy(source, link, plan=plan(source), reference_sets=refs())
    assert snapshot_tree_bytes(source) == snapshot_tree_bytes(candidate) == EXPECTED['file_sha256']
