"""Frozen current-format evidence and fail-closed migration inspection/replay.

No approved old->new migration exists; these tests cannot claim v1 support.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from engine.life import ENGINE_VERSION
from engine.life.canonical import canonical_hash, normalize_persisted_object
from engine.life.checkpoint import load_checkpoint, write_checkpoint
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import list_runtime_files, write_runtime_json
from engine.life.schema import validate_instance
from engine.life.versioning import migrate_state
from tools.persistence_preflight import inspect_persistence

pytestmark = pytest.mark.contract
GOLDEN = Path(__file__).resolve().parents[2] / 'fixtures/golden/persistence-v0.1.0'
ROWS = json.loads((GOLDEN / 'artifacts.json').read_text())['artifacts']
EXPECTED = json.loads((GOLDEN / 'expected.json').read_text())


def refs():
    raw = json.loads((GOLDEN / 'references.json').read_text())
    return RuntimeReferenceSets(**{key: set(value) for key, value in raw.items()})


def copy_source(tmp_path):
    return Path(shutil.copytree(GOLDEN / 'runtime', tmp_path / 'source'))


def inspect(root, **kwargs):
    return inspect_persistence(root, target_engine=ENGINE_VERSION, reference_sets=refs(), **kwargs)


def document(root, row):
    full = json.loads((root / row['path']).read_text())
    selected = full
    if row['pointer']:
        for key in row['pointer'].strip('/').split('/'):
            selected = selected[int(key)] if isinstance(selected, list) else selected[key]
    return full, selected


def test_golden_covers_exactly_the_current_inventory():
    inventory = json.loads((GOLDEN.parents[3] / 'oss/persistence-contract-inventory.json').read_text())
    assert {(r['family'], r['schema_name']) for r in ROWS} == {
        (r['family'], r['schema_name']) for r in inventory['artifacts']
    }
    assert len(ROWS) == 16
    assert EXPECTED['migration_available'] is False
    assert EXPECTED['source_engine'] == '0.1.0-foundation'
    assert EXPECTED['target_engine'] == '0.1.0-foundation'


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['family'])
def test_serialized_family_schema_and_pinned_canonical_digest(row):
    _, selected = document(GOLDEN / 'runtime', row)
    validate_instance(selected, row['schema_name'])
    assert selected['schema_version'] == 1
    assert canonical_hash(normalize_persisted_object(selected)) == row['canonical_hash']


def test_current_format_roundtrip_is_byte_stable_and_preserves_link_vectors(tmp_path):
    root = copy_source(tmp_path)
    before = list_runtime_files(root)
    assert inspect(root) == EXPECTED
    # Current checkpoint reader + writer, followed by all runtime JSON writes,
    # must preserve the frozen serialized representations and their link vectors.
    current = load_checkpoint(root / 'state/current.json')
    untouched = deepcopy(current)
    migrated = migrate_state(deepcopy(current), target_engine=ENGINE_VERSION)
    assert migrated == untouched
    write_checkpoint(root / 'state/current.json', migrated)
    for rel in before:
        write_runtime_json(root / rel, json.loads((root / rel).read_text()))
    assert list_runtime_files(root) == before
    assert inspect(root) == EXPECTED
    assert len(EXPECTED['event_ids']) == len(EXPECTED['capture_ids']) == 1
    assert len(EXPECTED['action_ids']) == 2
    assert EXPECTED['correction_lineage'][1]['supersedes_action_id'] == EXPECTED['action_ids'][0]
    assert EXPECTED['correction_lineage'][0]['target_event_id'] == EXPECTED['event_ids'][0]


@pytest.mark.parametrize('row', ROWS, ids=lambda row: row['family'])
def test_every_future_schema_rejects_without_source_writes(tmp_path, row):
    root = copy_source(tmp_path)
    full, selected = document(root, row)
    selected['schema_version'] = 99
    write_runtime_json(root / row['path'], full)
    before = list_runtime_files(root)
    with pytest.raises(LifeEngineError):
        inspect(root)
    assert list_runtime_files(root) == before


@pytest.mark.parametrize('version', ['99.0.0-future', '0.0.0-unknown'])
def test_unsupported_source_engine_rejects_without_writes(tmp_path, version):
    root = copy_source(tmp_path)
    path = root / 'state/current.json'
    raw = json.loads(path.read_text()); raw['engine_version'] = version
    write_runtime_json(path, raw)
    before = list_runtime_files(root)
    with pytest.raises(LifeEngineError) as caught:
        inspect(root)
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert list_runtime_files(root) == before


def test_unapproved_target_never_invokes_registered_migration(tmp_path, monkeypatch):
    from engine.life import versioning
    root = copy_source(tmp_path)
    before = list_runtime_files(root)
    calls = []
    monkeypatch.setattr(versioning, '_MIGRATIONS', {
        (ENGINE_VERSION, '1.0.0-unapproved'): lambda state: calls.append(state)
    })
    with pytest.raises(LifeEngineError) as caught:
        inspect_persistence(root, target_engine='1.0.0-unapproved', reference_sets=refs())
    assert caught.value.code == ErrorCode.UNSUPPORTED_VERSION
    assert calls == []
    assert list_runtime_files(root) == before


@pytest.mark.parametrize('rel,field,value', [
    ('state/current.json', 'history.history_hash', '0' * 64),
    ('state/operator-history.json', 'operator_history_hash', '0' * 64),
    ('state/last-run.json', 'state_after_hash', '0' * 64),
    ('state/last-operator-action.json', 'state_after_hash', '0' * 64),
    ('camera-roll/records/2026-03.json', 'records.0.event_id', 'event:orphan'),
    ('timeline/2026-03-15.json', 'actual_events.0.summary', 'tampered'),
])
def test_integrity_failure_preserves_corrupt_source_bytes(tmp_path, rel, field, value):
    root = copy_source(tmp_path)
    raw = json.loads((root / rel).read_text())
    cursor = raw
    keys = field.split('.')
    for key in keys[:-1]:
        cursor = cursor[int(key)] if isinstance(cursor, list) else cursor[key]
    cursor[keys[-1]] = value
    write_runtime_json(root / rel, raw)
    before = list_runtime_files(root)
    with pytest.raises(LifeEngineError):
        inspect(root)
    assert list_runtime_files(root) == before


def test_operator_missing_version_exception_is_read_only(tmp_path):
    root = copy_source(tmp_path)
    path = root / 'state/operator-history.json'
    raw = json.loads(path.read_text()); raw.pop('schema_version')
    write_runtime_json(path, raw)
    before = list_runtime_files(root)
    from engine.life.operator_action import load_operator_ledger
    # Legacy ledger normalization remains supported. Removing a version from
    # a hash-bound snapshot nevertheless changes its semantic tree identity.
    assert load_operator_ledger(root).as_history() == EXPECTED['operator_history']
    with pytest.raises(LifeEngineError, match='state_after_hash mismatch'):
        inspect(root)
    assert list_runtime_files(root) == before


def test_source_change_between_verification_and_report_fails_closed(tmp_path, monkeypatch):
    import tools.persistence_preflight as tool
    root = copy_source(tmp_path)
    original = tool.snapshot_tree_bytes
    calls = 0
    def changed(path):
        nonlocal calls
        calls += 1
        fingerprint = original(path)
        if calls == 2:
            fingerprint['state/current.json'] = '0' * 64
        return fingerprint
    monkeypatch.setattr(tool, 'snapshot_tree_bytes', changed)
    before = list_runtime_files(root)
    with pytest.raises(LifeEngineError, match='source changed during preflight'):
        inspect(root)
    assert list_runtime_files(root) == before


@pytest.mark.parametrize('case', ['missing', 'unexpected', 'malformed', 'symlink'])
def test_invalid_filesystem_inputs_fail_closed(tmp_path, case):
    root = copy_source(tmp_path)
    if case == 'missing':
        (root / 'finance/state.json').unlink()
    elif case == 'unexpected':
        (root / 'unapproved.txt').write_text('unexpected')
    elif case == 'malformed':
        (root / 'finance/state.json').write_text('{bad json')
    else:
        (root / 'linked.json').symlink_to(GOLDEN / 'runtime/state/current.json')
    before = list_runtime_files(root)
    with pytest.raises(LifeEngineError):
        inspect(root)
    assert list_runtime_files(root) == before


@pytest.mark.parametrize('seed', ['0', '1', '8675309'])
@pytest.mark.parametrize('tz', ['UTC', 'Asia/Tokyo'])
def test_fresh_process_preflight_and_staged_copy_replay(tmp_path, seed, tz):
    root = copy_source(tmp_path)
    staged = Path(shutil.copytree(root, tmp_path / 'staged'))
    before = list_runtime_files(root)
    command = [sys.executable, '-m', 'tools.persistence_preflight', '--target-engine',
               ENGINE_VERSION, '--references', str(GOLDEN / 'references.json'), '--root']
    env = dict(os.environ, PYTHONHASHSEED=seed, TZ=tz)
    source = subprocess.run(command + [str(root)], env=env, check=True, capture_output=True)
    replay = subprocess.run(command + [str(staged)], env=env, check=True, capture_output=True)
    assert source.stdout == replay.stdout
    assert json.loads(source.stdout) == EXPECTED
    assert list_runtime_files(root) == before
    assert list_runtime_files(staged) == before


def test_cli_unknown_target_returns_rejection_without_source_writes(tmp_path):
    root = copy_source(tmp_path)
    before = list_runtime_files(root)
    result = subprocess.run([sys.executable, '-m', 'tools.persistence_preflight',
        '--root', str(root), '--references', str(GOLDEN / 'references.json'),
        '--target-engine', '1.0.0-unapproved'], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['code'] == ErrorCode.UNSUPPORTED_VERSION.value
    assert list_runtime_files(root) == before


@pytest.mark.parametrize('damage', ['target_hash', 'supersession'])
def test_hash_valid_operator_chain_with_invalid_correction_is_rejected(tmp_path, damage):
    from engine.life.operator_action import build_operator_action_from_request, operator_action_relpath
    from engine.life.operator_history import advance_operator_history, empty_operator_history
    from engine.life.runtime_persistence import load_runtime_persistent_snapshot
    root = copy_source(tmp_path)
    first = json.loads((root / ('operator/actions/' + EXPECTED['action_ids'][0] + '.json')).read_text())
    correction = deepcopy(first['correction'])
    if damage == 'target_hash':
        correction['target_event_hash'] = '0' * 64
    else:
        correction['supersedes_action_id'] = '0' * 64
    action = build_operator_action_from_request({
        'schema_version': 1, 'request_type': 'CORRECTION',
        'expected_life_head': '1' * 40, 'effective_at': first['effective_at'],
        'approval_ref': 'fixture:invalid-correction', 'correction': correction,
    }, life_base_sha='1' * 40, previous_action_id=None)
    for path in (root / 'operator/actions').glob('*.json'):
        path.unlink()
    write_runtime_json(root / operator_action_relpath(action['action_id']), action)
    write_runtime_json(root / 'state/operator-history.json',
                       advance_operator_history(empty_operator_history(), action))
    # Remove execution metadata bindings in this deliberately synthetic damage
    # case to isolate correction semantics after schema/hash-chain validation.
    for rel in ('state/last-run.json', 'state/last-operator-action.json'):
        (root / rel).unlink()
    current = json.loads((root / 'state/current.json').read_text())
    current['state_revision'] = 0
    write_runtime_json(root / 'state/current.json', current)
    load_runtime_persistent_snapshot(root, reference_sets=refs())
    before = list_runtime_files(root)
    with pytest.raises(LifeEngineError, match='target_event_hash mismatch|supersedes_action_id'):
        inspect(root)
    assert list_runtime_files(root) == before
