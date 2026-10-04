"""Typed owner launch locators: freeze locations, never semantic authority.

Historical v1 concrete policies stay unchanged. V2 resolves explicit location
objects, not shell interpolation, home-directory guessing, or receipt rewriting.
"""
from __future__ import annotations

import copy
from pathlib import Path
import re

from typing import Any

from openpine.verification.execution_identity import hash_file
from openpine.verification.identity import digest, seal, verify

SCHEMA = 'openpine.owner_launch.v2'
LEGACY_SCHEMA = 'openpine.owner_launch.v1'
POLICY_SCHEMA = 'openpine.execution_policy.v2'


def _slots(policy):
    if policy.get('schema_id') != POLICY_SCHEMA:
        raise ValueError('typed owner launch requires explicit v2 policy')
    slots = policy.get('owner_locator_slots')
    if not isinstance(slots, dict) or not slots:
        raise ValueError('policy must declare owner locator slots')
    if any(not isinstance(k, str) or not re.fullmatch(r'[a-zA-Z][a-zA-Z0-9_-]*', k)
           or v not in {'directory', 'executable'} for k, v in slots.items()):
        raise ValueError('invalid owner locator slot type')
    return slots


def _path(value, *, live):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise ValueError('invalid owner location')
    path = Path(value)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('owner location must be absolute and traversal-free')
    if live and any(p.is_symlink() for p in (path, *path.parents)):
        # Interpreter executable symlinks are separately bound to resolved bytes.
        raise ValueError('owner directory location crosses a symlink')
    return path


def _resolve(value, paths, field=None, semantic=False) -> Any:
    semantic = semantic or field == 'expected_stdout'
    if isinstance(value, dict):
        if 'owner_locator' in value:
            if semantic or field not in {'argv', 'cwd', 'path', 'stack_root'}:
                raise ValueError('owner locators may not replace semantic authority')
            if set(value) - {'owner_locator', 'relative', 'prefix'}:
                raise ValueError('locator contains authority fields')
            slot = value['owner_locator']
            if slot not in paths:
                raise ValueError('unknown owner locator slot')
            relative = value.get('relative', '')
            prefix = value.get('prefix', '')
            if not isinstance(relative, str) or (relative and (Path(relative).is_absolute()
                or any(p in {'', '.', '..'} for p in relative.split('/')))):
                raise ValueError('invalid relative owner locator')
            if not isinstance(prefix, str) or (prefix and not re.fullmatch(r'--[a-zA-Z0-9_.-]+=', prefix)):
                raise ValueError('invalid owner argument prefix')
            return prefix + str(Path(paths[slot]) / relative)
        return {k: _resolve(v, paths, k, semantic) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, paths, field, semantic) for v in value]
    return copy.deepcopy(value)


def freeze_owner_launch(policy: dict, paths: dict) -> dict:
    slots = _slots(policy)
    if not isinstance(paths, dict) or set(paths) != set(slots):
        raise ValueError('launch must supply exactly the declared location slots')
    executables = {}
    targets = {}
    for name, kind in slots.items():
        path = _path(paths[name], live=kind == 'directory')
        if kind == 'executable':
            resolved = path.resolve(strict=True)
            if not resolved.is_file():
                raise ValueError('owner executable is not a regular file')
            executables[name] = hash_file(resolved)
            targets[name] = str(resolved)
    _resolve(policy, paths)
    return seal({'schema_id': SCHEMA, 'policy_hash': digest(policy),
                 'paths': dict(sorted(paths.items())), 'executables': executables,
                 'executable_targets': targets})


