"""Exact readable patch restoration and guarded publication of Stage 1."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BRANCH = 'ops/rc6-stage1-20260906'
TARGET = 'refs/heads/release/5.0.0rc6'


def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data).decode().strip()


def load(path):
    plan = json.loads(path.read_text())
    if plan['repository'] != 's7cret/openpine' or os.environ.get('GITHUB_REPOSITORY', plan['repository']) != plan['repository']:
        raise ValueError('repository mismatch')
    if plan['branch'] != TARGET.removeprefix('refs/heads/'):
        raise ValueError('target mismatch')
    parent = plan['base']
    for item in plan['commits']:
        if any(not re.fullmatch('[0-9a-f]{40}', item[key]) for key in ('sha','parent','tree')) or item['parent'] != parent:
            raise ValueError('invalid source series')
        raw = item['raw'].encode()
        if not raw.startswith(f"tree {item['tree']}\nparent {parent}\nauthor ".encode()):
            raise ValueError('invalid commit header')
        if hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest() != item['sha']:
            raise ValueError('invalid raw commit identity')
        parent = item['sha']
    if plan['head'] != parent:
        raise ValueError('wrong head')
    return plan


def restore(path, evidence):
    plan = load(path)
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence/'plan.json').write_text(json.dumps(plan,indent=2)+'\n')
    patches = []
    for number, item in enumerate(plan['commits'], 1):
        if item['patch'] != f'{number:02d}.patch':
            raise ValueError('invalid patch path')
        parts = sorted(path.parent.glob(f'{number:02d}-??.patch'))
        if not parts or [p.name for p in parts] != [f'{number:02d}-{i:02d}.patch' for i in range(1,len(parts)+1)]:
            raise ValueError('missing patch part')
        patch = b''.join(p.read_bytes() for p in parts)
        if len(patch)>2_000_000 or hashlib.sha256(patch).hexdigest()!=item['sha256']:
            raise ValueError('patch checksum mismatch')
        patches.append(patch)
        (evidence/item['patch']).write_bytes(patch)
    git('checkout','--detach',plan['base'])
    for item,patch in zip(plan['commits'],patches,strict=True):
        if git('rev-parse','HEAD') != item['parent']:
            raise ValueError('working parent mismatch')
        git('apply','--check','--index','-',data=patch)
        git('apply','--index','--whitespace=nowarn','-',data=patch)
        if git('write-tree') != item['tree']:
            raise ValueError('reviewed tree mismatch')
        actual=git('hash-object','-t','commit','-w','--stdin',data=item['raw'].encode())
        if actual != item['sha']:
            raise ValueError('reviewed commit mismatch')
        git('checkout','--detach',actual)
    git('update-ref','refs/heads/review-candidate',plan['head'])
    git('bundle','create',str(evidence/'verified.bundle'),'refs/heads/review-candidate')
    git('bundle','verify',str(evidence/'verified.bundle'))
    (evidence/'code-head.txt').write_text(plan['head']+'\n')


def refs():
    return {ref:sha for sha,ref in (line.split() for line in git('ls-remote','--refs','origin').splitlines())}


def publish(evidence):
    plan=load(evidence/'plan.json')
    branch, tag = 'refs/heads/'+BRANCH, 'refs/tags/'+BRANCH
    if os.environ.get('GITHUB_REF') != branch:
        raise ValueError('wrong maintenance branch')
    sha=os.environ['GITHUB_SHA']
    if not re.fullmatch('[0-9a-f]{40}',sha):
        raise ValueError('invalid maintenance head')
    git('fetch',str(evidence/'verified.bundle'),'refs/heads/review-candidate')
    if git('rev-parse','FETCH_HEAD') != plan['head']:
        raise ValueError('verified bundle mismatch')
    git('merge-base','--is-ancestor',plan['base'],plan['head'])
    before=refs()
    if before.get(TARGET) not in (plan['base'],plan['head']):
        raise ValueError('release changed concurrently')
    if before[TARGET] != plan['head']:
        git('push','origin',plan['head']+':'+TARGET)
    if refs().get(TARGET) != plan['head']:
        raise ValueError('release update failed')
    git('push','--atomic',f'--force-with-lease={branch}:{sha}',f'--force-with-lease={tag}:', 'origin',sha+':'+tag,':'+branch)
    after=refs()
    expected={k:v for k,v in before.items() if k.startswith('refs/heads/') and k != branch}
    expected[TARGET]=plan['head']
    if {k:v for k,v in after.items() if k.startswith('refs/heads/')} != expected or after.get(tag)!=sha:
        raise ValueError('final branch inventory mismatch')
    for name,value in [('before',before),('after',after)]:
        (evidence/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
    (evidence/'published-head.txt').write_text(plan['head']+'\n')


if __name__=='__main__':
    if sys.argv[1]=='restore': restore(Path(sys.argv[2]).resolve(),Path(sys.argv[3]).resolve())
    elif sys.argv[1]=='publish': publish(Path(sys.argv[2]).resolve())
    else: raise ValueError('unknown operation')
