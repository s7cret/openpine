"""Archive real Git patch inputs for parent review; never execute a campaign."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import time

from openpine.verification.execution_ci import create_source_archive, export_git_provenance
from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_identity import ensure_external_output, hash_file, write_once_json
from openpine.verification.identity import read_json


def freeze(collection, scenarios, output):
    roots = {name: Path(path) for name, path in collection['roots'].items()}
    ensure_external_output(output, roots)
    observed = join_collections([collection], roots)
    git_exe = shutil.which('git')
    if git_exe is None:
        raise ValueError('Git is required for real patch provenance')

    def git(root, *args):
        return subprocess.run([git_exe, '-C', str(root), *args], capture_output=True,  # noqa: S603 -- resolved executable and local Git argv
                              check=True).stdout

    commits = {}
    for name, root in roots.items():
        if git(root, 'status', '--porcelain'):
            raise ValueError('real patch preview needs a clean committed owner: ' + name)
        commits[name] = git(root, 'rev-parse', 'HEAD').decode().strip()
    declared = read_json(roots['openpine'] / 'docs/RC6_LIFECYCLE_SOURCES.json')
    if declared != {name: sha for name, sha in commits.items() if name != 'openpine'}:
        raise ValueError('real patch preview differs from seven candidate library pins')
    output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    archived = create_source_archive(roots, output / 'sources.tar.gz')
    if archived != observed['source']:
        raise ValueError('candidate changed before archiving real patch inputs')
    for name, root in roots.items():
        export_git_provenance(root, output / 'git' / (name + '.bundle'), commits[name])
    rows = []
    for scenario in scenarios:
        owner, relative = scenario['owner'], scenario['path']
        root = roots[owner]
        edit = git(root, 'log', '-1', '--format=%H', '--', relative).decode().strip()
        parent = git(root, 'rev-parse', edit + '^').decode().strip()
        after = git(root, 'show', commits[owner] + ':' + relative)
        if after != (root / relative).read_bytes() or after != git(root, 'show', edit + ':' + relative):
            raise ValueError('postimage differs from the last real Git edit: ' + relative)
        names = git(root, 'ls-tree', '--name-only', parent, '--', relative).decode().splitlines()
        before = git(root, 'show', parent + ':' + relative) if names else None
        patch = git(root, 'diff', '--binary', parent, commits[owner], '--', relative)
        if not patch or before == after:
            raise ValueError('scenario needs a real nonempty Git patch: ' + relative)
        folder = output / scenario['id']
        folder.mkdir()
        (folder / 'after').write_bytes(after)
        if before is not None:
            (folder / 'before').write_bytes(before)
        (folder / 'change.patch').write_bytes(patch)
        replay = folder / 'replayed-file'
        replay.mkdir()
        if before is not None:
            target = replay / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(before)
        git(replay, 'apply', '--check', str(folder / 'change.patch'))
        git(replay, 'apply', str(folder / 'change.patch'))
        if (replay / relative).read_bytes() != after:
            raise ValueError('real patch did not reproduce the candidate postimage')
        expected = sorted(roots) if scenario['expected_owners'] == 'all' else scenario['expected_owners']
        oracle_owner = scenario.get('oracle_owner', owner)
        oracle = scenario['oracle']
        oracle_inventories = [inventory for key, inventory in observed['inventories'].items()
                              if key.startswith(oracle_owner + '@')]
        oracle_present = bool(oracle_inventories) and all(oracle in inventory['nodeids'] for inventory in oracle_inventories)
        if not oracle_present:
            raise ValueError('real product sensitivity oracle is outside the reviewed inventory')
        rows.append({'id': scenario['id'], 'owner': owner, 'changed': owner + '/' + relative,
                     'last_edit_commit': edit, 'last_edit_parent': parent,
                     'after_owner_commit': commits[owner], 'before_exists': before is not None,
                     'before_sha256': hash_file(folder / 'before') if before is not None else None,
                     'after_sha256': hash_file(folder / 'after'), 'patch_sha256': hash_file(folder / 'change.patch'),
                     'before_blob': git(root, 'rev-parse', parent + ':' + relative).decode().strip() if before is not None else None,
                     'after_blob': git(root, 'rev-parse', commits[owner] + ':' + relative).decode().strip(),
                     'expected_owners': expected, 'expected_preparation': sorted(set(roots) - set(expected)),
                     'oracle_owner': oracle_owner, 'oracle': oracle, 'oracle_in_reviewed_inventory': oracle_present,
                     'patch_replay_matches_after': True})
    result = {'source_commits': commits, 'source_hash': observed['source']['content_hash'],
              'collection_hash': collection['content_hash'], 'policy_hash': collection['policy_hash'],
              'archive_sha256': hash_file(output / 'sources.tar.gz'),
              'git_bundles': {name: hash_file(output / 'git' / (name + '.bundle')) for name in roots},
              'rows': rows, 'preparation_wall_seconds': time.perf_counter() - started,
              'execution_performed': False, 'selection_qualified': False,
              'scope': 'parent review preview; real Git single-file preimages with all other candidate files held constant',
              'refreeze_required': 'parent integration candidate or any policy/source/environment/inventory change'}
    write_once_json(output / 'manifest.json', result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--scenarios', type=Path, default=Path(__file__).with_name('patch_scenarios.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = freeze(read_json(args.collection), read_json(args.scenarios), args.output.resolve())
    print(f"{len(result['rows'])} real Git patch inputs archived; execution not run")


if __name__ == '__main__':
    main()
