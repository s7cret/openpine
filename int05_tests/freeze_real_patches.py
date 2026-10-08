"""Archive real Git patch inputs for parent review; never execute a campaign."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import time

from openpine.verification.execution_ci import COMPONENTS, create_source_archive, export_git_provenance
from openpine.verification.execution_collections import join_collections
from openpine.verification.execution_identity import evidence_path, ensure_external_output, hash_file, source_snapshot, write_once_json
from openpine.verification.execution_plan import select_components
from openpine.verification.identity import digest, read_json


def freeze(collection, scenarios, output):
    roots = {name: Path(path) for name, path in collection['roots'].items()}
    ensure_external_output(output, roots)
    observed = join_collections([collection], roots)
    policy = read_json(roots['openpine'] / 'verification/execution-policy.json')
    if set(roots) != set(COMPONENTS) or set(policy['components']) != set(roots):
        raise ValueError('real patch preview requires all eight candidate owners')
    if observed['policy_hash'] != digest(policy):
        raise ValueError('real patch collection differs from candidate policy')
    for owner, settings in policy['components'].items():
        required = set(settings.get('pythons', ['3.13']))
        present = set()
        for env_id, env in observed['environments'].items():
            minor = '.'.join(env['identity']['python'].split('.')[:2])
            if minor in required:
                if owner + '@' + env_id not in observed['inventories']:
                    raise ValueError('real patch collection omitted owner inventory: ' + owner + '@' + env_id)
                present.add(minor)
        if not required or not required.issubset(present):
            raise ValueError('real patch collection omitted required interpreter: ' + owner)
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError('real patch scenarios must be a nonempty reviewed list')
    ids = set()
    for scenario in scenarios:
        scenario_id = scenario.get('id')
        if (not isinstance(scenario_id, str) or re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]*', scenario_id) is None
                or scenario_id in ids or scenario_id in {'git', 'sources.tar.gz', 'manifest.json'}):
            raise ValueError('invalid or duplicate real patch scenario ID')
        ids.add(scenario_id)
        owner = scenario.get('owner')
        if owner not in roots or scenario.get('oracle_owner', owner) not in roots:
            raise ValueError('unknown real patch scenario owner')
        evidence_path(roots[owner], scenario['path'])
        if scenario['path'] not in observed['source']['components'][owner]['files']:
            raise ValueError('real patch path is outside archived candidate source')
        expected = scenario['expected_owners']
        if expected != 'all' and (not isinstance(expected, list) or not expected
                or any(not isinstance(n, str) or n not in roots for n in expected)
                or len(set(expected)) != len(expected)):
            raise ValueError('invalid real patch expected owners')
        selected, _ = select_components(policy, 'affected', [], [owner + '/' + scenario['path']])
        if selected != sorted(roots if expected == 'all' else expected):
            raise ValueError('real patch scope differs from independent scenario expectation')
        oracle_owner = scenario.get('oracle_owner', owner)
        inventories = [inventory for key, inventory in observed['inventories'].items()
                       if key.startswith(oracle_owner + '@')]
        if not inventories or any(scenario['oracle'] not in inventory['nodeids'] for inventory in inventories):
            raise ValueError('real product sensitivity oracle is outside the reviewed inventory')
    git_exe = shutil.which('git')
    if git_exe is None:
        raise ValueError('Git is required for real patch provenance')

    def git(root, *args):
        return subprocess.run([git_exe, '-C', str(root), '--literal-pathspecs', *args], capture_output=True,  # noqa: S603 -- resolved executable and local Git argv
                              check=True).stdout

    def file_mode(root, revision, relative):
        entries = git(root, 'ls-tree', '-z', revision, '--', relative).split(b'\0')
        entries = [entry for entry in entries if entry]
        if not entries:
            return None
        if len(entries) != 1:
            raise ValueError('real patch path is not one Git file')
        metadata, name = entries[0].split(b'\t', 1)
        mode, kind, _ = metadata.decode().split()
        if name.decode() != relative or kind != 'blob' or mode not in {'100644', '100755'}:
            raise ValueError('real patch requires regular Git file modes')
        return mode

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
        after_mode = file_mode(root, commits[owner], relative)
        before_mode = file_mode(root, parent, relative)
        executable = bool((root / relative).stat().st_mode & 0o111)
        if (after != (root / relative).read_bytes() or after != git(root, 'show', edit + ':' + relative)
                or after_mode != file_mode(root, edit, relative)
                or executable != (after_mode == '100755')):
            raise ValueError('postimage differs from the last real Git edit: ' + relative)
        before = git(root, 'show', parent + ':' + relative) if before_mode is not None else None
        patch = git(root, 'diff', '--no-ext-diff', '--no-textconv', '--binary', '--full-index', parent, commits[owner], '--', relative)
        if not patch or (before == after and before_mode == after_mode):
            raise ValueError('scenario needs a real nonempty Git patch: ' + relative)
        folder = output / scenario['id']
        folder.mkdir()
        (folder / 'after').write_bytes(after)
        (folder / 'after').chmod(0o755 if after_mode == '100755' else 0o644)
        if before is not None:
            (folder / 'before').write_bytes(before)
            (folder / 'before').chmod(0o755 if before_mode == '100755' else 0o644)
        (folder / 'change.patch').write_bytes(patch)
        replay = folder / 'replayed-file'
        replay.mkdir()
        if before is not None:
            target = replay / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before)
            target.chmod(0o755 if before_mode == '100755' else 0o644)
        git(replay, 'apply', '--check', str(folder / 'change.patch'))
        git(replay, 'apply', str(folder / 'change.patch'))
        if ((replay / relative).read_bytes() != after
                or bool((replay / relative).stat().st_mode & 0o111) != executable):
            raise ValueError('real patch did not reproduce the candidate postimage')
        expected = sorted(roots if scenario['expected_owners'] == 'all' else scenario['expected_owners'])
        oracle_owner = scenario.get('oracle_owner', owner)
        oracle = scenario['oracle']
        rows.append({'id': scenario['id'], 'owner': owner, 'changed': owner + '/' + relative,
                     'last_edit_commit': edit, 'last_edit_parent': parent,
                     'after_owner_commit': commits[owner], 'before_exists': before is not None,
                     'before_mode': before_mode, 'after_mode': after_mode,
                     'before_sha256': hash_file(folder / 'before') if before is not None else None,
                     'after_sha256': hash_file(folder / 'after'), 'patch_sha256': hash_file(folder / 'change.patch'),
                     'before_blob': git(root, 'rev-parse', parent + ':' + relative).decode().strip() if before is not None else None,
                     'after_blob': git(root, 'rev-parse', commits[owner] + ':' + relative).decode().strip(),
                     'expected_owners': expected, 'expected_preparation': sorted(set(roots) - set(expected)),
                     'oracle_owner': oracle_owner, 'oracle': oracle, 'oracle_in_reviewed_inventory': True,
                     'patch_replay_matches_after': True})
    if source_snapshot(roots) != observed['source'] or any(
            git(root, 'rev-parse', 'HEAD').decode().strip() != commits[name]
            or git(root, 'status', '--porcelain') for name, root in roots.items()):
        raise ValueError('candidate changed while freezing real patch inputs')
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
