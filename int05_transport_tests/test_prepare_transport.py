"""Transport environment boundary contracts; no network or build qualification."""
from __future__ import annotations

import json
import importlib.metadata
import os
from pathlib import Path
import sys
import traceback

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from openpine.verification.execution_ci import Commands
from openpine.verification.execution_identity import clean_environment


# These expected names are independent of the production allowlist.
PROXIES = ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy')
BYPASS = ('NO_PROXY', 'no_proxy')
CA_FILES = ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'GIT_SSL_CAINFO')
SECRETS_AND_UNSAFE = ('GH_TOKEN', 'GITHUB_TOKEN', 'OPENAI_API_KEY', 'AWS_SECRET_ACCESS_KEY',
                     'SSH_AUTH_SOCK', 'GIT_ASKPASS', 'GIT_CONFIG_PARAMETERS',
                     'GIT_SSL_NO_VERIFY', 'PYTHONHTTPSVERIFY', 'PIP_TRUSTED_HOST',
                     'UV_INSECURE_HOST', 'SSLKEYLOGFILE', 'NODE_EXTRA_CA_CERTS',
                     'PIP_INDEX_URL', 'UV_INDEX_URL', 'CUSTOM_PROXY_TOKEN')
SYNTHETIC_SECRET = 'INT05-SYNTHETIC-DO-NOT-LEAK'


@pytest.fixture
def configured(monkeypatch, tmp_path):
    for name in PROXIES + BYPASS + CA_FILES:
        monkeypatch.delenv(name, raising=False)
    cert = tmp_path / 'configured-ca.pem'
    cert.write_text('unit boundary input; no TLS connection is made\n')
    expected = {name: 'https://proxy.example.invalid:8443' for name in PROXIES}
    expected.update({name: 'localhost,127.0.0.1,::1,.example.invalid' for name in BYPASS})
    expected.update({name: str(cert) for name in CA_FILES})
    for name, value in expected.items():
        monkeypatch.setenv(name, value)
    for name in SECRETS_AND_UNSAFE:
        monkeypatch.setenv(name, SYNTHETIC_SECRET)
    monkeypatch.setenv('PYTHONPATH', SYNTHETIC_SECRET)
    return expected


def test_default_keeps_transport_and_credentials_out(configured, tmp_path):
    env = clean_environment({'openpine': str(tmp_path / 'source')}, tmp_path / 'private')
    assert not set(PROXIES + BYPASS + CA_FILES + SECRETS_AND_UNSAFE) & env.keys()
    assert env['PYTHONPATH'] == str(tmp_path / 'source')
    assert SYNTHETIC_SECRET not in env.values()


def test_opt_in_forwards_only_configured_proxy_and_ca_paths(configured, tmp_path):
    before = Path(configured['SSL_CERT_FILE']).read_bytes()
    env = clean_environment({}, tmp_path / 'private', inherit_transport=True)
    assert {name: env[name] for name in configured} == configured
    assert not set(SECRETS_AND_UNSAFE) & env.keys()
    assert SYNTHETIC_SECRET not in env.values()
    assert Path(configured['SSL_CERT_FILE']).read_bytes() == before
    assert env['PYTHONPATH'] == ''


@pytest.mark.parametrize('flag', [None, 1, 'yes'])
def test_transport_opt_in_requires_literal_boolean(configured, tmp_path, flag):
    with pytest.raises(ValueError, match='boolean'):
        clean_environment({}, tmp_path / 'private', inherit_transport=flag)


def test_absent_configuration_is_not_invented(monkeypatch, tmp_path):
    for name in PROXIES + BYPASS + CA_FILES:
        monkeypatch.delenv(name, raising=False)
    env = clean_environment({}, tmp_path / 'private', inherit_transport=True)
    assert not set(PROXIES + BYPASS + CA_FILES) & env.keys()


