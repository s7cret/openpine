"""Public transport rejects unexpected or sensitive bytes without editing them."""
import importlib.util
from pathlib import Path

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