def resolve_owner_policy(policy: dict, launch: dict, *, check_live: bool = False, live_executables: set | None = None) -> dict:
    bind_aliases = policy.get('bind_owner_tool_aliases', False)
    if type(bind_aliases) is not bool:
        raise ValueError('owner tool alias binding must be an independently reviewed boolean')
    slots = _slots(policy)
    schema = launch.get('schema_id')
    if schema not in {SCHEMA, LEGACY_SCHEMA}:
        raise ValueError('unknown owner launch schema')
    verify(launch, schema)
    fields = {'schema_id', 'content_hash', 'policy_hash', 'paths', 'executables'}
    if schema == SCHEMA:
        fields.add('executable_targets')
    if set(launch) != fields:
        raise ValueError('owner launch contains unexpected authority fields')
    paths = launch.get('paths')
    if launch['policy_hash'] != digest(policy) or not isinstance(paths, dict) or set(paths) != set(slots):
        raise ValueError('owner launch differs from reviewed policy')
    if set(launch['executables']) != {n for n, k in slots.items() if k == 'executable'}:
        raise ValueError('owner launch omitted executable identities')
    targets = launch.get('executable_targets', {})
    if schema == SCHEMA and set(targets) != set(launch['executables']):
        raise ValueError('owner launch omitted executable targets')
    if policy.get('capture_owner_executables') is True and schema != SCHEMA:
        raise ValueError('captured tools require frozen executable targets')
    if live_executables is not None and (not isinstance(live_executables, set) or not live_executables.issubset(launch['executables'])):
        raise ValueError('invalid live executable scope')
    for name, kind in slots.items():
        path = _path(paths[name], live=check_live and kind == 'directory')
        if kind == 'executable':
            if not re.fullmatch(r'sha256:[0-9a-f]{64}', launch['executables'][name]):
                raise ValueError('invalid owner executable identity')
            live = check_live and (live_executables is None or name in live_executables)
            if live and hash_file(path.resolve(strict=True)) != launch['executables'][name]:
                raise ValueError('owner executable drift')
            if schema == SCHEMA:
                target = _path(targets[name], live=live)
                if live and path.resolve(strict=True) != target:
                    raise ValueError('owner executable target drift')
    resolved = _resolve(policy, paths)
    if policy.get('capture_owner_executables') is True:
        dependencies = policy.get('owner_tool_dependencies', {})
        if not isinstance(dependencies, dict) or any(n not in launch['executables'] or not isinstance(ds, list) or any(d not in launch['executables'] for d in ds) for n, ds in dependencies.items()):
            raise ValueError('invalid independently reviewed tool dependencies')
        def named_tools(value):
            if isinstance(value, dict):
                if 'owner_locator' in value:
                    return {value['owner_locator']} & set(launch['executables'])
                return set().union(*(named_tools(v) for v in value.values())) if value else set()
            if isinstance(value, list):
                return set().union(*(named_tools(v) for v in value)) if value else set()
            return set()
        def attach(original, current):
            if isinstance(original, dict):
                if 'owner_locator' in original:
                    return
                if 'role' in original and 'argv' in original:
                    extra = original.get('owner_extra_tools', [])
                    if not isinstance(extra, list) or any(not isinstance(n, str) or n not in launch['executables'] for n in extra):
                        raise ValueError('invalid independently reviewed extra command tools')
                    names = named_tools(original['argv']) | set(extra)
                    pending = list(names)
                    while pending:
                        for dependency in dependencies.get(pending.pop(), []):
                            if dependency not in names:
                                names.add(dependency)
                                pending.append(dependency)
                    inputs = {'owner-tool:' + n: {'path': targets[n], 'sha256': launch['executables'][n]} for n in sorted(names)}
                    if bind_aliases:
                        for name in names:
                            inputs['owner-tool:' + name].update(
                                declared_path=paths[name],
                                is_argv_executable=current['argv'][0] == paths[name],
                            )
                    if set(inputs) & set(current.get('inputs', {})):
                        raise ValueError('tool capture may not replace independently reviewed inputs')
                    current['inputs'] = {**current.get('inputs', {}), **inputs}
                for key, value in original.items():
                    attach(value, current[key])
            elif isinstance(original, list):
                for before, after in zip(original, current):
                    attach(before, after)
        attach(policy, resolved)
    return resolved


def resolve_producer_policy(plan, policy, *, attempt, helpers=None, attempt_slot='attempt', owner=None):
    """Check original policy and executing helpers before any producer commands."""
    from openpine.verification.execution_plan import PLAN_SCHEMA
    verify(plan, PLAN_SCHEMA)
    if digest(policy) != plan['policy_hash']:
        raise ValueError('producer policy differs from frozen plan')
    resolved = copy.deepcopy(policy)
    if policy.get('schema_id') == POLICY_SCHEMA:
        launch = plan.get('owner_launch')
        if not isinstance(launch, dict):
            raise ValueError('producer requires frozen owner launch')
        live_executables = None
        if owner is not None and policy.get('capture_owner_executables') is True:
            declared = policy.get('owner_tool_scopes', {}).get(owner)
            if not isinstance(declared, list) or len(declared) != len(set(declared)) or not set(declared).issubset(launch['executables']):
                raise ValueError('missing or invalid independently reviewed owner tool scope')
            rendered = resolve_owner_policy(policy, launch)
            def required_tools(value):
                if isinstance(value, dict):
                    own = {n.removeprefix('owner-tool:') for n in value.get('inputs', {}) if n.startswith('owner-tool:')} if 'argv' in value else set()
                    return own | (set().union(*(required_tools(v) for v in value.values())) if value else set())
                if isinstance(value, list):
                    return set().union(*(required_tools(v) for v in value)) if value else set()
                return set()
            live_executables = set(declared)
            if not required_tools(rendered['stabilization'].get(owner, {})).issubset(live_executables):
                raise ValueError('owner tool scope omits required command tools')
        resolved = resolve_owner_policy(policy, launch, check_live=True, live_executables=live_executables)
        if _slots(policy).get(attempt_slot) != 'directory' or Path(launch['paths'][attempt_slot]) != Path(attempt):
            raise ValueError('producer attempt differs from frozen owner launch')
    if helpers is not None:
        declarations = resolved['stabilization']['package_harness_inputs']
        for name, declaration in declarations.items():
            if not isinstance(name, str) or Path(name).name != name or name in {'.', '..'}:
                raise ValueError('invalid producer helper name')
            helper = Path(helpers) / name
            _path(str(helper), live=True)
            if declaration['path'] != str(helper) or declaration['sha256'] != hash_file(helper):
                raise ValueError('executed helper differs from independently frozen input: ' + name)
    return resolved