@pytest.mark.parametrize('proxy', [
    'http://user:INT05-SYNTHETIC-DO-NOT-LEAK@proxy.invalid:8080',
    'https://INT05-SYNTHETIC-DO-NOT-LEAK@proxy.invalid',
    'https://proxy.invalid?token=INT05-SYNTHETIC-DO-NOT-LEAK',
    'https://proxy.invalid#INT05-SYNTHETIC-DO-NOT-LEAK',
    'https://proxy.invalid/INT05-SYNTHETIC-DO-NOT-LEAK',
    'http://user%3AINT05-SYNTHETIC-DO-NOT-LEAK%40proxy.invalid:8080',
    'https://proxy.invalid:INT05-SYNTHETIC-DO-NOT-LEAK',
    'https://proxy.invalid:99999',
    'https://proxy.invalid\nINT05-SYNTHETIC-DO-NOT-LEAK',
    'file:///tmp/proxy',
    'proxy.invalid:8080',
    'https://',
], ids=['password', 'userinfo', 'query', 'fragment', 'path', 'encoded-userinfo',
        'invalid-port-secret', 'invalid-port', 'newline', 'scheme', 'relative', 'no-host'])
def test_invalid_proxy_fails_without_values_in_diagnostics(configured, monkeypatch, tmp_path, proxy):
    monkeypatch.setenv('HTTPS_PROXY', proxy)
    with pytest.raises(ValueError) as caught:
        clean_environment({}, tmp_path / 'private', inherit_transport=True)
    rendered = ''.join(traceback.format_exception(caught.value))
    assert 'HTTPS_PROXY' in rendered
    assert proxy not in rendered
    assert SYNTHETIC_SECRET not in rendered


@pytest.mark.parametrize('kind', ['relative', 'missing', 'directory', 'newline', 'device'])
def test_invalid_ca_path_fails_without_echoing_value(configured, monkeypatch, tmp_path, kind):
    paths = {'relative': SYNTHETIC_SECRET + '.pem',
             'missing': str(tmp_path / (SYNTHETIC_SECRET + '.pem')),
             'directory': str(tmp_path),
             'newline': str(tmp_path / (SYNTHETIC_SECRET + '\n.pem')),
             'device': os.devnull}
    monkeypatch.setenv('SSL_CERT_FILE', paths[kind])
    with pytest.raises(ValueError) as caught:
        clean_environment({}, tmp_path / 'private', inherit_transport=True)
    rendered = ''.join(traceback.format_exception(caught.value))
    assert 'SSL_CERT_FILE' in rendered
    assert SYNTHETIC_SECRET not in rendered


def test_proxy_bypass_cannot_carry_credentials(configured, monkeypatch, tmp_path):
    monkeypatch.setenv('NO_PROXY', 'user:' + SYNTHETIC_SECRET + '@proxy.invalid')
    with pytest.raises(ValueError, match='NO_PROXY') as caught:
        clean_environment({}, tmp_path / 'private', inherit_transport=True)
    assert SYNTHETIC_SECRET not in ''.join(traceback.format_exception(caught.value))


@pytest.mark.parametrize('inherit', [False, True])
def test_actual_stock_child_receives_opt_in_without_secret_leak(configured, tmp_path, inherit):
    program = ('import json,os; print(json.dumps({'
               '"proxy": "HTTPS_PROXY" in os.environ,'
               '"ca": "SSL_CERT_FILE" in os.environ,'
               '"forbidden": sorted(k for k in os.environ if k in '
               + repr(SECRETS_AND_UNSAFE) + ')}))')
    output = tmp_path / 'commands'
    result = Commands(output).run([sys.executable, '-c', program], cwd=tmp_path,
                                  inherit_transport=inherit)
    assert json.loads(result) == {'proxy': inherit, 'ca': inherit, 'forbidden': []}
    for path in output.rglob('*'):
        if path.is_file():
            assert SYNTHETIC_SECRET.encode() not in path.read_bytes()
    raw = json.loads((output / 'commands/0000/command.json').read_text())
    assert raw['ok'] and not raw['full_stage_accepted']
    assert 'GH_TOKEN' not in raw['environment_keys']
    assert ('HTTPS_PROXY' in raw['environment_keys']) is inherit
    assert 'environment_values' not in raw


