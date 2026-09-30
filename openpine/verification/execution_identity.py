"""Input identities and immutable evidence I/O for the existing verification system.

An input identity never includes execution receipts. Evidence must live outside
all source roots. Hashes attest to bytes, not to independent Pine correctness.
"""
from __future__ import annotations
import hashlib
import importlib.metadata
import os
import platform
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from openpine.verification.identity import canonical, read_json, seal
SOURCE_SCHEMA = 'openpine.execution_sources.v1'
ENV_SCHEMA = 'openpine.execution_environment.v1'
ROOT_OUTPUT_DIRS = frozenset({'.git', '.pytest_cache', '.mypy_cache', '.ruff_cache', '.marketdata-cache', '.venv', 'venv', 'build', 'dist'})
NESTED_OUTPUT_DIRS = frozenset({'__pycache__', 'node_modules'})

def hash_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f'expected regular file: {path}')
    before = path.stat()
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    after = path.stat()
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError(f'input changed while being read: {path}')
    return 'sha256:' + value.hexdigest()

def _excluded(relative: Path) -> bool:
    parts = relative.parts
    return any((p in NESTED_OUTPUT_DIRS for p in parts)) or parts[0] in ROOT_OUTPUT_DIRS or parts[0].endswith('.egg-info') or (len(parts) > 1 and parts[0] == 'openpine-ui' and (parts[1] in {'dist', '.vite', '.vitest'} or relative.name.endswith('.tsbuildinfo'))) or (relative.name in {'.coverage'}) or relative.name.startswith('.coverage.')

def source_snapshot(roots: Mapping[str, Path]) -> dict:
    if not roots:
        raise ValueError('no source roots')
    components: dict[str, dict] = {}
    resolved: list[Path] = []
    for name, original in sorted(roots.items()):
        root = Path(original)
        if not name or root.is_symlink() or (not root.is_dir()):
            raise ValueError(f'invalid source root: {name}')
        root = root.resolve()
        if any((root == p or root.is_relative_to(p) or p.is_relative_to(root) for p in resolved)):
            raise ValueError('source roots overlap')
        resolved.append(root)
        files = {}
        for directory, subdirs, filenames in os.walk(root, followlinks=False):
            base = Path(directory)
            kept = []
            for child in sorted(subdirs):
                path = base / child
                if _excluded(path.relative_to(root)):
                    continue
                if path.is_symlink():
                    raise ValueError(f'source symlink is not admitted: {path}')
                kept.append(child)
            subdirs[:] = kept
            for filename in sorted(filenames):
                path = base / filename
                relative = path.relative_to(root)
                if _excluded(relative):
                    continue
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise ValueError(f'source is not a regular file: {path}')
                files[relative.as_posix()] = {'sha256': hash_file(path), 'executable': bool(mode & 73), 'size': path.stat().st_size}
        if not files:
            raise ValueError(f'empty source root: {name}')
        components[name] = {'files': files, 'file_count': len(files)}
    return seal({'schema_id': SOURCE_SCHEMA, 'policy': 'execution-inputs-v2', 'components': components})

def environment_snapshot() -> dict:
    distributions = {}
    for dist in importlib.metadata.distributions():
        name = (dist.metadata.get('Name') or '').lower().replace('_', '-').replace('.', '-')
        if not name:
            continue
        version = dist.version
        if name in distributions and distributions[name] != version:
            raise ValueError(f'conflicting installed distribution: {name}')
        distributions[name] = version
    return seal({'schema_id': ENV_SCHEMA, 'implementation': platform.python_implementation(), 'python': platform.python_version(), 'platform': platform.platform(), 'machine': platform.machine(), 'executable_sha256': hash_file(Path(sys.executable).resolve()), 'distributions': dict(sorted(distributions.items()))})

def evidence_path(root: Path, relative: str, *, must_exist: bool=True) -> Path:
    """No absolute paths, Windows drive syntax, traversal or symlink ancestors."""
    if not isinstance(relative, str) or not relative or '\\' in relative or (':' in relative):
        raise ValueError('invalid relative evidence path')
    pure = PurePosixPath(relative)
    if pure.is_absolute() or any((p in {'', '.', '..'} for p in relative.split('/'))):
        raise ValueError('invalid relative evidence path')
    if root.is_symlink():
        raise ValueError('evidence root cannot be a symlink')
    root = root.resolve()
    current = root
    for part in pure.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('evidence path crosses a symlink')
    if not current.resolve().is_relative_to(root):
        raise ValueError('evidence path leaves root')
    if must_exist and (not current.is_file()):
        raise ValueError(f'missing evidence file: {relative}')
    return current

def ensure_external_output(output: Path, roots: Mapping[str, Path]) -> None:
    if output.is_symlink():
        raise ValueError('execution output cannot be a symlink')
    resolved = output.resolve()
    if any((resolved == Path(p).resolve() or resolved.is_relative_to(Path(p).resolve()) for p in roots.values())):
        raise ValueError('execution output must be outside source roots')


def ensure_external_outputs(outputs: tuple[Path, ...], roots: Mapping[str, Path]) -> None:
    """Validate every declared output before any source-derived work starts."""
    if not outputs:
        raise ValueError('no execution outputs')
    resolved = []
    for output in outputs:
        ensure_external_output(output, roots)
        resolved.append(output.resolve())
    if len(set(resolved)) != len(resolved):
        raise ValueError('execution outputs must not overlap')

def write_once_json(path: Path, value: Any) -> None:
    """No overwritten attempts. Publish only fully written files via a hard link."""
    import tempfile
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or path.exists():
        raise ValueError(f'evidence already exists: {path}')
    fd, temporary = tempfile.mkstemp(prefix='.evidence-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)

def read_artifact(root: Path, descriptor: Mapping[str, str]) -> Any:
    path = evidence_path(root, descriptor['path'])
    if hash_file(path) != descriptor['sha256']:
        raise ValueError(f"evidence checksum mismatch: {descriptor['path']}")
    return read_json(path)

def clean_environment(roots: dict[str, str], private: Path, *, build_commit: str | None=None) -> dict[str, str]:
    if build_commit is not None and (not isinstance(build_commit, str) or re.fullmatch('[0-9a-f]{40}', build_commit) is None):
        raise ValueError('invalid exact build commit')
    private.mkdir(parents=True, exist_ok=True)
    for name in ('home', 'tmp', 'cache'):
        (private / name).mkdir(exist_ok=True)
    result = {key: value for key, value in os.environ.items() if key in {'PATH', 'SYSTEMROOT', 'WINDIR', 'LANG', 'LC_ALL', 'TZ'}}
    result['OPENPINE_SOURCE_ROOTS'] = __import__('json').dumps(roots, sort_keys=True)
    result.update(PYTHONPATH=os.pathsep.join((roots[name] for name in sorted(roots))), PYTEST_DISABLE_PLUGIN_AUTOLOAD='1', PYTHONHASHSEED='0', HOME=str(private / 'home'), TMPDIR=str(private / 'tmp'), XDG_CACHE_HOME=str(private / 'cache'), OMP_NUM_THREADS='1', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    if build_commit is not None:
        result['OPENPINE_BUILD_COMMIT'] = build_commit
    return result
