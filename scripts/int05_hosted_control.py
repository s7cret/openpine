"""One reviewed hosted control using unchanged product owners, never acceptance.

The workflow checkout is a separate transport adapter. Product sources, wheels,
plans, execution receipts and gate authority come from the frozen candidate.
The existing public auditor remains fail closed for unsupported source inputs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig


CANDIDATE = '4eee24fc458a2099f000211850f720ccfc6bfb2b'
COMMITS = {
    'openpine': CANDIDATE,
    'openpine-contracts': 'db1745756516b47466756c9c5d38fbbb95595a3b',
    'pine2ast': 'eb249402e67199b07e9880fadc14e03fb1651edc',
    'ast2python': '9080ed559e5cd9acbfe1400f284314d2af3f9193',
    'pinelib': 'b953e803d618a9784b117f8047ca709791aaca79',
    'backtest_engine': 'd9210fbb6a72689e76da918c2eba422b22f439f6',
    'marketdata-provider': 'da6c25c55289cea4cbb9329997c165abc1b2af5e',
    'optimizer': '623e2639581d23242109dd47e20f14f799fa7b88',
}
SOURCE_HASH = 'sha256:725720c3dfedf6818b1bb3ddb06657643ccf2853bcce5692833b6bd74b5cad8a'
HARD_FLOOR = 18 * 1024**3
SOFT_FLOOR = 20 * 1024**3


def require_candidate(commits):
    if commits != COMMITS:
        raise ValueError('exact reviewed eight-package candidate required')


def preflight(root):
    # Before bootstrap: use only the official setup-python stdlib.
    root.mkdir(parents=True, exist_ok=False)
    pid = os.getpid()
    children = Path(f'/proc/{pid}/task/{pid}/children').read_text()
    facts = {
        'python': sys.version, 'executable': sys.executable,
        'ensurepip': importlib.util.find_spec('ensurepip') is not None,
        'gil_enabled': sys._is_gil_enabled(),
        'free_threaded': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
        'os_release': platform.freedesktop_os_release(),
        'systemd_pid1': Path('/proc/1/comm').read_text().strip(),
        'procfs_children_readable': isinstance(children, str),
        'cpu_count': os.cpu_count(), 'disk_free_bytes': shutil.disk_usage(root).free,
        'candidate_commit': subprocess.check_output(  # noqa: S603 -- fixed read-only Git identity
            [shutil.which('git'), 'rev-parse', 'HEAD'], text=True).strip(),
        'workflow_commit': os.environ['INT05_WORKFLOW_SHA'],
        'artifact_ceiling_bytes': int(os.environ['ARTIFACT_BUDGET']),
        'retention_days': int(os.environ['RETENTION_DAYS']),
        'retries': 0, 'full_stage_accepted': False,
    }
    (root / 'runner.json').write_text(json.dumps(facts, indent=2) + '\n')
    if (sys.version_info[:3] != (3, 13, 5) or not facts['ensurepip']
            or not facts['gil_enabled'] or facts['free_threaded']
            or facts['os_release']['VERSION_ID'] != '24.04'
            or facts['systemd_pid1'] != 'systemd' or (os.cpu_count() or 0) < 4
            or facts['candidate_commit'] != CANDIDATE
            or facts['disk_free_bytes'] < SOFT_FLOOR
            or not 0 < facts['artifact_ceiling_bytes'] <= 512 * 1024**2
            or facts['retention_days'] != 1):
        raise ValueError('hosted control prerequisites or reviewed budget unavailable')


def freeze(root):
    from openpine.verification.execution_binding import make_binding
    from openpine.verification.execution_ci import (
        attest_ci_source_commits, make_ci_plan, make_owner_locations, verify_bundle,
    )
    from openpine.verification.execution_identity import hash_file, write_once_json
    from openpine.verification.execution_plan import validate_plan
    from openpine.verification.identity import read_json, seal
    bundle = root / 'prepare/bundle'
    prepared = verify_bundle(bundle)
    commits = attest_ci_source_commits([prepared])
    require_candidate(commits)
    if prepared['source']['content_hash'] != SOURCE_HASH:
        raise ValueError('stock prepare source differs from reviewed product bytes')
    restored = read_json(root / 'restored/restored.json')
    if restored['environment'] != prepared['environment'] or restored['source_commits'] != commits:
        raise ValueError('restored hosted identity differs from prepared lane')
    for name in ('exact-owner', 'package-owner'):
        (root / name).mkdir(exist_ok=False)
    locations = make_owner_locations(
        {'py313': restored}, stack_root=root / 'restored/stack',
        attempt=root / 'exact-owner', package_attempt=root / 'package-owner',
        npm=Path(shutil.which('npm')), node=Path(shutil.which('node')),
        chromium=Path(os.environ['INT05_CHROMIUM']),
    )
    raw = root / 'raw'
    raw.mkdir(exist_ok=False)
    write_once_json(raw / 'owner-locations.json', locations)
    roots = {name: Path(path) for name, path in restored['roots'].items()}
    native = make_ci_plan([bundle / 'collection.json'], roots, raw / 'native-full.json',
                          commits, owner_locations=locations)
    plan = seal({**{k: v for k, v in native.items() if k != 'content_hash'},
                 'disk_free_guard': {'minimum_free_bytes': HARD_FLOOR}})
    validate_plan(plan)
    write_once_json(raw / 'full.json', plan)
    binding = make_binding(plan, roots, {'py313': restored['executable']})
    write_once_json(raw / 'binding.json', binding)
    write_once_json(raw / 'refreeze.json', seal({
        'candidate_commit': CANDIDATE, 'workflow_commit': os.environ['INT05_WORKFLOW_SHA'],
        'source_hash': plan['source']['content_hash'], 'source_commits': commits,
        'collection_hash': read_json(bundle / 'collection.json')['content_hash'],
        'prepared_bundle_hash': prepared['content_hash'], 'plan_hash': plan['content_hash'],
        'environment_hash': prepared['environment']['content_hash'],
        'source_archive_sha256': hash_file(bundle / 'sources.tar.gz'),
        'required_count': sum(len(task['nodeids']) for task in plan['tasks']),
        'nodeid_hashes': {task['id']: task['nodeids_hash'] for task in plan['tasks']},
        'required_gates': plan['required_gates'], 'execution_status': 'NOT_RUN',
        'old_anchor_aliases': [], 'retries': 0, 'full_stage_accepted': False,
    }))


def control(root, run_id):
    from openpine.verification.execution_campaign import aggregate_campaign, run_campaign
    from openpine.verification.execution_identity import write_once_json
    from openpine.verification.identity import read_json, seal
    path = root / 'raw/full.json'
    plan = read_json(path)
    require_candidate(plan['source_commits'])
    output = root / 'raw/control-full'
    run_campaign(plan, path, output, jobs=2, max_parallel_shards=1,
                 memory_mib=6144, run_id=run_id,
                 binding=read_json(root / 'raw/binding.json'), build_commit=CANDIDATE)
    report = aggregate_campaign(plan, output, expected_plan_hash=plan['content_hash'],
                                expected_run_id=run_id)
    write_once_json(output / 'aggregate.json', report)
    write_once_json(root / 'raw/control-checkpoint.json', seal({
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'candidate_commit': CANDIDATE, 'workflow_commit': os.environ['INT05_WORKFLOW_SHA'],
        'aggregate': report, 'control_campaigns': 1, 'mutant_campaigns': 0,
        'retries': 0, 'full_stage_accepted': False, 'full_release_accepted': False,
        'remaining_provider5_live_and_spec68_mandatory': True,
    }))
    if not report['pytest_scope_passed']:
        raise ValueError('control pytest obligations failed; original evidence retained')


def retain(root, run_id):
    from openpine.verification.execution_evidence import archive_evidence, verify_archive
    from openpine.verification.execution_identity import write_once_json
    from openpine.verification.identity import seal
    # Import the product's existing publication owner, never the workflow copy.
    from scripts.rc6_public_evidence import audit, check_upload_payload
    raw = root / 'raw'
    raw.mkdir(exist_ok=True)
    for source, relative in ((root / 'runner.json', 'runner.json'),
                             (root / 'prepare/host.tar', 'committed-host.tar'),
                             (root / 'prepare/bundle', 'prepared-bundle'),
                             (root / 'prepare/commands', 'prepare-commands'),
                             (root / 'restored/commands', 'restore-commands'),
                             (root / 'restored/restored.json', 'restored.json'),
                             (root / 'prepare-resource', 'prepare-resource'),
                             (root / 'restore-resource', 'restore-resource')):
        if source.is_dir():
            shutil.copytree(source, raw / relative)
        elif source.is_file():
            shutil.copy2(source, raw / relative)
    write_once_json(raw / 'transport-scope.json', seal({
        'candidate_commit': CANDIDATE, 'workflow_commit': os.environ['INT05_WORKFLOW_SHA'],
        'original_primary_bytes_changed': False, 'required_inputs_omitted': False,
        'includes': 'prepared source archive, all Git provenance bundles, wheelhouse, identities, collections, plans, phases, JUnit and failed command logs',
        'excludes': 'reproducible installed venvs and caches; explicit private trees only',
        'full_product_qualified': False,
    }))
    maximum = int(os.environ['ARTIFACT_BUDGET'])
    private = tuple(path.relative_to(raw).as_posix() for path in raw.rglob('private') if path.is_dir())
    manifest = archive_evidence(raw, root / 'archive', candidate=CANDIDATE, run_id=run_id,
                                retention_days=1, max_bytes=maximum, private=private,
                                roots={'openpine': Path.cwd()})
    verify_archive(root / 'archive/evidence.tar.gz', manifest,
                   expected_manifest_hash=manifest['content_hash'],
                   expected_candidate=CANDIDATE, expected_run_id=run_id)
    report = audit(raw, candidate=CANDIDATE, run_id=run_id, max_bytes=maximum)
    write_once_json(root / 'public-evidence-audit.json', report)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write('public_ready=false\n')
    if not report['ok']:
        raise ValueError('complete evidence is retained; existing public owner rejects required source/primary scope; reviewed durable transport required before dispatch')
    check_upload_payload(root / 'archive', root / 'public-evidence-audit.json', max_bytes=maximum)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as stream:
        stream.write('public_ready=true\n')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('preflight', 'freeze', 'control', 'retain'))
    args = parser.parse_args()
    root = Path(os.environ['RUNNER_TEMP']) / 'int05'
    run_id = 'int05-control-' + os.environ['GITHUB_RUN_ID'] + '-' + os.environ['GITHUB_RUN_ATTEMPT']
    if args.action == 'preflight':
        preflight(root)
    elif args.action == 'freeze':
        freeze(root)
    elif args.action == 'control':
        control(root, run_id)
    else:
        retain(root, run_id)


if __name__ == '__main__':
    main()
