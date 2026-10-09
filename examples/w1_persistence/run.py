"""Pre-v1 W1 write/reload probe using only disposable synthetic save data.

Runs against installed engine code under `python -I`; no test/tool imports.
Creates an unreferenced correction commit in a temporary repository only.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

import engine.life
from engine.life import ENGINE_VERSION
from engine.life.canonical import canonical_hash
from engine.life.correction import apply_correction_overlays
from engine.life.history import collect_ledger_events
from engine.life.operator_action import load_operator_ledger
from engine.life.operator_git_transaction import build_operator_correction_git_transaction
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import load_runtime_persistent_snapshot, snapshot_tree_bytes
from engine.life.schema import validate_instance

GOLDEN = Path(__file__).resolve().parents[2] / 'tests/fixtures/golden/persistence-v0.1.0'


def references():
    raw = json.loads((GOLDEN / 'references.json').read_text())
    return RuntimeReferenceSets(**{k: set(v) for k, v in raw.items()})


def git(repo, *args):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic W1',
           'GIT_AUTHOR_EMAIL': 'w1@example.invalid', 'GIT_COMMITTER_NAME': 'Synthetic W1',
           'GIT_COMMITTER_EMAIL': 'w1@example.invalid',
           'GIT_AUTHOR_DATE': '1776222000 +0900', 'GIT_COMMITTER_DATE': '1776222000 +0900'}
    return subprocess.run(['git', '-C', str(repo), '-c', 'core.autocrlf=false',
                           '-c', 'commit.gpgsign=false', *args], env=env,
                          check=True, capture_output=True).stdout


def make_repo(path):
    shutil.copytree(GOLDEN / 'runtime', path)
    git(path, 'init', '--initial-branch=synthetic-runtime', '--object-format=sha1')
    git(path, 'add', '.')
    git(path, 'commit', '-m', 'Synthetic frozen W1 baseline')
    return path


def authority(repo):
    return (git(repo, 'rev-parse', 'HEAD'), git(repo, 'symbolic-ref', 'HEAD'),
            git(repo, 'for-each-ref', '--format=%(refname) %(objectname)'),
            (repo / '.git/index').read_bytes(),
            {k: v for k, v in snapshot_tree_bytes(repo).items() if not k.startswith('.git/')})


def request_for(repo):
    expected = json.loads((GOLDEN / 'expected.json').read_text())
    prior = json.loads((repo / ('operator/actions/' + expected['action_ids'][-1] + '.json')).read_text())
    correction = deepcopy(prior['correction'])
    correction['supersedes_action_id'] = prior['action_id']
    correction['corrected_event']['summary'] = 'synthetic W1 writer correction 3'
    return {'schema_version': 1, 'request_type': 'CORRECTION',
            'expected_life_head': git(repo, 'rev-parse', 'HEAD').decode().strip(),
            'effective_at': prior['effective_at'], 'approval_ref': 'fixture:w1-write-reload',
            'correction': correction}


def materialize_commit(repo, commit, destination):
    destination.mkdir()
    # Only verified runtime JSON blobs are exported, without checkout/ref updates.
    for rel in git(repo, 'ls-tree', '-r', '--name-only', commit).decode().splitlines():
        path = destination / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(git(repo, 'show', f'{commit}:{rel}'))


def read_report(root):
    before = snapshot_tree_bytes(root)
    snapshot = load_runtime_persistent_snapshot(root, reference_sets=references())
    events = collect_ledger_events(snapshot.timeline_days.values())
    ledger = load_operator_ledger(root)
    effective = apply_correction_overlays(events, ledger)
    assert snapshot_tree_bytes(root) == before
    return {'file_sha256': before, 'semantic_tree_hash': snapshot.semantic_tree_hash,
            'history': dict(snapshot.bundle.current_state['history']),
            'operator_history': ledger.as_history(),
            'effective_events_hash': canonical_hash(list(effective)),
            'event_ids': [e['event_id'] for e in events],
            'capture_ids': sorted(r['capture_id'] for s in snapshot.camera_roll_shards.values() for r in s['records']),
            'action_ids': [a['action_id'] for a in ledger.actions],
            'state_revision': snapshot.bundle.current_state['state_revision']}


def demo():
    expected = json.loads((GOLDEN / 'expected.json').read_text())
    assert snapshot_tree_bytes(GOLDEN / 'runtime') == expected['file_sha256']
    with TemporaryDirectory(prefix='snowfall-w1-save-') as tmp:
        repo = make_repo(Path(tmp) / 'repo')
        before = authority(repo)
        request = request_for(repo)
        first = build_operator_correction_git_transaction(runtime_repo=repo, request=request, reference_sets=references())
        second = build_operator_correction_git_transaction(runtime_repo=repo, request=request, reference_sets=references())
        assert first.status == second.status == 'CHANGE'
        assert first.candidate_commit_sha == second.candidate_commit_sha
        assert authority(repo) == before
        commit = first.candidate_commit_sha
        assert git(repo, 'rev-list', '--parents', '-n', '1', commit).decode().split() == [commit, request['expected_life_head']]
        assert not git(repo, 'for-each-ref', '--contains', commit).strip()
        saved = Path(tmp) / 'saved'
        materialize_commit(repo, commit, saved)
        report = read_report(saved)
        # Reload in an isolated fresh interpreter using the same installed code.
        reader = (['-m', 'examples.w1_persistence.run']
                  if Path(engine.life.__file__).resolve().is_relative_to(GOLDEN.parents[3])
                  else ['-I', str(Path(__file__).resolve())])
        restarted = subprocess.run([os.sys.executable, *reader, '--read', str(saved)],
                                   check=True, capture_output=True, text=True)
        assert json.loads(restarted.stdout) == report
        assert report['history'] == expected['history']
        assert report['event_ids'] == expected['event_ids']
        assert report['capture_ids'] == expected['capture_ids']
        assert report['action_ids'][:-1] == expected['action_ids']
        assert report['operator_history']['action_count'] == 3
        assert report['state_revision'] == 3
        assert report['semantic_tree_hash'] == first.state_after_hash
        assert report['effective_events_hash'] != expected['effective_events_hash']
        new_action = json.loads((saved / ('operator/actions/' + first.action_id + '.json')).read_text())
        assert new_action['previous_action_id'] == expected['action_ids'][-1]
        assert new_action['correction']['supersedes_action_id'] == expected['action_ids'][-1]
        for rel, digest in expected['file_sha256'].items():
            if rel.startswith(('timeline/', 'camera-roll/', 'operator/actions/')):
                assert report['file_sha256'][rel] == digest
        state = json.loads((saved / 'state/current.json').read_text())
        assert state['engine_version'] == ENGINE_VERSION == '0.1.0-foundation'
        for row in json.loads((GOLDEN / 'artifacts.json').read_text())['artifacts']:
            selected = json.loads((saved / row['path']).read_text())
            for key in (row['pointer'] or '').strip('/').split('/'):
                if key:
                    selected = selected[int(key)] if isinstance(selected, list) else selected[key]
            validate_instance(selected, row['schema_name'])
            assert selected['schema_version'] == 1
        validate_instance(new_action, 'operator_action')
        assert new_action['schema_version'] == 1
        assert authority(repo) == before
    assert snapshot_tree_bytes(GOLDEN / 'runtime') == expected['file_sha256']
    return {'status': 'PASS', 'scope': 'pre-v1-W1-write-reload',
            'candidate_commit_sha': commit, 'candidate_tree_sha': first.candidate_tree_sha,
            **report, 'source_unchanged': True, 'authority_unchanged': True,
            'restart_identical': True, 'unreferenced_candidate': True,
            'stable_v1_guarantee': False}


if __name__ == '__main__':
    import sys
    print(json.dumps(read_report(Path(sys.argv[2])) if len(sys.argv) == 3 and sys.argv[1] == '--read' else demo(), sort_keys=True))
