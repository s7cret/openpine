"""Select inexpensive R6 checks from the Git tree changed by a batch."""
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import time

from packages.contracts.wire import atomic_json, loads, read_json
from tools.specification import ROOT


SHARED = ('packages/compiler/frontend/', 'packages/compiler/semantic/binder.py',
          'packages/compiler/compile.py', 'packages/compiler/lowering/', 'packages/contracts/model.py',
          'packages/contracts/codec.py', 'packages/contracts/wire.py', 'packages/contracts/scopes.py',
          'packages/runtime/engine.py', 'packages/runtime/admission.py', 'packages/runtime/checkpoints.py',
          'packages/runtime/operations/__init__.py', 'packages/runtime/series/__init__.py',
          'packages/runtime/history.py', 'packages/runtime/history_memory.py', 'packages/runtime/state/',
          'packages/execution/session/', 'packages/execution/driver.py', 'packages/execution/realtime.py',
          'spec/bundles/', 'spec/lock.json', 'requirements-dev.txt', 'pyproject.toml', '.python-version')
ALIASES = {
    'array': ('arrays',), 'arrays': ('arrays', 'array-operations', 'array-order', 'array-slice', 'object-arrays',
                                    'chart-points', 'polylines', 'maps', 'matrices'),
    'band': ('visual-bands',), 'bands': ('visual-bands',), 'box': ('boxes',),
    'chart-points': ('chart-points', 'polylines', 'line-styles', 'box-text', 'label-text'),
    'composite-results': ('composite',),
    'drawing-xloc': ('drawing-xloc', 'drawing-xloc-mutations'),
    'drawings': ('labels', 'lines', 'boxes', 'tables', 'linefills', 'polylines'),
    'function': ('functions',), 'input': ('inputs',), 'line': ('lines',),
    'lines': ('lines', 'line-styles', 'line-extensions', 'linefills', 'drawing-xloc'),
    'loop': ('loops',), 'markers': ('visual-signals',), 'partial-exit': ('partial-exits',),
    'pending-entry': ('pending-entries',), 'lot': ('lots',), 'order-group': ('order-groups',),
    'signal': ('signals',), 'strategy': ('strategies',),
}
TOOL_TESTS = {
    'tools/batch_feedback.py': 'test_batch_feedback.py',
    'tools/conformance.py': 'test_batch_feedback.py',
    'tools/release_gate.py': 'test_ci_artifacts.py',
    'tools/semantic_matrix.py': 'test_semantic_matrix.py',
    '.github/workflows/r6-feedback.yml': 'test_batch_feedback.py',
    '.github/workflows/r6-specification.yml': 'test_ci_artifacts.py',
}


def git(*args, allow_missing=False):
    result = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, check=not allow_missing)
    return None if result.returncode else result.stdout


def changed_paths(base):
    if base == 'github-event':
        event = read_json(Path(os.environ['GITHUB_EVENT_PATH']))
        base = event['pull_request']['base']['sha'] if 'pull_request' in event else event['before']
    commit = git('rev-parse', '--verify', '--end-of-options', base + '^{commit}', allow_missing=True)
    if commit is None:
        return None, sorted(filter(None, git('ls-files', '-z').decode('utf-8').split('\0')))
    revision = commit.decode('ascii').strip()
    tracked = git('diff', '--name-only', '--no-renames', '-z', revision, '--')
    untracked = git('ls-files', '--others', '--exclude-standard', '-z')
    return revision, sorted(set(filter(None, (tracked + untracked).decode('utf-8').split('\0'))))


def stem(path):
    name = PurePosixPath(path).stem.removeprefix('test_')
    for suffix in ('_spec', '_fixtures', '_examples', '-profile', '-evidence', '-legacy-abi'):
        name = name.removesuffix(suffix)
    return name.replace('_', '-')


def select(paths, corpora, tests, *, missing_base=False):
    if missing_base:
        return sorted(corpora), [], []
    selected, unit, unmapped = set(), set(), []
    broad = False
    for path in paths:
        if path.startswith(('docs/', '.agents/', 'conformance/results/')) or path in ('README.md', 'AGENTS.md'):
            continue
        if path in TOOL_TESTS:
            unit.add(TOOL_TESTS[path])
            continue
        if any(path == name or name.endswith('/') and path.startswith(name) for name in SHARED):
            broad = True
            continue
        parts = PurePosixPath(path).parts
        corpus_path = parts[0] == 'conformance' and len(parts) > 2
        key = parts[1] if corpus_path else stem(path)
        targets = set((key,) if corpus_path else ALIASES.get(key, (key,))) & corpora
        module = parts[-1]
        if parts[0] == 'tests' and module in tests:
            unit.add(module)
        elif not targets:
            fallback = 'test_' + key.replace('-', '_') + '.py'
            if fallback in tests:
                unit.add(fallback)
            else:
                unmapped.append(path)
        selected.update(targets)
    return sorted(corpora if broad or unmapped else selected), sorted(unit), unmapped


