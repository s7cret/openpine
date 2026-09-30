#!/usr/bin/env python3
"""FIX06 external orchestration; source/environment owners remain authoritative."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.dont_write_bytecode = True
import tarfile
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile

BASE = Path(__file__).resolve().parent
NAMES = ('openpine', 'openpine-contracts', 'pine2ast', 'ast2python', 'pinelib', 'backtest_engine', 'marketdata-provider', 'optimizer')

def sha(path):
    return 'sha256:' + hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')

class Runner:
    def __init__(self, output, inputs):
        self.output = output
        self.inputs = inputs
        self.commands = []
        self.cwd = output / 'cwd'
        self.cwd.mkdir()
        (output / 'logs').mkdir()
        self.env = {k: v for k, v in os.environ.items() if k in ('PATH', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'UV_INDEX_URL', 'PIP_INDEX_URL')}
        for name in ('home', 'tmp', 'cache'):
            (output / name).mkdir()
        self.env.update(HOME=str(output/'home'), TMPDIR=str(output/'tmp'), XDG_CACHE_HOME=str(output/'cache'), UV_CACHE_DIR=str(output/'cache'/'uv'), PYTHONDONTWRITEBYTECODE='1', OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', PYTHONHASHSEED='0')

    def run(self, label, argv, cwd=None, expected=0, env=None):
        from openpine.verification.execution_process import run_logged
        folder = self.output/'logs'/f'{len(self.commands):03d}-{label}'
        metrics = folder/'resources.json'
        command = [sys.executable, str(BASE/'measure.py'), str(metrics), *map(str, argv)]
        raw = run_logged(command, cwd=cwd or self.cwd, output=folder, env=env or self.env, timeout=1200, inputs=self.inputs)
        code = raw['returncode']
        row = {'id': label, 'argv': list(map(str, argv)), 'measured_argv': command, 'cwd': str(cwd or self.cwd), 'exit_code': code, 'expected_exit': expected, 'wall_s': raw['wall_seconds'], 'log': {'path': str((folder/'stdout.log').relative_to(self.output)), 'sha256': sha(folder/'stdout.log')}, 'command': {'path': str((folder/'command.json').relative_to(self.output)), 'sha256': sha(folder/'command.json')}}
        row['stderr'] = {'path': str((folder/'stderr.log').relative_to(self.output)), 'sha256': sha(folder/'stderr.log')}
        write(folder/'process-result.json', raw)
        row['execution'] = {'path': str((folder/'process-result.json').relative_to(self.output)), 'sha256': sha(folder/'process-result.json')}
        if metrics.exists():
            row['resources'] = json.loads(metrics.read_text())
            row['metrics'] = {'path': str(metrics.relative_to(self.output)), 'sha256': sha(metrics)}
        row['ok'] = (code == expected if expected is not None else code is not None and code not in (0,127)) and raw['error'] is None and raw['status'] in ('completed','failed')
        self.commands.append(row)
        write(self.output/'commands.json', self.commands)
        print(label, code, flush=True)
        return row

    def require(self, *args, **kwargs):
        row = self.run(*args, **kwargs)
        if not row['ok']:
            raise RuntimeError(f"command failed: {row['id']} ({row['log']['path']})")
        return row

def snapshot(runner, roots, builder, label):
    dest = runner.output / (label+'.json')
    code = "import sys,json; from pathlib import Path; roots=json.loads(sys.argv[1]); sys.path[:0]=list(roots.values()); from openpine.verification.execution_identity import source_snapshot; Path(sys.argv[2]).write_text(json.dumps(source_snapshot({k:Path(v) for k,v in roots.items()}),sort_keys=True,indent=2)+'\\n')"
    runner.require(label, [builder, '-I', '-B', '-c', code, json.dumps(roots), dest])
    return json.loads(dest.read_text())

def copy_sources(runner, roots, snap, git_roots):
    copied = {}
    commits = {}
    provenance = {}
    for name in NAMES:
        root = Path(roots[name])
        git_root = Path(git_roots.get(name, root))
        def git(*argv):
            return subprocess.check_output(['git', '--no-optional-locks', '-C', str(git_root), *argv], text=True).strip()
        head = git('rev-parse', 'HEAD')
        origin = git('config', '--get', 'remote.origin.url')
        commits[name] = head
        provenance[name] = {'metadata_root': str(git_root), 'head': head, 'origin': origin, 'status': git('status', '--porcelain', '--untracked-files=all')}
        dest = runner.output/'build-copies'/name
        dest.parent.mkdir(exist_ok=True)
        runner.require('clone-'+name, ['git', 'clone', '--no-local', '--no-checkout', '--depth', '1', git_root.resolve().as_uri(), str(dest)])
        runner.require('index-'+name, ['git', '-C', dest, 'read-tree', head])
        runner.require('origin-'+name, ['git', '-C', dest, 'remote', 'set-url', 'origin', origin])
        for relative in snap['components'][name]['files']:
            target = dest/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root/relative, target)
        copied[name] = str(dest)
    write(runner.output/'git-provenance.json', provenance)
    write(runner.output/'commits.json', {k:commits[k] for k in ('pine2ast','ast2python')})
    return copied

def artifacts(runner, copies, builder):
    rows = {}
    for name in NAMES:
        dist = runner.output/'artifacts'/name
        dist.mkdir(parents=True)
        runner.require('build-wheel-'+name, [builder, '-I', '-m', 'build', '--no-isolation', '--wheel', '--outdir', dist, copies[name]])
        runner.require('build-sdist-'+name, [builder, '-I', '-m', 'build', '--no-isolation', '--sdist', '--outdir', dist, copies[name]])
        wheels = list(dist.glob('*.whl'))
        sdists = list(dist.glob('*.tar.gz'))
        assert len(wheels) == len(sdists) == 1, name
        sdist = sdists[0]
        unpack = runner.output/'sdist-copies'/name
        unpack.mkdir(parents=True)
        with tarfile.open(sdist) as archive:
            for member in archive.getmembers():
                path = Path(member.name)
                assert not path.is_absolute() and '..' not in path.parts and (member.isfile() or member.isdir()), member.name
            archive.extractall(unpack, filter='data')
        extracted = list(unpack.iterdir())
        assert len(extracted) == 1
        rebuilt = runner.output/'rebuilt'/name
        rebuilt.mkdir(parents=True)
        runner.require('sdist-rebuild-'+name, [builder, '-I', '-m', 'build', '--no-isolation', '--wheel', '--outdir', rebuilt, extracted[0]])
        rebuilt_wheel = list(rebuilt.glob('*.whl'))
        assert len(rebuilt_wheel) == 1
        with zipfile.ZipFile(wheels[0]) as wheel, zipfile.ZipFile(rebuilt_wheel[0]) as other:
            # Dist-info provenance can differ after tar extraction. Runtime/resource payload must match.
            payload = {n: hashlib.sha256(wheel.read(n)).hexdigest() for n in wheel.namelist() if not '.dist-info/' in n and not n.endswith('/')}
            rebuilt_payload = {n: hashlib.sha256(other.read(n)).hexdigest() for n in other.namelist() if not '.dist-info/' in n and not n.endswith('/')}
            assert payload == rebuilt_payload, ('sdist payload differs', name)
            if name == 'openpine':
                assert 'openpine/verification/rc6_catalog_source_pin.json' in payload
        rows[name] = {'wheel': {'path': str(wheels[0].relative_to(runner.output)), 'sha256': sha(wheels[0])}, 'sdist': {'path': str(sdist.relative_to(runner.output)), 'sha256': sha(sdist)}, 'rebuilt_wheel': {'path': str(rebuilt_wheel[0].relative_to(runner.output)), 'sha256': sha(rebuilt_wheel[0])}, 'payload_identical': True, 'payload_hashes': payload}
    write(runner.output/'artifacts.json', rows)
    return rows

def installed(runner, interpreters, rows):
    results = {}
    for version, base_python in sorted(interpreters.items()):
        for kind in ('wheel', 'rebuilt_wheel'):
            venv = runner.output/('venv-'+version+'-'+kind)
            runner.require('venv-'+version+'-'+kind, ['uv', 'venv', '--python', base_python, venv])
            python = venv/'bin'/'python'
            runner.require('version-'+version+'-'+kind, [python, '-c', f"import sys; assert '.'.join(map(str,sys.version_info[:2])) == {version!r}; print(sys.version); print(sys.executable)"])
            wheels = [runner.output/rows[n][kind]['path'] for n in NAMES]
            runner.require('install-'+version+'-'+kind, ['uv', 'pip', 'install', '--python', python, '--reinstall', *wheels, 'httpx'])
            runner.require('pip-check-'+version+'-'+kind, ['uv', 'pip', 'check', '--python', python])
            scope = version+'-'+kind
            result_dir = runner.output/'observations'/scope
            result_dir.mkdir(parents=True)
            for mode in ('origins', 'compile', 'runtime', 'library', 'api', 'positive', 'missing', 'tampered'):
                runner.run(scope+'-'+mode, [python, BASE/'probe.py', mode, '--commits', runner.output/'commits.json', '--output', result_dir/(mode+'.json')])
            runner.run(scope+'-cli', [venv/'bin'/'openpine', '--help'])
            runner.run(scope+'-cli-core-check', [venv/'bin'/'openpine', 'core', 'check'])
            poison = runner.output/('poison-'+scope)
            poison.mkdir()
            (poison/'openpine').mkdir()
            (poison/'openpine'/'__init__.py').write_text('__version__="source-shadow-sentinel"\n')
            # Ordinary invocation demonstrably imports cwd; the qualification origin guard must reject it.
            runner.run(scope+'-source-shadow', [python, BASE/'shadow_check.py', poison, BASE/'probe.py', result_dir/'shadow.json'])
            # Isolated mode must ignore the actual cwd poison, not just assert a constant.
            runner.run(scope+'-shadow-isolated', [python, '-I', '-B', '-m', 'openpine.verification.installed_probe'], cwd=poison)
            results[scope] = {p.stem: {'path': str(p.relative_to(runner.output)), 'sha256': sha(p)} for p in result_dir.glob('*.json')}
    return results

def command_specification(command):
    """Independent tiny-fixture expectations, never copied from observed stdout."""
    label = command['id']
    specification = {'argv': command['measured_argv'], 'cwd': command['cwd'], 'role': 'auxiliary'}
    if label.startswith('build-wheel-'):
        specification['role'] = 'wheel'
    elif label.startswith('build-sdist-'):
        specification['role'] = 'sdist'
    elif label.startswith('sdist-rebuild-'):
        specification['role'] = 'sdist-wheel'
    elif label.startswith('install-'):
        specification['role'] = 'install'
    elif label.endswith('-shadow-isolated'):
        specification['role'] = 'probe'
    elif label.endswith(('-compile', '-runtime', '-library')):
        specification['role'] = 'compile' if label.endswith('-compile') else ('run' if label.endswith('-runtime') else 'library')
        specification['expected_stdout'] = {'bars': 5, 'observed': [11.0, 12.0, 13.0, 14.0, 15.0], 'library': label.endswith('-library')}
    elif label.endswith(('-positive', '-missing', '-tampered')):
        mode = label.rsplit('-', 1)[-1]
        specification['role'] = {'positive': 'resources', 'missing': 'missing-resource', 'tampered': 'tampered-resource'}[mode]
        specification['expected_stdout'] = {'mode': mode, 'resource_valid': mode == 'positive', 'changed_versions': [] if mode == 'positive' else [str(v) for v in range(1, 7)]}
    elif label.endswith('-source-shadow'):
        specification['role'] = 'source-shadowing'
        specification['expected_stdout'] = {'source_shadowing_rejected': True, 'child_exit_code': 1, 'shadow_module': 'openpine'}
    elif label.endswith('-api'):
        specification['role'] = 'api'
    return specification


def export_stage(runner, observations, artifacts, interpreters):
    """Locator/specification templates for the existing FIX03 raw reader.

    Parent MUST review/freeze policy commands independently; this template is
    not evidence authority and DEVELOPMENT entries must never be admitted.
    """
    entries, policies = {}, {}
    for version in sorted(interpreters):
        entries[version], policies[version] = {}, {}
        for kind, artifact_kind in (('normal', 'wheel'), ('rebuilt', 'rebuilt_wheel')):
            scope = version + '-' + artifact_kind
            selected = [c for c in runner.commands if c['id'].startswith(('build-', 'sdist-rebuild-', scope+'-')) or c['id'] in {'install-'+scope, 'pip-check-'+scope, 'version-'+scope}]
            entries[version][kind] = {'commands': [c['command'] for c in selected], 'probe': next(c['log'] for c in selected if c['id'] == scope+'-shadow-isolated'), 'artifacts': {n: {'wheel': artifacts[n][artifact_kind], 'sdist': artifacts[n]['sdist']} for n in NAMES}}
            policies[version][kind] = {'python': version, 'commands': [command_specification(c) for c in selected], 'resources': {'openpine': ['openpine/verification/rc6_catalog_source_pin.json']}}
    path = runner.output/'stage-packages-inputs.json'
    write(path, {'gates': {'packages': entries}, 'policy_template_requires_independent_review': {'packages': policies}})
    return {'path': str(path.relative_to(runner.output)), 'sha256': sha(path)}


def check_plan_source(plan, source, *, development):
    if not development and plan.get('source') != source:
        raise ValueError('plan source differs from the frozen build inputs')

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--roots', type=Path, required=True, help='JSON root map or execution plan containing roots')
    p.add_argument('--git-roots', type=Path)
    p.add_argument('--interpreters', type=Path, required=True, help='JSON map 3.11/3.12/3.13 -> executable')
    p.add_argument('--builder', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--frozen-source', type=Path, help='Required exact owner source snapshot for qualified run')
    p.add_argument('--run-id', default='development', help='Parent campaign ID for exact package binding')
    p.add_argument('--development', action='store_true')
    p.add_argument('--reuse-artifacts', type=Path, help='Development only: existing artifacts tree')
    args = p.parse_args()
    plan = json.loads(args.roots.read_text())
    roots = plan
    plan_hash = roots.get('content_hash') if 'roots' in roots else None
    roots = roots.get('roots', roots)
    assert set(roots) == set(NAMES)
    roots = {k:str(Path(v).resolve()) for k,v in roots.items()}
    output = args.output.resolve()
    assert output.is_relative_to(BASE), 'writes restricted to delegated workspace'
    assert not output.exists(), 'attempt output must be new'
    assert all(not output.is_relative_to(Path(v)) and not Path(v).is_relative_to(output) for v in roots.values())
    assert args.development or (args.frozen_source and plan_hash and args.run_id != 'development'), 'qualified run requires parent frozen candidate, exact plan, and campaign ID'
    assert not args.reuse_artifacts or args.development
    interpreters = json.loads(args.interpreters.read_text())
    assert set(interpreters) == {'3.11','3.12','3.13'}
    # Bootstrap existing owners in orchestration ONLY. Installed subprocesses never inherit this sys.path.
    sys.path.insert(0, roots['openpine'])
    output.mkdir(parents=True)
    frozen_policy = json.loads((Path(roots['openpine'])/'verification/execution-policy.json').read_text())['stabilization']
    runner = Runner(output, frozen_policy['package_harness_inputs'])
    write(output/'commands.json', [])
    report = {'schema_id':'openpine.installed_package_receipt.v1', 'owner':'packages', 'scope':'FIX06_installed_wheel_and_sdist', 'development':args.development, 'status':'blocked', 'ok':False, 'full_stage2_accepted':False, 'roots':roots, 'plan_hash':plan_hash, 'run_id':args.run_id, 'harness_sha256':sha(__file__), 'probe_sha256':sha(BASE/'probe.py'), 'helper_hashes':{p.name:sha(p) for p in (BASE/'measure.py',BASE/'shadow_check.py')}}
    try:
        before = snapshot(runner, roots, args.builder, 'source-before')
        report['source_hash'] = before['content_hash']
        report['source'] = {'path':'source-before.json','sha256':sha(output/'source-before.json')}
        if not args.development:
            from openpine.verification.identity import verify
            verify(plan, 'openpine.test_execution_plan.v1')
        check_plan_source(plan, before, development=args.development)
        if args.frozen_source:
            assert before == json.loads(args.frozen_source.read_text()), 'candidate drift before build'
        git_roots = json.loads(args.git_roots.read_text()) if args.git_roots else {}
        copies = copy_sources(runner, roots, before, git_roots)
        copied = snapshot(runner, copies, args.builder, 'copied-source')
        assert copied == before, 'copy changed identity/mode'
        if args.reuse_artifacts:
            rows = {}
            for name in NAMES:
                dist = output/'artifacts'/name
                dist.mkdir(parents=True)
                wheels = list((args.reuse_artifacts/name).glob('*.whl'))
                sdists = list((args.reuse_artifacts/name).glob('*.tar.gz'))
                assert len(wheels) == len(sdists) == 1
                for src in wheels+sdists:
                    shutil.copy2(src, dist/src.name)
                rows[name] = {kind:{'path':str((dist/src.name).relative_to(output)), 'sha256':sha(dist/src.name)} for kind,src in [('wheel',wheels[0]),('sdist',sdists[0]),('rebuilt_wheel',wheels[0])]}
            write(output/'artifacts.json', rows)
            report['artifact_source']='STALE_REUSED_DEVELOPMENT_NOT_BUILD_PROOF'
        else:
            rows = artifacts(runner, copies, args.builder)
        report['observations'] = installed(runner, interpreters, rows)
        report['stage_inputs'] = export_stage(runner, report['observations'], rows, interpreters)
        after = snapshot(runner, roots, args.builder, 'source-after')
        assert before == after, 'candidate drift during qualification'
        assert report['harness_sha256'] == sha(__file__) and report['probe_sha256'] == sha(BASE/'probe.py') and all(sha(BASE/name)==value for name,value in report['helper_hashes'].items()), 'harness changed during qualification'
        report['artifacts'] = {'path':'artifacts.json','sha256':sha(output/'artifacts.json')}
        report['ok'] = all(c['ok'] for c in runner.commands) and not args.development
        report['status'] = 'passed' if report['ok'] else ('development' if args.development else 'failed')
    except Exception:
        report['error'] = traceback.format_exc()
        print(report['error'], flush=True)
    report['commands'] = {'path':'commands.json','sha256':sha(output/'commands.json')}
    report['failed_commands'] = [c['id'] for c in runner.commands if not c['ok']]
    suite = ET.Element('testsuite', name='FIX06-installed-packages', tests=str(len(runner.commands)), failures=str(len(report['failed_commands'])))
    for c in runner.commands:
        test = ET.SubElement(suite, 'testcase', name=c['id'], time=str(c['wall_s']))
        if not c['ok']:
            ET.SubElement(test, 'failure', message=f"actual exit {c['exit_code']}, expected {c['expected_exit']}").text = c['log']['path']
    ET.ElementTree(suite).write(output/'junit.xml', encoding='utf-8', xml_declaration=True)
    report['junit'] = {'path':'junit.xml','sha256':sha(output/'junit.xml')}
    report['content_hash'] = 'sha256:' + hashlib.sha256(json.dumps(report, sort_keys=True, separators=(',',':'), ensure_ascii=False).encode()).hexdigest()
    write(output/'receipt.json', report)
    print(json.dumps({'status':report['status'],'ok':report['ok'],'failed_commands':report['failed_commands'],'error':report.get('error')},indent=2))
    return 0 if report['ok'] else 1

if __name__ == '__main__':
    sys.exit(main())
