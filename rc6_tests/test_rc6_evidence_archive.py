"""Evidence transport and disposal reject unavailable or foreign primary bytes."""
import gzip
import json
import shutil
import tarfile
from pathlib import Path

import pytest

from openpine.verification import execution_campaign as campaign
from openpine.verification.execution_evidence import archive_evidence, cleanup_archived_private, main, verify_archive
from openpine.verification.execution_identity import hash_file, write_once_json
from openpine.verification.identity import seal
from rc6_tests.test_rc6_execution_platform import tiny_plan

CANDIDATE = 'a' * 40
SOURCE = 'sha256:' + 'b' * 64


def reseal(value):
    return seal({k: v for k, v in value.items() if k != 'content_hash'})


def evidence(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'input.py').write_text('source bytes')
    root = tmp_path / 'run'
    root.mkdir()
    (root / 'execution.json').write_text('{"status":"failed"}')
    (root / 'stderr.log').write_bytes(b'original failure\x00\xff\n')
    private = root / 'owner' / 'private'
    private.mkdir(parents=True)
    (private / 'fixture').write_bytes(b'temporary fixture')
    return root, source


def create(tmp_path, root, source, name='archive', **kwargs):
    return archive_evidence(root, tmp_path / name, roots={'source': source}, candidate=CANDIDATE,
        run_id='run-17-attempt-1', source_hash=SOURCE, retention_days=1, max_bytes=1024 * 1024,
        **kwargs)


def verify(path, manifest, **kwargs):
    return verify_archive(path, manifest, expected_manifest_hash=manifest['content_hash'],
        expected_candidate=kwargs.get('candidate', CANDIDATE),
        expected_run_id=kwargs.get('run_id', 'run-17-attempt-1'),
        expected_source_hash=kwargs.get('source_hash', SOURCE))


def test_failed_primary_roundtrip_is_portable_deterministic_and_never_acceptance(tmp_path):
    root, source = evidence(tmp_path)
    originals = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    first = create(tmp_path, root, source)
    second = create(tmp_path, root, source, name='again')
    assert first == second
    assert first['full_product_accepted'] is False
    assert first['retention_days'] == 1
    download = tmp_path / 'download'
    shutil.copytree(tmp_path / 'archive', download)
    result = verify(download / 'evidence.tar.gz', first)
    assert result['verified'] is True
    assert result['file_count'] == len(originals)
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob('*') if p.is_file()} == originals
    assert not list((tmp_path / 'archive').glob('*.partial'))
    with pytest.raises(ValueError, match='already exists'):
        create(tmp_path, root, source)


@pytest.mark.parametrize('foreign', ['candidate', 'run_id', 'source_hash'])
def test_foreign_provenance_rejected(tmp_path, foreign):
    root, source = evidence(tmp_path)
    manifest = create(tmp_path, root, source)
    value = 'c' * 40 if foreign == 'candidate' else 'different-run'
    if foreign == 'source_hash':
        value = 'sha256:' + 'c' * 64
    with pytest.raises(ValueError, match='identity mismatch'):
        verify(tmp_path / 'archive/evidence.tar.gz', manifest, **{foreign: value})
    with pytest.raises(ValueError, match='identity mismatch'):
        verify_archive(tmp_path / 'archive/evidence.tar.gz', manifest, expected_manifest_hash='sha256:' + '0' * 64,
                       expected_candidate=CANDIDATE, expected_run_id='run-17-attempt-1')


@pytest.mark.parametrize('damage', ['mutated', 'truncated', 'missing-member', 'gzip-footer', 'tar-symlink'])
def test_incomplete_or_mutated_upload_fails_closed(tmp_path, damage):
    root, source = evidence(tmp_path)
    manifest = create(tmp_path, root, source)
    archive = tmp_path / 'archive/evidence.tar.gz'
    content = archive.read_bytes()
    if damage == 'mutated':
        archive.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
    elif damage in {'truncated', 'gzip-footer'}:
        archive.write_bytes(content[:-8])
    else:
        with archive.open('wb') as raw, gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|') as stream:
                if damage == 'tar-symlink':
                    member = tarfile.TarInfo('execution.json')
                    member.type, member.linkname = tarfile.SYMTYPE, '/tmp/foreign'
                    stream.addfile(member)
        manifest['archive'].update(sha256=hash_file(archive), size=archive.stat().st_size)
        manifest = reseal(manifest)
    if damage == 'gzip-footer':
        manifest['archive'].update(sha256=hash_file(archive), size=archive.stat().st_size)
        manifest = reseal(manifest)
    with pytest.raises(ValueError):
        verify(archive, manifest)
    assert (root / 'stderr.log').read_bytes() == b'original failure\x00\xff\n'