def smoke_cases(path, changes, old=None):
    cases = read_json(path)['cases']
    relative = path.parent.relative_to(ROOT).as_posix()
    edited = [case for case in cases if f'{relative}/{case["source"]}' in changes
              or old is not None and old.get(case['case_id']) != case]
    executed = [case for case in cases if case['expected']['status'] == 'executed']
    focused = next((case for case in edited if case['expected']['status'] == 'executed'), None)
    representative = focused or max(executed or cases, key=lambda case: case['language_version'])
    rejection = next((case for case in edited if case['expected']['status'] == 'rejected'), None)
    return tuple(dict.fromkeys(case['case_id'] for case in (representative, rejection) if case))


def run(base, output):
    from tools.ci_artifacts import source_identity
    from packages.storage.trade_index import TradeIndex
    from tools.conformance import Session, run_cases
    from tools.release_gate import GENERATORS

    started = time.monotonic()
    before = source_identity()
    revision, paths = changed_paths(base)
    corpora = {path.parent.name: path for path in (ROOT / 'conformance').glob('*/cases.json')}
    selected, tests, unmapped = select(paths, corpora.keys(), {path.name for path in (ROOT/'tests').glob('test_*.py')},
                                       missing_base=revision is None)
    if revision is None:
        print('Git base unavailable. Running all profile smokes.', flush=True)
    if unmapped:
        print('Unmapped paths. Running all profile smokes: ' + ', '.join(unmapped), flush=True)
    print('Batch smoke profiles: ' + (', '.join(selected) or '(none)'), flush=True)
    for name in paths:
        path = ROOT / name
        if path.suffix == '.py' and path.is_file():
            compile(path.read_bytes(), name, 'exec')

    def check(*args):
        subprocess.run([sys.executable, '-X', 'utf8', *args], cwd=ROOT, check=True)

    check('-m', 'tools.verify_architecture')
    check('-m', 'tools.verify_spec')
    check('-m', 'tools.semantic_matrix', '--check')
    if revision is not None:
        affected = {stem(path) for path in paths if not path.startswith(('docs/', 'conformance/results/'))}
        if len(selected) != len(corpora):
            affected.update(selected)
        for generator in GENERATORS:
            if stem(generator) in affected:
                check('-m', 'tools.' + generator, '--check')
    for module in tests:
        check('-m', 'tools.test_runner', '--module', module)

    sessions, observations = {}, []
    for name in selected:
        path = corpora[name]
        relative = path.relative_to(ROOT).as_posix()
        old = None
        if revision and relative in paths:
            raw = git('show', f'{revision}:{relative}', allow_missing=True)
            if raw is not None:
                old = {case['case_id']: case for case in loads(raw)['cases']}
        profile = read_json(path)['implementation_profile_id']
        if profile not in sessions:
            sessions[profile] = Session(profile_id=profile, trade_history_factory=TradeIndex)
        ids = smoke_cases(path, paths if revision else (), old)
        report = run_cases(sessions[profile], cases_path=relative, case_ids=ids)
        observations.append({'corpus': relative, 'cases': [row['case_id'] for row in report['results']]})
        print(f'{name}: {", ".join(ids)}', flush=True)
    if source_identity() != before:
        raise ValueError('Source changed during batch feedback.')
    receipt = {'status': 'passed', 'mode': 'batch-feedback', 'commit': git('rev-parse', 'HEAD').decode().strip(),
               'source_sha256': before['sha256'], 'base': revision, 'paths': paths, 'unmapped': unmapped,
               'smoke': observations, 'test_modules': tests, 'seconds': round(time.monotonic() - started, 3)}
    if output:
        output.mkdir(parents=True, exist_ok=True)
        atomic_json(output / 'batch.json', receipt)
    print(f"R6 batch feedback passed in {receipt['seconds']}s.", flush=True)
    return 0
