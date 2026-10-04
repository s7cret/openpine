"""Bounded subprocess cleanup shared by verification and CI preparation."""
from __future__ import annotations
import os
import signal
import subprocess
from collections.abc import Callable

def _stop_group(process: subprocess.Popen, grace: float=2.0) -> None:
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    elif process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        if os.name == 'posix':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                return
        else:
            process.kill()
        process.wait(timeout=grace)
    if os.name == 'posix':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:  # noqa: S110 -- absent process/resource is already cleaned up
            pass

def run_logged(argv: list[str], *, cwd, output, env: dict[str, str], timeout: int=900, stdin: bytes | None=None, inputs: dict | None=None, binding: dict | None=None, artifacts: dict | Callable[[], dict] | None=None) -> dict:
    """Run a declared command with no shell; retain failed and cancelled evidence."""
    import time
    from datetime import datetime, timezone
    from pathlib import Path
    from openpine.verification.execution_identity import hash_file, write_once_json
    from openpine.verification.identity import seal
    if not argv or any((not isinstance(v, str) or '\x00' in v for v in argv)) or (not Path(argv[0]).is_absolute()) or (type(timeout) is not int) or (timeout < 1):
        raise ValueError('need absolute executable, argv and a positive timeout')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    provenance = {}
    for index, (name, spec) in enumerate((inputs or {}).items()):
        source = Path(spec['path'])
        if source.is_symlink() or hash_file(source) != spec['sha256']:
            raise ValueError('command input provenance differs before execution')
        captured = output / f'input-{index}.bin'
        captured.write_bytes(source.read_bytes())
        if hash_file(captured) != spec['sha256']:
            raise ValueError('command input provenance capture drift')
        provenance[name] = {'source': dict(spec), 'captured': {'path': captured.name, 'sha256': spec['sha256']}}
    def check_tool_aliases():
        import shutil
        for name, row in provenance.items():
            if not name.startswith('owner-tool:'):
                continue
            spec = row['source']
            # Old single-tool callers bind argv directly. Auxiliary tools need
            # the frozen launch alias; a resolved target alone is insufficient.
            alias = spec.get('declared_path')
            if alias is None:
                if len([n for n in provenance if n.startswith('owner-tool:')]) != 1:
                    raise ValueError('command tool provenance lacks declared alias')
                alias = argv[0]
            candidates = [alias]
            if spec.get('is_argv_executable') is True:
                candidates.append(argv[0])
            if name == 'owner-tool:node':
                search = os.get_exec_path(env)
                search = [str(Path(cwd) / p) if not Path(p).is_absolute() else p for p in search]
                candidates.append(shutil.which('node', path=os.pathsep.join(search)))
            elif name == 'owner-tool:chromium':
                candidates.append(env.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH'))
            for candidate in candidates:
                if not isinstance(candidate, str) or not Path(candidate).is_absolute():
                    raise ValueError('command tool provenance lacks absolute executable alias')
                target = Path(candidate).resolve(strict=True)
                if target != Path(spec['path']) or hash_file(target) != spec['sha256']:
                    raise ValueError('command tool alias provenance differs from frozen target')

    process, error, status, returncode = (None, None, 'not_run', None)
    started, tick = (datetime.now(timezone.utc).isoformat(), time.perf_counter())
    input_file = output / 'stdin.bin'
    if stdin is not None:
        input_file.write_bytes(stdin)
    try:
        with (output / 'stdout.log').open('xb') as out, (output / 'stderr.log').open('xb') as err:
            with input_file.open('rb') if stdin is not None else open(os.devnull, 'rb') as inp:
                check_tool_aliases()
                process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=inp, stdout=out, stderr=err, start_new_session=os.name == 'posix')  # noqa: S603, S607 -- declared argv, shell=False; exit status is checked
                try:
                    returncode = process.wait(timeout=timeout)
                    status = 'completed' if returncode == 0 else 'failed'
                except subprocess.TimeoutExpired:
                    status = 'timeout'
                    _stop_group(process)
                    returncode = process.poll()
                except KeyboardInterrupt:
                    status = 'cancelled'
                    _stop_group(process)
                    returncode = process.poll()
    except (OSError, ValueError, RuntimeError) as caught:
        status, error = ('infrastructure_error', str(caught))
    finally:
        if process is not None:
            if process.poll() is None:
                _stop_group(process)
            elif os.name == 'posix':
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:  # noqa: S110 -- absent process/resource is already cleaned up
                    pass
                else:
                    _stop_group(process)
                    if status == 'completed':
                        status, error = ('failed', 'orphan process after command exit')
    try:
        check_tool_aliases()
    except (OSError, ValueError, RuntimeError) as caught:
        status, error = ('failed', 'command tool provenance changed or invalid: ' + str(caught))
    for name, row in provenance.items():
        try:
            row['after_sha256'] = hash_file(Path(row['source']['path']))
        except (OSError, ValueError):
            row['after_sha256'] = None
        if row['after_sha256'] != row['source']['sha256']:
            status, error = ('failed', 'command input provenance changed during execution')
    files = {p.name: hash_file(p) for p in output.iterdir() if p.is_file()}
    if callable(artifacts):
        artifacts = artifacts() if status == 'completed' else None
    report = seal({'schema_id': 'openpine.execution_command.v1', 'argv': argv, 'cwd': str(cwd), 'started_at': started, 'finished_at': datetime.now(timezone.utc).isoformat(), 'wall_seconds': time.perf_counter() - tick, 'status': status, 'returncode': returncode, 'error': error, 'files': files, 'ok': status == 'completed' and returncode == 0 and (error is None), 'environment_keys': sorted(env), 'input_provenance': provenance, 'binding': binding, 'artifacts': artifacts, 'full_stage_accepted': False})
    write_once_json(output / 'command.json', report)
    return report
