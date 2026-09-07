"""Archive only this completed stage's refs; do not update any release branch."""
import json
import os
from pathlib import Path
import subprocess

FEATURE='refs/heads/stage2/language-core-20260907'
FEATURE_SHA='d09ff57721655918d3313634b2291c45a7e414e4'
MAINT='refs/heads/ops/rc6-stage2-finalize-20260907'
TARGET='refs/heads/release/5.0.0rc6'
TARGET_SHA='85fa14ee7b95fa5c18314ba82c07379e8e0e2f80'

def git(*args):
    return subprocess.check_output(['git',*args],text=True).strip()

def refs():
    return {ref:sha for sha,ref in (line.split() for line in git('ls-remote','--refs','origin').splitlines())}

if os.environ['GITHUB_REPOSITORY']!='s7cret/openpine' or os.environ['GITHUB_REF']!=MAINT:
    raise SystemExit('wrong repository or maintenance ref')
sha=os.environ['GITHUB_SHA'];before=refs()
if before.get(TARGET)!=TARGET_SHA or before.get(MAINT)!=sha or before.get(FEATURE) not in (None,FEATURE_SHA):
    raise SystemExit('concurrent source change; nothing archived')
git('merge-base','--is-ancestor',FEATURE_SHA,TARGET_SHA)
updates=[];leases=[]
for branch,tip in [(FEATURE,FEATURE_SHA),(MAINT,sha)]:
    tag=branch.replace('refs/heads/','refs/tags/',1)
    if tag in before and before[tag]!=tip:
        raise SystemExit('archive tag already has a different identity')
    if tag not in before:
        updates.append(tip+':'+tag);leases.append('--force-with-lease='+tag+':')
    if branch in before:
        updates.append(':'+branch);leases.append('--force-with-lease='+branch+':'+tip)
git('push','--atomic',*leases,'origin',*updates)
after=refs()
expected={k:v for k,v in before.items() if k not in {FEATURE,MAINT}}
expected[FEATURE.replace('refs/heads/','refs/tags/',1)]=FEATURE_SHA
expected[MAINT.replace('refs/heads/','refs/tags/',1)]=sha
if after!=expected: raise SystemExit('post-archive refs do not match the exact plan')
out=Path(os.environ['RUNNER_TEMP'])/'publication';out.mkdir()
for name,value in [('before',before),('after',after)]:
    (out/(name+'.json')).write_text(json.dumps(value,indent=2)+'\n')
(out/'published-head.txt').write_text(TARGET_SHA+'\n')
git('update-ref','refs/heads/published-stage2',TARGET_SHA)
git('bundle','create',str(out/'openpine.bundle'),'refs/heads/published-stage2')
git('bundle','verify',str(out/'openpine.bundle'))
