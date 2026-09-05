"""Publish only the tested request-series head, preserving all other branch tips."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BASE = '60c615cab534ce948aebc15a1a32681823bacee7'
HEAD = '53f0c2d6da67bf364e0962fb50a7107571182427'
BRANCH = 'ops/rc6-requests-finalize-20260906'
TARGET = 'refs/heads/release/5.0.0rc6'
KEEP = {'refs/heads/main', 'refs/heads/release/v2.17', 'refs/heads/release/v4.0.2', TARGET}


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def heads():
    return {ref: sha for sha, ref in (line.split() for line in git('ls-remote', '--heads', 'origin').splitlines())}


def main(evidence):
    if os.environ.get('GITHUB_REPOSITORY') != 's7cret/openpine' or os.environ.get('GITHUB_REF') != 'refs/heads/' + BRANCH:
        raise ValueError('Wrong publication repository or branch')
    trigger = os.environ['GITHUB_SHA']
    if not re.fullmatch('[0-9a-f]{40}', trigger):
        raise ValueError('Invalid maintenance identity')
    plan = json.loads((evidence / 'plan.json').read_text())
    if (plan['repository'], plan['base'], plan['head'], plan['target']) != ('s7cret/openpine', BASE, HEAD, TARGET.removeprefix('refs/heads/')):
        raise ValueError('Unexpected verified submission')
    git('bundle', 'verify', str(evidence / 'verified.bundle'))
    git('fetch', str(evidence / 'verified.bundle'), 'refs/heads/review-candidate')
    if git('rev-parse', 'FETCH_HEAD') != HEAD:
        raise ValueError('Verification bundle head mismatch')
    git('merge-base', '--is-ancestor', BASE, HEAD)
    before = heads()
    branch, tag = 'refs/heads/' + BRANCH, 'refs/tags/' + BRANCH
    if set(before) != KEEP | {branch} or before[branch] != trigger or before[TARGET] not in (BASE, HEAD):
        raise ValueError('Branch inventory changed; publication aborted')
    existing_tag = git('ls-remote', '--refs', 'origin', tag)
    if existing_tag:
        raise ValueError('Archive tag already exists; refusing replacement')
    (evidence / 'before-heads.json').write_text(json.dumps(before, indent=2) + '\n')
    # Only retirement/tag refs get compare-and-delete leases. RC6 is an ordinary
    # fast-forward refspec; this push cannot discard a concurrently added commit.
    git('push', '--atomic', f'--force-with-lease={branch}:{trigger}', f'--force-with-lease={tag}:',
        'origin', HEAD + ':' + TARGET, trigger + ':' + tag, ':' + branch)
    expected = {key: value for key, value in before.items() if key != branch}
    expected[TARGET] = HEAD
    after = heads()
    if after != expected or git('ls-remote', '--refs', 'origin', tag).split()[0] != trigger:
        raise ValueError('Post-publication branch or archive verification failed')
    (evidence / 'after-heads.json').write_text(json.dumps(after, indent=2) + '\n')
    (evidence / 'publication.json').write_text(json.dumps({'base': BASE, 'head': HEAD, 'tree': git('rev-parse', HEAD + '^{tree}'),
        'maintenance_tag': tag, 'maintenance_sha': trigger, 'verification_run': os.environ['GITHUB_RUN_ID']}, indent=2) + '\n')


if __name__ == '__main__':
    main(Path(sys.argv[1]).resolve())
