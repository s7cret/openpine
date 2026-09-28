"""Relocate execution *locations*, never a frozen candidate or obligation.

A binding is an untrusted locator. Every executor independently re-hashes the
candidate and observes its interpreter before executing a planned shard. The
original plan remains byte-identical across runners and archived evidence.
"""
from __future__ import annotations
from pathlib import Path
from typing import Mapping
from openpine.verification.execution_identity import source_snapshot
from openpine.verification.execution_plan import validate_plan
from openpine.verification.identity import seal, verify
BINDING_SCHEMA = 'openpine.execution_binding.v1'

def validate_binding(plan: dict, binding: dict) -> dict:
    validate_plan(plan)
    verify(binding, BINDING_SCHEMA)
    if binding.get('plan_hash') != plan['content_hash']:
        raise ValueError('binding belongs to a different execution plan')
    roots, interpreters = (binding.get('roots'), binding.get('interpreters'))
    if not isinstance(roots, dict) or set(roots) != set(plan['roots']):
        raise ValueError('binding must locate exactly the frozen source roots')
    if not isinstance(interpreters, dict) or not interpreters or (not set(interpreters).issubset(plan['environments'])):
        raise ValueError('binding has unknown or empty interpreter mapping')
    for location in [*roots.values(), *interpreters.values()]:
        if not isinstance(location, str) or not Path(location).is_absolute():
            raise ValueError('binding locations must be absolute paths')
    return binding

def make_binding(plan: dict, roots: Mapping[str, Path], interpreters: Mapping[str, str]) -> dict:
    binding = seal({'schema_id': BINDING_SCHEMA, 'plan_hash': plan['content_hash'], 'roots': {name: str(Path(path).absolute()) for name, path in sorted(roots.items())}, 'interpreters': dict(sorted(interpreters.items()))})
    validate_binding(plan, binding)
    if source_snapshot({name: Path(path) for name, path in binding['roots'].items()}) != plan['source']:
        raise ValueError('relocated source is not the frozen candidate')
    return binding

def locations(plan: dict, binding: dict | None=None) -> tuple[dict[str, str], dict[str, str]]:
    if binding is None:
        return (dict(plan['roots']), {key: env['executable'] for key, env in plan['environments'].items()})
    validate_binding(plan, binding)
    return (dict(binding['roots']), dict(binding['interpreters']))