def test_credential_proxy_is_rejected_before_child_or_receipt(configured, monkeypatch, tmp_path):
    monkeypatch.setenv('http_proxy', 'http://user:' + SYNTHETIC_SECRET + '@proxy.invalid')
    work = tmp_path / 'commands'
    with pytest.raises(ValueError):
        Commands(work).run([sys.executable, '-c', 'raise SystemExit(99)'], cwd=tmp_path,
                           inherit_transport=True)
    assert not (work / 'commands/0000').exists()


@pytest.mark.parametrize('proxy', [
    'https://bad host:8080',
    ' https://proxy.invalid:8080',
    'https://proxy.invalid:8080 ',
    'https://bad\thost:8080',
    'https://bad\u00a0host:8080',
    'https://bad\u2003host:8080',
    'https://bad\\host:8080',
    'https://proxy.invalid\\@other.invalid:8080',
    'https://bad_host:8080',
    'https://-bad.invalid:8080',
    'https://bad-.invalid:8080',
    'https://bad..invalid:8080',
    'https://' + 'a' * 64 + '.invalid:8080',
    'https://' + '.'.join(['a' * 63] * 4) + ':8080',
    'https://999.1.2.3:8080',
    'https://proxy.invalid:',
    'https://[::1]extra:8080',
    'https://proxy.invalid:8080\\',
], ids=['embedded-space', 'leading-space', 'trailing-space', 'tab', 'nbsp',
        'unicode-space', 'hostname-backslash', 'authority-backslash', 'underscore',
        'leading-hyphen', 'trailing-hyphen', 'empty-label', 'long-label', 'long-host',
        'bad-ipv4', 'empty-port', 'ipv6-authority-suffix', 'trailing-backslash'])
def test_malformed_hostname_rejected_before_child_without_value_leak(
        configured, monkeypatch, tmp_path, proxy):
    monkeypatch.setenv('HTTPS_PROXY', proxy)
    work = tmp_path / 'commands'
    launched = tmp_path / 'child-launched'
    program = 'from pathlib import Path; Path(' + repr(str(launched)) + ').touch()'
    with pytest.raises(ValueError) as caught:
        Commands(work).run([sys.executable, '-c', program], cwd=tmp_path,
                           inherit_transport=True)
    rendered = ''.join(traceback.format_exception(caught.value))
    assert str(caught.value) == 'invalid configured transport setting: HTTPS_PROXY'
    assert proxy not in rendered
    assert SYNTHETIC_SECRET not in rendered
    assert not launched.exists()
    assert not (work / 'commands/0000').exists()


@pytest.mark.parametrize('proxy', ['http://localhost:8080', 'https://proxy.invalid.:443',
                                 'http://127.0.0.1:8080', 'https://[::1]:8443',
                                 'http://xn--bcher-kva.invalid:8080'])
def test_bounded_dns_ipv4_ipv6_proxy_hosts_remain_supported(
        configured, monkeypatch, tmp_path, proxy):
    monkeypatch.setenv('HTTPS_PROXY', proxy)
    env = clean_environment({}, tmp_path / 'private', inherit_transport=True)
    assert env['HTTPS_PROXY'] == proxy


def test_existing_hashed_locks_cover_the_declared_extras_transitively():
    import re
    host = Path(__file__).resolve().parents[1]
    locked = {}
    for name in ('ci-bootstrap-requirements.txt', 'ci-runtime-requirements.txt'):
        text = (host / 'verification' / name).read_text()
        blocks = re.split(r'(?=^[\w.-]+==)', text, flags=re.M)[1:]
        for block in blocks:
            req = Requirement(block.splitlines()[0].removesuffix(' \\'))
            assert re.search(r'--hash=sha256:[0-9a-f]{64}', block)
            locked[req.name.lower().replace('_', '-')] = req
    pending = ['pytest-cov', 'black', 'socksio', 'zstandard']
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        assert name in locked, 'declared extra dependency absent from existing hashed locks: ' + name
        assert Version(importlib.metadata.version(name)) in locked[name].specifier
        for text in importlib.metadata.requires(name) or []:
            dependency = Requirement(text)
            if dependency.marker and not dependency.marker.evaluate({'extra': ''}):
                continue
            child = dependency.name.lower().replace('_', '-')
            assert child in locked and Version(importlib.metadata.version(child)) in dependency.specifier
            pending.append(child)
