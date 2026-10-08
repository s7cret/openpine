"""Join interpreter-specific collection receipts before freezing a common plan."""
from __future__ import annotations
from pathlib import Path
from openpine.verification.execution_identity import source_snapshot
from openpine.verification.execution_cli import COLLECTION_SCHEMA
from openpine.verification.identity import seal, verify
from openpine.verification.pytest_gate import validate_inventory
from openpine.verification.execution_plan import HASH, _nodes

def join_collections(receipts: list[dict], roots: dict[str, Path]) -> dict:
    if not receipts:
        raise ValueError('no interpreter collections supplied')
    source = source_snapshot(roots)
    environments, inventories, origins = ({}, {}, [])
    policy_hash = receipts[0].get('policy_hash')
    if not isinstance(policy_hash, str) or not HASH.fullmatch(policy_hash):
        raise ValueError('missing or invalid collection policy identity')
    for receipt in receipts:
        verify(receipt, COLLECTION_SCHEMA)
        if receipt.get('ok') is not True or receipt.get('errors') != [] or receipt.get('collect_only') is not True or (receipt.get('execution_pass') is not False):
            raise ValueError('failed or executable-as-collection receipt')
        if receipt.get('source') != source or receipt.get('policy_hash') != policy_hash:
            raise ValueError('collection source/policy identity mismatch')
        if set(receipt.get('roots', {})) != set(roots):
            raise ValueError('collection omitted a candidate component')
        if not receipt.get('environments') or not receipt.get('inventories'):
            raise ValueError('empty interpreter collection')
        for env_id, env in receipt['environments'].items():
            verify(env['identity'], 'openpine.execution_environment.v1')
            if env_id in environments:
                raise ValueError('duplicate interpreter collection: ' + env_id)
            environments[env_id] = env
        for key, inventory in receipt['inventories'].items():
            component, separator, env_id = key.partition('@')
            if not separator or component not in roots or env_id not in receipt['environments'] or (key in inventories) or (inventory.get('source_hash') != source['content_hash']) or (inventory.get('environment_hash') != environments[env_id]['identity']['content_hash']):
                raise ValueError('orphan/duplicate/wrong-source inventory: ' + key)
            nodes = _nodes(inventory['nodeids'])
            validate_inventory(nodes, inventory['reviewed_lock'], inventory.get('deselected', 0))
            if 'node_markers' in inventory:
                markers = inventory['node_markers']
                if (not isinstance(markers, dict) or set(markers) != set(nodes)
                        or any(not isinstance(v, list) or any(not isinstance(m, str) for m in v)
                               or len(set(v)) != len(v) for v in markers.values())):
                    raise ValueError('missing or malformed collection marker evidence')
            inventories[key] = inventory
        if {key.partition('@')[2] for key in receipt['inventories']} != set(receipt['environments']):
            raise ValueError('interpreter collection omitted all inventory selectors')
        origins.append(receipt['content_hash'])
    return seal({'schema_id': COLLECTION_SCHEMA, 'roots': {n: str(p.resolve()) for n, p in roots.items()}, 'source': source, 'policy_hash': policy_hash, 'environments': environments, 'inventories': inventories, 'origin_collection_hashes': origins, 'errors': [], 'ok': True, 'collect_only': True, 'execution_pass': False})
