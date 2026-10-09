"""Actual W1 correction writer, saved Git blobs, restart and rejection probes."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from engine.life.errors import LifeEngineError
from engine.life.operator_git_transaction import build_operator_correction_git_transaction
from examples.w1_persistence.run import authority, demo, make_repo, references, request_for

pytestmark = pytest.mark.contract
ROOT = Path(__file__).resolve().parents[3]
EXPECTED = ROOT / 'tests/fixtures/golden/w1-write-reload.experimental.json'


def test_actual_w1_saved_candidate_matches_pinned_hashes_and_restart():
    assert demo() == json.loads(EXPECTED.read_text())


@pytest.mark.parametrize('damage', ['future_engine', 'future_schema', 'history', 'capture', 'supersession'])
def test_writer_rejection_preserves_source_refs_head_and_index(tmp_path, damage):
    repo = make_repo(tmp_path / 'repo')
    request = request_for(repo)
    if damage == 'supersession':
        request['correction']['supersedes_action_id'] = '0' * 64
    else:
        path = repo / ('camera-roll/records/2026-03.json' if damage == 'capture' else 'state/current.json')
        raw = json.loads(path.read_text())
        if damage == 'future_engine': raw['engine_version'] = '99.0.0-future'
        elif damage == 'future_schema': raw['schema_version'] = 99
        elif damage == 'history': raw['history']['history_hash'] = '0' * 64
        else: raw['records'][0]['event_id'] = 'event:orphan'
        # Synthetic corrupt genesis: commit damaged input so failure probes read
        # committed data, rather than merely rejecting an uncommitted worktree.
        path.write_text(json.dumps(raw))
        from examples.w1_persistence.run import git
        git(repo, 'add', '.')
        git(repo, 'commit', '-m', 'Synthetic unsupported or damaged W1 input')
        request['expected_life_head'] = git(repo, 'rev-parse', 'HEAD').decode().strip()
    before = authority(repo)
    with pytest.raises(LifeEngineError):
        build_operator_correction_git_transaction(runtime_repo=repo, request=request, reference_sets=references())
    assert authority(repo) == before


@pytest.mark.parametrize('seed', ['0', '1', '8675309'])
@pytest.mark.parametrize('tz', ['UTC', 'Asia/Tokyo'])
def test_actual_writer_fresh_process_six_environment_replay(seed, tz):
    result = subprocess.run([sys.executable, '-m', 'examples.w1_persistence.run'], cwd=ROOT,
                            env={**os.environ, 'PYTHONHASHSEED': seed, 'TZ': tz},
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == json.loads(EXPECTED.read_text())
