"""Relocate execution *locations*, never a frozen candidate or obligation.

A binding is an untrusted locator. Every executor independently re-hashes the
candidate and observes its interpreter before executing a planned shard. The
original plan remains byte-identical across runners and archived evidence.
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping
from openpine.verification.execution_identity import hash_file, source_snapshot
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import seal, verify
from openpine.verification.execution_process import require_executable
BINDING_SCHEMA = 'openpine.execution_binding.v1'

def validate_binding(plan: dict, binding: dict) -> dict:
    validate_plan(plan)
    verify(binding, BINDING_SCHEMA)
    required = {'schema_id', 'content_hash', 'plan_hash', 'roots', 'interpreters'}
    if not required.issubset(binding) or set(binding) - required - {'owner_paths'}:
        raise ValueError('binding has unexpected authority fields')
    owner_paths = binding.get('owner_paths', {})
    if not isinstance(owner_paths, dict):
        raise ValueError('owner replay paths must be a locator mapping')
    for old, new in owner_paths.items():
        for location in (old, new):
            if not isinstance(location, str) or not Path(location).is_absolute() or '..' in Path(location).parts:
                raise ValueError('owner replay paths must be absolute traversal-free paths')
    historical = [Path(p) for p in owner_paths]
    if any(a != b and (a.is_relative_to(b) or b.is_relative_to(a)) for a in historical for b in historical):
        raise ValueError('overlapping owner replay paths')
    if binding.get('plan_hash') != plan['content_hash']:
        raise ValueError('binding belongs to a different execution plan')
    roots, interpreters = (binding.get('roots'), binding.get('interpreters'))
    if not isinstance(roots, dict) or set(roots) != set(plan['roots']):
        raise ValueError('binding must locate exactly the frozen source roots')
    if not isinstance(interpreters, dict) or not interpreters or (not set(interpreters).issubset(plan['environments'])):
        raise ValueError('binding has unknown or empty interpreter mapping')
    for location in [*roots.values(), *interpreters.values()]:
        if not isinstance(location, str) or not Path(location).is_absolute() or '..' in Path(location).parts:
            raise ValueError('binding locations must be absolute traversal-free paths')
    return binding

def make_binding(plan: dict, roots: Mapping[str, Path], interpreters: Mapping[str, str]) -> dict:
    binding = seal({'schema_id': BINDING_SCHEMA, 'plan_hash': plan['content_hash'], 'roots': {name: str(Path(path).absolute()) for name, path in sorted(roots.items())}, 'interpreters': dict(sorted(interpreters.items()))})
    validate_binding(plan, binding)
    checked_locations(plan, binding)
    return binding


def checked_locations(plan: dict, binding: dict | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Check live launch/replay locators, not archived execution provenance.

    validate_binding intentionally performs no filesystem observation: historical
    execution bindings remain verifiable after their runner has been removed.
    Call this boundary before using a binding to consume current filesystem bytes.
    """
    roots, interpreters = locations(plan, binding)
    for location in roots.values():
        path = Path(location)
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('bound source path crosses a symlink')
    if source_snapshot({name: Path(path) for name, path in roots.items()}) != plan['source']:
        raise ValueError('candidate changed; current candidate is stale: relocated source is not the frozen candidate')
    for name, executable in interpreters.items():
        path = Path(executable).resolve(strict=True)
        require_executable(path)
        if hash_file(path) != plan['environments'][name]['identity']['executable_sha256']:
            raise ValueError('bound interpreter differs from frozen executable: ' + name)
    return roots, interpreters

def locations(plan: dict, binding: dict | None=None) -> tuple[dict[str, str], dict[str, str]]:
    if binding is None:
        return (dict(plan['roots']), {key: env['executable'] for key, env in plan['environments'].items()})
    validate_binding(plan, binding)
    return (dict(binding['roots']), dict(binding['interpreters']))
