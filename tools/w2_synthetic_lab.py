"""Checkout-only synthetic W2 transaction experiment; no production format/API.

Only byte-identical copies of the public frozen synthetic fixture are accepted.
The lab container is a hypothetical transport representation OUTSIDE runtime
paths, schemas and the migration registry. It is never an engine-readable save.
All stages, decoded copies and unreferenced Git objects are owned temporaries.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from engine.life import ENGINE_VERSION
from engine.life.canonical import canonical_bytes, canonical_hash
from engine.life.errors import ErrorCode, LifeEngineError
from engine.life.runtime_bundle import RuntimeReferenceSets
from engine.life.runtime_persistence import list_runtime_files, snapshot_tree_bytes
from tools.persistence_preflight import inspect_persistence

GOLDEN = Path(__file__).resolve().parents[1] / 'tests/fixtures/golden/persistence-v0.1.0'
LAB_FORMAT = 'TEST_ONLY_W2_CONTAINER_V2'
FAILPOINTS = ('after_stage', 'after_transform', 'invalid_target', 'before_commit', 'after_commit', 'before_exit')


def reject(detail):
    raise LifeEngineError(ErrorCode.INVALID_STATE, detail)


def source_proof(source, references):
    source = Path(source)
    if source.is_symlink():
        reject('lab source root may not be a symlink')
    expected = json.loads((GOLDEN / 'expected.json').read_text())
    proof = inspect_persistence(source, target_engine=ENGINE_VERSION, reference_sets=references)
    if proof != expected:
        reject('W2 lab accepts only the pinned public synthetic archive; no real saves')
    return proof


def transform(files, proof):
    # Real layout transformation: per-path runtime documents → sorted records
    # in one lab-only container. Keep exact UTF-8 source bytes, including indent
    # and metadata, so historical hashes never need relinking or resealing.
    return {'format': LAB_FORMAT, 'source_evidence_hash': canonical_hash(proof),
            'documents': [{'path': rel, 'utf8': raw.decode('utf-8'),
                           'sha256': hashlib.sha256(raw).hexdigest()}
                          for rel, raw in sorted(files.items())]}


def decode(container, proof):
    if (not isinstance(container, dict)
        or set(container) != {'format', 'source_evidence_hash', 'documents'}
        or container['format'] != LAB_FORMAT
        or container['source_evidence_hash'] != canonical_hash(proof)
        or not isinstance(container['documents'], list)):
        reject('unknown or invalid lab container')
    expected = proof['file_sha256']
    files = {}
    for row in container['documents']:
        if (not isinstance(row, dict) or set(row) != {'path', 'utf8', 'sha256'}
            or not isinstance(row['path'], str) or row['path'] not in expected
            or row['path'] in files or not isinstance(row['utf8'], str)):
            reject('invalid/duplicate/unapproved lab document')
        raw = row['utf8'].encode('utf-8')
        if hashlib.sha256(raw).hexdigest() != row['sha256'] or row['sha256'] != expected[row['path']]:
            reject('lab target changed original bytes or hashes')
        files[row['path']] = raw
    if list(files) != sorted(expected):
        reject('lab target must contain the exact ordered original file set')
    return files


def git(repo, *args, data=None, index=None):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 'Synthetic W2 lab',
           'GIT_AUTHOR_EMAIL': 'w2@example.invalid', 'GIT_COMMITTER_NAME': 'Synthetic W2 lab',
           'GIT_COMMITTER_EMAIL': 'w2@example.invalid', 'GIT_AUTHOR_DATE': '1776222000 +0900',
           'GIT_COMMITTER_DATE': '1776222000 +0900'}
    if index is not None:
        env['GIT_INDEX_FILE'] = str(index)
    return subprocess.run(['git', '--git-dir', str(repo), *args], input=data, env=env,
                          check=True, capture_output=True).stdout.decode().strip()


def commit_files(repo, files, index, parent=None):
    # Private index and object writes only; never checkout/update-ref/reset.
    git(repo, 'read-tree', '--empty', index=index)
    rows = []
    for rel, raw in sorted(files.items()):
        blob = git(repo, 'hash-object', '-w', '--stdin', data=raw)
        rows.append(f'100644 {blob}\t{rel}\n')
    git(repo, 'update-index', '--index-info', data=''.join(rows).encode(), index=index)
    tree = git(repo, 'write-tree', index=index)
    args = ['commit-tree', tree]
    if parent:
        args.extend(['-p', parent])
    commit = git(repo, *args, '-F', '-', data=b'Synthetic W2 lab only\n')
    return tree, commit


def run_lab(source, *, reference_sets, failpoint=None):
    if failpoint is not None and failpoint not in FAILPOINTS:
        reject('unknown failpoint')
    proof = source_proof(source, reference_sets)
    source = Path(source)

    def fault(point):
        if failpoint == point:
            raise RuntimeError(f'injected lab interruption: {point}')

    with TemporaryDirectory(prefix='snowfall-w2-lab-') as tmp:
        owned = Path(tmp)
        stage = owned / 'stage'
        shutil.copytree(source, stage)
        if snapshot_tree_bytes(stage) != proof['file_sha256']:
            reject('staging changed synthetic source')
        fault('after_stage')
        container = transform(list_runtime_files(stage), proof)
        target = owned / 'target.lab.json'
        target.write_bytes(canonical_bytes(container))
        fault('after_transform')
        if failpoint == 'invalid_target':
            container['documents'][0]['utf8'] += '\n'
            target.write_bytes(canonical_bytes(container))
        # Read the actual serialized target, not just the transform's object.
        files = decode(json.loads(target.read_text()), proof)
        decoded = owned / 'decoded'
        for rel, raw in files.items():
            path = decoded / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        if inspect_persistence(decoded, target_engine=ENGINE_VERSION, reference_sets=reference_sets) != proof:
            reject('decoded target lost historical semantics/metadata')
        if snapshot_tree_bytes(source) != proof['file_sha256']:
            reject('source changed during migration experiment')
        fault('before_commit')
        repo = owned / 'objects.git'
        subprocess.run(['git', 'init', '--bare', '--object-format=sha1', '--initial-branch=lab-unborn', str(repo)],
                       check=True, capture_output=True)
        head_before = (repo / 'HEAD').read_bytes()
        refs_before = git(repo, 'for-each-ref', '--format=%(refname) %(objectname)')
        _, base = commit_files(repo, list_runtime_files(stage), owned / 'base-index')
        tree, commit = commit_files(repo, {'target.lab.json': target.read_bytes()}, owned / 'candidate-index', base)
        fault('after_commit')
        if git(repo, 'rev-list', '--parents', '-n', '1', commit).split() != [commit, base]:
            reject('incorrect candidate parent')
        if git(repo, 'rev-parse', f'{commit}^{{tree}}') != tree:
            reject('incorrect candidate tree')
        stored = git(repo, 'show', f'{commit}:target.lab.json').encode()
        # git helper strips trailing whitespace; canonical_bytes has none.
        if stored != target.read_bytes():
            reject('stored candidate differs from verified lab target')
        decode(json.loads(stored), proof)
        if ((repo / 'HEAD').read_bytes() != head_before
            or git(repo, 'for-each-ref', '--format=%(refname) %(objectname)') != refs_before
            or (repo / 'index').exists()
            or git(repo, 'for-each-ref', '--contains', commit)):
            reject('lab candidate acquired authority')
        fault('before_exit')
        if snapshot_tree_bytes(source) != proof['file_sha256']:
            reject('source changed before lab exit')
        evidence = {'status': 'VERIFIED_SYNTHETIC_LAB_ONLY', 'format': LAB_FORMAT,
                    'source_evidence_hash': canonical_hash(proof),
                    'source_file_sha256': proof['file_sha256'],
                    'original_semantic_tree_hash': proof['semantic_tree_hash'],
                    'target_file_sha256': hashlib.sha256(stored).hexdigest(),
                    'base_commit_sha': base, 'candidate_tree_sha': tree, 'candidate_commit_sha': commit,
                    'history': proof['history'], 'operator_history': proof['operator_history'],
                    'effective_events_hash': proof['effective_events_hash'],
                    'source_unchanged': True, 'decoded_byte_identical': True,
                    'unreferenced_candidate': True, 'publication_authorized': False,
                    'stable_v1_guarantee': False}
        evidence['proof_hash'] = canonical_hash(evidence)
        return evidence


def recover_simulated_ack(source, *, evidence, observed_head, reference_sets):
    """Pure host-observation model; never reads/writes any real ref or publishes.

    Recompute the full synthetic transaction after restart, rather than trusting
    a self-sealed, caller-supplied receipt. Revalidation is not retry approval.
    """
    if run_lab(source, reference_sets=reference_sets) != evidence:
        reject('stale/altered receipt; restart proof differs')
    if not isinstance(observed_head, str) or re.fullmatch('[0-9a-f]{40}', observed_head) is None:
        reject('invalid simulated host observation')
    state = ('ALREADY_PUBLISHED' if observed_head == evidence['candidate_commit_sha']
             else 'NOT_PUBLISHED' if observed_head == evidence['base_commit_sha'] else 'CONFLICT')
    return {'simulation_only': True, 'state': state, 'publication_performed': False,
            'retry_authorized': False, 'source_unchanged': True}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    refs = RuntimeReferenceSets(**{k: set(v) for k, v in json.loads((GOLDEN / 'references.json').read_text()).items()})
    print(json.dumps(run_lab(args.source, reference_sets=refs), sort_keys=True))
