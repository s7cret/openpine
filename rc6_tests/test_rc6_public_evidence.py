"""Public transport rejects unexpected or sensitive bytes without editing them."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


def owner():
    path = Path(__file__).resolve().parents[1] / 'scripts/rc6_public_evidence.py'
    spec = importlib.util.spec_from_file_location('public_evidence_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('path,content', [
    ('database.sqlite', b'SQLite format 3'), ('sources.tar.gz', b'archive'),
    ('credentials.json', b'{}'), ('junit.xml', b'\xff'),
    ('junit.xml', b'person@example.com'),
    ('junit.xml', b'https://user:password@example.invalid'),
    ('junit.xml', b'-----BEGIN PRIVATE KEY-----'),
    ('junit.xml', b'ghp_' + b'a' * 30),
    ('junit.xml', b'api_key=123456789abcdef'),
], ids=[
    'database-file', 'source-archive', 'unknown-file', 'non-text',
    'personal-email', 'url-credentials', 'private-key', 'access-token',
    'credential-value',
])
def test_public_evidence_rejects_unapproved_contents(tmp_path, path, content):
    primary = tmp_path / path
    primary.write_bytes(content)
    report = owner().audit(tmp_path, candidate='a' * 40, run_id='test-run', max_bytes=1024)
    assert not report['ok'] and report['errors']
    assert primary.read_bytes() == content
    assert report['permission_granted'] is False


def test_public_evidence_accepts_allowlisted_technical_bytes(tmp_path):
    (tmp_path / 'summary.json').write_text('{"full_product_qualified":false}')
    folder = tmp_path / 'commands/0001'
    folder.mkdir(parents=True)
    (folder / 'stdout.log').write_text('fixture ci@example.invalid: ordinary CPython 3.13.5')
    report = owner().audit(tmp_path, candidate='a' * 40, run_id='test-run', max_bytes=1024)
    assert report['ok'] and len(report['files']) == 2


def test_public_evidence_rejects_symlink_and_budget(tmp_path):
    (tmp_path / 'summary.json').write_text('x' * 32)
    (tmp_path / 'junit.xml').symlink_to(tmp_path / 'summary.json')
    assert not owner().audit(tmp_path, candidate='a' * 40, run_id='test-run', max_bytes=1024)['ok']
    with pytest.raises(ValueError, match='byte ceiling'):
        owner().audit(tmp_path, candidate='a' * 40, run_id='test-run', max_bytes=16)


@pytest.mark.parametrize('mode', ['execution', 'collection', 'platform-collection'])
def test_public_evidence_reports_remain_safe(tmp_path, mode):
    """Scan actual pytest reports so negative fixture bytes cannot leak via IDs."""
    report_root = tmp_path / 'reports'
    logs = report_root / 'commands/0001'
    logs.mkdir(parents=True)
    host = Path(__file__).resolve().parents[1]
    selector = ('rc6_tests/test_rc6_execution_platform.py::test_real_unsuccessful_shards_never_pass'
                if mode == 'platform-collection' else
                'rc6_tests/test_rc6_public_evidence.py::test_public_evidence_rejects_unapproved_contents')
    argv = [sys.executable, '-m', 'pytest', '--noconftest', '-o', 'addopts=', '-q',
            selector,
            '-p', 'openpine.verification.pytest_gate',
            '--verification-output=' + str(report_root / 'collection.json')]
    if mode != 'execution':
        argv.append('--collect-only')
    else:
        argv.append('--junitxml=' + str(report_root / 'junit.xml'))
    with (logs / 'stdout.log').open('wb') as stdout, (logs / 'stderr.log').open('wb') as stderr:
        result = subprocess.run(  # noqa: S603 -- exact interpreter and declared test selector
            argv, cwd=host, stdout=stdout, stderr=stderr,
            env=dict(os.environ, PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'), timeout=30,
        )
    assert result.returncode == 0
    if mode == 'execution':
        suite = next(ET.parse(report_root / 'junit.xml').getroot().iter('testsuite'))
        assert int(suite.attrib['tests']) == 9
        assert int(suite.attrib['failures']) == int(suite.attrib['errors']) == 0
    report = owner().audit(report_root, candidate='a' * 40, run_id='report-regression',
                           max_bytes=1024 * 1024)
    assert report['ok'], report['errors']
    assert report['original_primaries_changed'] is False


def upload_files(tmp_path):
    archive = tmp_path / 'archive'
    archive.mkdir()
    (archive / 'evidence.tar.gz').write_bytes(b'x' * 128)
    (archive / 'manifest.json').write_text('{}')
    audit = tmp_path / 'public-evidence-audit.json'
    audit.write_text(json.dumps({'ok': True}))
    return archive, audit


def test_public_upload_budget_includes_all_files_and_zip_reserve(tmp_path):
    archive, audit = upload_files(tmp_path)
    required = [archive / 'evidence.tar.gz', archive / 'manifest.json', audit]
    before = {str(p): p.read_bytes() for p in required}
    payload = sum(len(content) for content in before.values())
    boundary = payload + 1024 * 1024 + 3 * 1024
    report = owner().check_upload_payload(archive, audit, max_bytes=boundary)
    assert report['payload_bytes'] == payload
    assert report['upload_upper_bound_bytes'] == boundary
    assert len(report['files']) == 3
    with pytest.raises(ValueError, match='complete required upload payload'):
        owner().check_upload_payload(archive, audit, max_bytes=boundary - 1)
    # A tar-only cap must also fail; no primary file is dropped to fit the budget.
    with pytest.raises(ValueError, match='complete required upload payload'):
        owner().check_upload_payload(archive, audit, max_bytes=128)
    assert {str(p): p.read_bytes() for p in required} == before


@pytest.mark.parametrize('mutation', ['missing', 'extra', 'symlink', 'audit-failed'])
def test_public_upload_budget_rejects_incomplete_or_unsafe_payload(tmp_path, mutation):
    archive, audit = upload_files(tmp_path)
    if mutation == 'missing':
        (archive / 'manifest.json').unlink()
    elif mutation == 'extra':
        (archive / 'unexpected.txt').write_text('unapproved')
    elif mutation == 'symlink':
        (archive / 'manifest.json').unlink()
        (archive / 'manifest.json').symlink_to(audit)
    else:
        audit.write_text(json.dumps({'ok': False}))
    with pytest.raises(ValueError):
        owner().check_upload_payload(archive, audit, max_bytes=2 * 1024 * 1024)


def aliased_inventory():
    from openpine.verification.pytest_gate import collection_hash
    historic = ['test.py::test_body[old-one]', 'test.py::test_body[old-two]']
    current = ['test.py::test_body[new-one]', 'test.py::test_body[new-two]', 'test.py::test_added']
    expected = {'identity_mode': 'reviewed_addition_to_hashed_baseline',
                'count': 3, 'deselected': 0, 'added_nodeids': [current[-1]],
                'baseline': {'count': 2, 'deselected': 0, 'sha256': collection_hash(historic)},
                'nodeid_aliases': dict(zip(current[:2], historic, strict=True))}
    return current, expected


def test_reviewed_neutral_ids_preserve_historical_inventory_and_real_phase_obligations():
    from openpine.verification.pytest_gate import validate_inventory, validate_phase_reports
    current, expected = aliased_inventory()
    before = json.dumps(expected, sort_keys=True)
    validate_inventory(current, expected, 0)
    assert json.dumps(expected, sort_keys=True) == before
    assert validate_phase_reports(current, {})  # Renaming never satisfies execution.


@pytest.mark.parametrize('mutation', [
    'not-map', 'non-string', 'unknown-current', 'duplicate-historical',
    'historical-present', 'cross-test', 'cycle', 'alias-addition', 'missing-alias',
    'wrong-historical', 'nested-baseline', 'unreviewed-mode',
])
def test_reviewed_neutral_ids_reject_changed_or_incomplete_obligations(mutation):
    from openpine.verification.pytest_gate import validate_inventory
    current, expected = aliased_inventory()
    aliases = expected['nodeid_aliases']
    if mutation == 'not-map':
        expected['nodeid_aliases'] = []
    elif mutation == 'non-string':
        aliases[current[0]] = None
    elif mutation == 'unknown-current':
        aliases['test.py::test_body[unknown]'] = aliases.pop(current[0])
    elif mutation == 'duplicate-historical':
        aliases[current[1]] = aliases[current[0]]
    elif mutation == 'historical-present':
        current[0] = aliases[current[0]]
    elif mutation == 'cross-test':
        aliases[current[0]] = 'other.py::test_body[old-one]'
    elif mutation == 'cycle':
        aliases[current[0]] = current[1]
        aliases[current[1]] = current[0]
    elif mutation == 'alias-addition':
        aliases[current[-1]] = 'test.py::test_added[old]'
    elif mutation == 'missing-alias':
        aliases.pop(current[0])
    elif mutation == 'wrong-historical':
        aliases[current[0]] = 'test.py::test_body[other]'
    elif mutation == 'nested-baseline':
        expected['baseline']['nodeid_aliases'] = {}
    else:
        expected.pop('identity_mode')
    with pytest.raises(ValueError):
        validate_inventory(current, expected, 0)