@pytest.mark.parametrize('path', ['../escape', '/absolute', 'drive:c', 'a\\b', 'a//b'])
def test_manifest_path_traversal_rejected(tmp_path, path):
    root, source = evidence(tmp_path)
    manifest = create(tmp_path, root, source)
    record = manifest['files'].pop('stderr.log')
    manifest['files'][path] = record
    with pytest.raises(ValueError, match='invalid relative evidence path'):
        verify(tmp_path / 'archive/evidence.tar.gz', reseal(manifest))


@pytest.mark.parametrize('unsafe', ['source', 'source-parent', 'nested-output', 'symlink-root', 'symlink-file', 'arbitrary-exclusion'])
def test_unsafe_scope_preserves_all_inputs(tmp_path, unsafe):
    root, source = evidence(tmp_path)
    options = {}
    output = tmp_path / 'archive'
    if unsafe == 'source':
        root = source
    elif unsafe == 'source-parent':
        root = tmp_path
        output = tmp_path.parent / (tmp_path.name + '-archive')
    elif unsafe == 'nested-output':
        output = root / 'archive'
    elif unsafe == 'symlink-root':
        alias = tmp_path / 'alias'
        alias.symlink_to(root, target_is_directory=True)
        root = alias
    elif unsafe == 'symlink-file':
        (root / 'external').symlink_to(source / 'input.py')
    else:
        options['private'] = ('stderr.log',)
    with pytest.raises(ValueError):
        archive_evidence(root, output, roots={'source': source}, candidate=CANDIDATE,
                         run_id='run-17-attempt-1', retention_days=1, max_bytes=1024 * 1024, **options)
    assert (source / 'input.py').read_text() == 'source bytes'
    assert (tmp_path / 'run/stderr.log').read_bytes() == b'original failure\x00\xff\n'


def test_explicit_private_exclusion_and_budget_preserve_private(tmp_path):
    root, source = evidence(tmp_path)
    private = root / 'owner/private'
    (private / 'pytest-current').symlink_to(private, target_is_directory=True)
    manifest = create(tmp_path, root, source, private=('owner/private',))
    assert 'owner/private/fixture' not in manifest['files']
    assert manifest['private_exclusions'] == ['owner/private']
    assert (private / 'fixture').read_bytes() == b'temporary fixture'
    with pytest.raises(ValueError, match='budget'):
        archive_evidence(root, tmp_path / 'too-small', roots={'source': source}, candidate=CANDIDATE,
                         run_id='run-17-attempt-1', retention_days=1, max_bytes=1, private=('owner/private',))
    assert not (tmp_path / 'too-small').exists()


def test_compressed_budget_failure_retains_owned_partial_and_all_primaries(tmp_path):
    root, source = evidence(tmp_path)
    for path in root.rglob('*'):
        if path.is_file():
            path.write_bytes(b'')
    with pytest.raises(ValueError, match='compressed evidence exceeds archive budget'):
        archive_evidence(root, tmp_path / 'too-small', roots={'source': source}, candidate=CANDIDATE,
                         run_id='run-17-attempt-1', retention_days=1, max_bytes=32)
    assert (tmp_path / 'too-small/evidence.tar.gz.partial').is_file()
    assert not (tmp_path / 'too-small/manifest.json').exists()
    assert (root / 'stderr.log').is_file()


def test_insufficient_disk_never_starts_archive_or_deletes_primary(tmp_path, monkeypatch):
    root, source = evidence(tmp_path)
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: shutil._ntuple_diskusage(0, 0, 0))
    with pytest.raises(ValueError, match='insufficient free disk'):
        create(tmp_path, root, source)
    assert not list((tmp_path / 'archive').iterdir())
    assert (root / 'stderr.log').read_bytes() == b'original failure\x00\xff\n'


def test_private_exclusion_cannot_hide_owner_primary_reference(tmp_path):
    root, source = evidence(tmp_path)
    fixture = root / 'owner/private/fixture'
    (root / 'owner/receipt.json').write_text(json.dumps({'artifact': {
        'path': 'private/fixture', 'sha256': hash_file(fixture)}}))
    with pytest.raises(ValueError, match='overlaps a primary artifact'):
        create(tmp_path, root, source, private=('owner/private',))
    assert fixture.read_bytes() == b'temporary fixture'
    assert not (tmp_path / 'archive').exists()


