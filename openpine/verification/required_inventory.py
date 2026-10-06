"""Retain external data obligations alongside a frozen native collection."""
from __future__ import annotations

from pathlib import Path

from openpine.verification.execution_identity import hash_file
from openpine.verification.identity import read_json, verify
from openpine.verification.pytest_gate import validate_inventory


def pending_required_inventories(policy, roots, source, inventories, selected):
    from openpine.verification.execution_plan import _nodes

    pending = {}
    for name in selected:
        descriptor = policy['components'][name].get('required_inventory')
        if descriptor is None:
            continue
        if not isinstance(descriptor, dict) or set(descriptor) != {'owner', 'path', 'sha256'}:
            raise ValueError('invalid required inventory descriptor')
        owner, relative = descriptor['owner'], descriptor['path']
        if owner not in roots or not isinstance(relative, str):
            raise ValueError('unknown required inventory owner')
        path = Path(relative)
        if path.is_absolute() or any(p in {'', '.', '..'} for p in relative.split('/')):
            raise ValueError('invalid required inventory path')
        root = Path(roots[owner]).resolve()
        target = root / path
        if target.is_symlink() or not target.resolve(strict=True).is_relative_to(root):
            raise ValueError('required inventory escapes its owner')
        recorded = source['components'][owner]['files'].get(relative, {})
        if recorded.get('sha256') != descriptor['sha256'] or hash_file(target) != descriptor['sha256']:
            raise ValueError('required inventory differs from frozen source')
        manifest = read_json(target)
        verify(manifest, 'openpine.required_external_inventory.v1')
        if manifest['component'] != name or manifest['gate'] != 'data' or manifest['status'] != 'NOT_EXECUTED':
            raise ValueError('invalid required external inventory scope/status')
        full = _nodes(manifest['full_nodeids'])
        external = _nodes(manifest['pending_nodeids'])
        validate_inventory(full, manifest['full_lock'], 0)
        proofs = manifest['node_sources']
        if not isinstance(proofs, dict) or set(proofs) != set(external):
            raise ValueError('missing required external source proofs')
        for node, proof in proofs.items():
            if not isinstance(proof, dict) or set(proof) != {'path', 'sha256'} or proof['path'] != node.split('::', 1)[0]:
                raise ValueError('invalid required external source proof')
            if source['components'][name]['files'].get(proof['path'], {}).get('sha256') != proof['sha256']:
                raise ValueError('required external test source changed')
        collections = [row for key, row in inventories.items() if key.startswith(name + '@')]
        if not collections:
            raise ValueError('required inventory has no native collection')
        for row in collections:
            native = set(_nodes(row['nodeids']))
            if native.intersection(external) or native | set(external) != set(full) or row.get('deselected', 0) != len(external):
                raise ValueError('native and external inventories do not partition full requirements')
        pending[name] = {'gate': 'data', 'status': 'NOT_EXECUTED',
                         'execution_pass': False, 'nodeids': external,
                         'full_count': len(full), 'full_lock': manifest['full_lock'],
                         'manifest_sha256': descriptor['sha256'],
                         'node_sources': proofs}
    return pending
