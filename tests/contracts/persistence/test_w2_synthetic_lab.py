"""Synthetic layout transformation and no-ref failure/recovery evidence only."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from engine.life.errors import LifeEngineError
from engine.life.runtime_persistence import list_runtime_files, snapshot_tree_bytes
from examples.w1_persistence.run import references
from tools import w2_synthetic_lab as lab

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]
EXPECTED = json.loads((ROOT / 'tests/fixtures/golden/w2-lab.experimental.json').read_text())


def source(tmp_path):
    return Path(shutil.copytree(lab.GOLDEN / 'runtime', tmp_path / 'source'))


def test_transformed_actual_target_and_git_objects_match_pinned_proof(tmp_path):
    root = source(tmp_path)
    assert lab.run_lab(root, reference_sets=references()) == EXPECTED
    assert snapshot_tree_bytes(root) == EXPECTED['source_file_sha256']


@pytest.mark.parametrize('failpoint', lab.FAILPOINTS)
def test_interruption_or_invalid_target_cleans_only_owned_temporaries(tmp_path, monkeypatch, failpoint):
    root = source(tmp_path)
    before = snapshot_tree_bytes(root)
    paths = []
    real_temp = lab.TemporaryDirectory
    def tracked_temp(*args, **kwargs):
        temp = real_temp(*args, **kwargs)
        paths.append(Path(temp.name))
        return temp
    monkeypatch.setattr(lab, 'TemporaryDirectory', tracked_temp)
    with pytest.raises((RuntimeError, LifeEngineError)):
        lab.run_lab(root, reference_sets=references(), failpoint=failpoint)
    assert paths and all(not p.exists() for p in paths)
    assert snapshot_tree_bytes(root) == before
    # Restart re-derives exact evidence; there is no mutable resume journal.
    assert lab.run_lab(root, reference_sets=references()) == EXPECTED


@pytest.mark.parametrize('operation', ['copy', 'transform', 'object_write'])
def test_io_or_transform_errors_preserve_source_and_cleanup(tmp_path, monkeypatch, operation):
    root = source(tmp_path)
    before = snapshot_tree_bytes(root)
    paths = []
    real_temp = lab.TemporaryDirectory
    def tracked_temp(*args, **kwargs):
        temp = real_temp(*args, **kwargs)
        paths.append(Path(temp.name))
        return temp
    monkeypatch.setattr(lab, 'TemporaryDirectory', tracked_temp)
    def interrupted(*args, **kwargs):
        raise OSError('synthetic disk/write fault')
    if operation == 'copy': monkeypatch.setattr(lab.shutil, 'copytree', interrupted)
    elif operation == 'transform': monkeypatch.setattr(lab, 'transform', interrupted)
    else: monkeypatch.setattr(lab, 'commit_files', interrupted)
    with pytest.raises(OSError):
        lab.run_lab(root, reference_sets=references())
    assert snapshot_tree_bytes(root) == before
    assert paths and all(not p.exists() for p in paths)


@pytest.mark.parametrize('change', ['format', 'unknown_key', 'missing', 'duplicate', 'path', 'order', 'bytes', 'hash', 'binding', 'type'])
def test_strict_lab_target_rejects_unproved_shape_or_rehash(tmp_path, change):
    root = source(tmp_path)
    proof = lab.source_proof(root, references())
    container = lab.transform(list_runtime_files(root), proof)
    if change == 'format': container['format'] = 'W2-production'
    elif change == 'unknown_key': container['schema_version'] = 2
    elif change == 'missing': container['documents'].pop()
    elif change == 'duplicate': container['documents'].append(deepcopy(container['documents'][0]))
    elif change == 'path': container['documents'][0]['path'] = '../outside.json'
    elif change == 'order': container['documents'].reverse()
    elif change == 'bytes': container['documents'][0]['utf8'] += '\n'
    elif change == 'hash': container['documents'][0]['sha256'] = '0' * 64
    elif change == 'binding': container['source_evidence_hash'] = '0' * 64
    else: container['documents'][0]['utf8'] = {}
    with pytest.raises(LifeEngineError): lab.decode(container, proof)
    assert snapshot_tree_bytes(root) == proof['file_sha256']


@pytest.mark.parametrize('change', ['same_semantics_different_bytes', 'future', 'extra', 'bad_json', 'symlink'])
def test_nonfixture_source_rejects_before_any_stage(tmp_path, monkeypatch, change):
    root = source(tmp_path)
    path = root / 'state/current.json'
    if change == 'same_semantics_different_bytes': path.write_bytes(path.read_bytes() + b'\n')
    elif change == 'future':
        raw = json.loads(path.read_text()); raw['engine_version'] = '99.0.0-future'; path.write_text(json.dumps(raw))
    elif change == 'extra': (root / 'format-manifest.json').write_text('{}')
    elif change == 'bad_json': path.write_text('{broken')
    else:
        link = tmp_path / 'link'; link.symlink_to(root, target_is_directory=True); root = link
    before = snapshot_tree_bytes(root)
    def forbidden(*args, **kwargs): pytest.fail('unsupported source entered staging')
    monkeypatch.setattr(lab, 'TemporaryDirectory', forbidden)
    with pytest.raises(LifeEngineError): lab.run_lab(root, reference_sets=references())
    assert snapshot_tree_bytes(root) == before


def test_source_change_during_stage_rejects_without_repair(tmp_path, monkeypatch):
    root = source(tmp_path)
    real = lab.snapshot_tree_bytes
    def stale(path):
        report = real(path)
        if Path(path) == root: report['state/current.json'] = '0' * 64
        return report
    monkeypatch.setattr(lab, 'snapshot_tree_bytes', stale)
    with pytest.raises(LifeEngineError, match='source changed'):
        lab.run_lab(root, reference_sets=references())
    assert real(root) == EXPECTED['source_file_sha256']


@pytest.mark.parametrize('observed_key,state', [('candidate_commit_sha', 'ALREADY_PUBLISHED'), ('base_commit_sha', 'NOT_PUBLISHED'), (None, 'CONFLICT')])
def test_lost_ack_host_observation_never_publishes_or_retries(tmp_path, observed_key, state):
    root = source(tmp_path)
    observed = EXPECTED[observed_key] if observed_key else 'f' * 40
    result = lab.recover_simulated_ack(root, evidence=EXPECTED, observed_head=observed, reference_sets=references())
    assert result == {'simulation_only': True, 'state': state, 'publication_performed': False,
                      'retry_authorized': False, 'source_unchanged': True}
    assert snapshot_tree_bytes(root) == EXPECTED['source_file_sha256']


def test_self_resealed_receipt_cannot_authorize_recovery(tmp_path):
    root = source(tmp_path)
    altered = deepcopy(EXPECTED)
    altered['candidate_commit_sha'] = 'e' * 40
    from engine.life.canonical import canonical_hash
    altered['proof_hash'] = canonical_hash({k:v for k,v in altered.items() if k != 'proof_hash'})
    with pytest.raises(LifeEngineError, match='receipt'):
        lab.recover_simulated_ack(root, evidence=altered, observed_head='e' * 40, reference_sets=references())


@pytest.mark.parametrize('seed', ['0', '1', '8675309'])
@pytest.mark.parametrize('tz', ['UTC', 'Asia/Tokyo'])
def test_fresh_process_transformation_and_restart_recovery_six_environments(tmp_path, seed, tz):
    root = source(tmp_path)
    script = '''
import json, sys
from examples.w1_persistence.run import references
from tools.w2_synthetic_lab import run_lab, recover_simulated_ack
proof=run_lab(sys.argv[1], reference_sets=references())
print(json.dumps({'proof':proof,'recovered':recover_simulated_ack(sys.argv[1],evidence=proof,observed_head=proof['candidate_commit_sha'],reference_sets=references())},sort_keys=True))
'''
    result = subprocess.run([sys.executable, '-c', script, str(root)], cwd=ROOT,
                            env={**os.environ, 'PYTHONHASHSEED': seed, 'TZ': tz},
                            capture_output=True, text=True, check=True)
    actual = json.loads(result.stdout)
    assert actual['proof'] == EXPECTED
    assert actual['recovered']['state'] == 'ALREADY_PUBLISHED'
    assert snapshot_tree_bytes(root) == EXPECTED['source_file_sha256']


def test_experiment_does_not_enable_engine_versions_or_public_migrations(tmp_path):
    from engine.life import ENGINE_VERSION, SUPPORTED_ENGINE_VERSIONS
    from engine.life import versioning
    from tools.persistence_capabilities import capability_table
    root = source(tmp_path)
    before = deepcopy(capability_table())
    migrations = dict(versioning._MIGRATIONS)
    lab.run_lab(root, reference_sets=references())
    assert capability_table() == before
    assert before['first_v1_writer'] is None and before['migration_edges'] == []
    assert versioning._MIGRATIONS == migrations == {}
    assert ENGINE_VERSION == '0.1.0-foundation'
    assert SUPPORTED_ENGINE_VERSIONS == frozenset({'0.1.0-foundation'})


def test_invalid_failpoint_rejects_before_source_or_stage(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs): pytest.fail('invalid configuration touched source')
    monkeypatch.setattr(lab, 'source_proof', forbidden)
    with pytest.raises(LifeEngineError, match='failpoint'):
        lab.run_lab(tmp_path / 'missing', reference_sets=references(), failpoint='publish')