def test_cli_verifies_download_and_rejects_missing_upload(tmp_path, capsys):
    root, source = evidence(tmp_path)
    manifest = create(tmp_path, root, source)
    args = ['verify', '--archive', str(tmp_path / 'archive/evidence.tar.gz'),
            '--manifest', str(tmp_path / 'archive/manifest.json'), '--expected-manifest-hash', manifest['content_hash'],
            '--candidate-sha', CANDIDATE, '--source-hash', SOURCE, '--run-id', 'run-17-attempt-1']
    assert main(args) == 0
    assert '"verified":true' in capsys.readouterr().out
    (tmp_path / 'archive/evidence.tar.gz').unlink()
    assert main(args) == 2
    assert '"verified":false' in capsys.readouterr().out


def test_archive_cli_requires_explicit_budget_and_retention(tmp_path, capsys):
    root, source = evidence(tmp_path)
    args = ['archive', '--evidence', str(root), '--output', str(tmp_path / 'archive'),
            '--source-root', str(source), '--candidate', CANDIDATE, '--source-hash', SOURCE,
            '--run-id', 'run-17-attempt-1', '--retention-days', '7', '--max-bytes', '1048576']
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    manifest = json.loads((tmp_path / 'archive/manifest.json').read_text())
    assert result['manifest_hash'] == manifest['content_hash']
    assert manifest['retention_days'] == 7
    assert result['full_product_accepted'] is False


@pytest.mark.parametrize('outcome', ['completed', 'failed', 'tampered-upload', 'missing-primary'])
def test_existing_cleanup_owner_requires_success_and_verified_archived_primaries(tmp_path, monkeypatch, outcome):
    body = "def test_value(tmp_path):\n    (tmp_path / 'fixture').write_text('private fixture')\n"
    if outcome == 'failed':
        body += '    assert False\n'
    plan, _ = tiny_plan(tmp_path, bodies={'test_a.py': body}, shards=1)
    plan['tasks'][0]['private_retention'] = 'delete-on-success'
    plan = reseal(plan)
    plan_path = tmp_path / 'retention-plan.json'
    write_once_json(plan_path, plan)
    cleanup = campaign._clear_owned_private
    # Defer the existing opt-in cleanup to the authenticated archive boundary.
    monkeypatch.setattr(campaign, '_clear_owned_private', lambda *args: None)
    run = campaign.run_campaign(plan, plan_path, tmp_path / 'run', jobs=1, run_id='archive-retention')
    monkeypatch.setattr(campaign, '_clear_owned_private', cleanup)
    root = tmp_path / 'run'
    attempt = 'tiny@py/s000/a001'
    private = root / attempt / 'private'
    assert private.is_dir()
    adjacent = tmp_path / 'adjacent-run'
    adjacent.mkdir()
    (adjacent / 'sentinel').write_text('previous evidence')
    manifest = archive_evidence(root, tmp_path / 'archive', roots={n: Path(p) for n, p in plan['roots'].items()},
        candidate=plan['source']['content_hash'], source_hash=plan['source']['content_hash'],
        run_id=run['run_id'], retention_days=1, max_bytes=1024 * 1024, private=(attempt + '/private',))
    archive = tmp_path / 'archive/evidence.tar.gz'
    if outcome == 'tampered-upload':
        archive.write_bytes(archive.read_bytes()[:-8])
    elif outcome == 'missing-primary':
        manifest['files'].pop(attempt + '/stdout.log')
        manifest['file_count'] -= 1
        manifest['total_bytes'] = sum(r['size'] for r in manifest['files'].values())
        manifest = reseal(manifest)
    kwargs = dict(expected_manifest_hash=manifest['content_hash'], expected_candidate=plan['source']['content_hash'], run_id=run['run_id'])
    if outcome == 'completed':
        assert main(['cleanup-private', '--plan', str(plan_path), '--evidence', str(root), '--attempt', attempt,
                     '--archive', str(archive), '--manifest', str(tmp_path / 'archive/manifest.json'),
                     '--expected-manifest-hash', manifest['content_hash'], '--candidate', plan['source']['content_hash'],
                     '--run-id', run['run_id']]) == 0
        assert not private.exists()
        assert campaign.aggregate_campaign(plan, root, expected_plan_hash=plan['content_hash'],
            expected_run_id=run['run_id'])['pytest_scope_passed']
    else:
        with pytest.raises(ValueError):
            cleanup_archived_private(plan, root, attempt, archive, manifest, **kwargs)
        assert private.is_dir()
    assert (adjacent / 'sentinel').read_text() == 'previous evidence'
    for descriptor in run['attempts'][0]['artifacts'].values():
        assert hash_file(root / descriptor['path']) == descriptor['sha256']
