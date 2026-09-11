"""Bind reviewed execution to a metadata-only release tree, then run unchanged gates."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import xml.etree.ElementTree as ET

CODE='8aa6a3e4f6f172652daf3ba37aade9f21e122c6c'
BASE='4a84ff2873caef95d8eb986dc1e999c379f33fbf'
TEMP=Path(os.environ['RUNNER_TEMP'])
HOST=Path(os.environ['GITHUB_WORKSPACE'])
DRAFT=TEMP/'draft'
OUT=TEMP/'final-gate'
OUT.mkdir(exist_ok=True)

def read(path):return json.loads(path.read_bytes())
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
def git(*args,env=None):return subprocess.check_output(['git',*args],cwd=HOST,text=True,env=env).strip()

inventory=read(DRAFT/'inventory.json')
proof={}
for version in ('3.11','3.13'):
    root=TEMP/('root-'+version)
    summary=read(root/'review-summary.json')
    if summary['candidate']!=CODE or summary['baseline']!=BASE or summary['harness']!='084f30aa7af9ba7c5b93f1169ed05f82f82abfc0':raise ValueError('wrong executed source')
    if read(root/'proposed-inventory.json')!=inventory:raise ValueError('unreviewed inventory')
    counts={}
    for name,wanted in inventory.items():
        old=read(root/f'baseline-{name}.inventory.json')
        actual=read(root/f'execution-{name}.inventory.json')
        if not actual['ok'] or actual['collect_only'] or actual['errors']:raise ValueError('unsuccessful required execution')
        if any(actual[k]!=wanted[k] for k in wanted):raise ValueError('execution identity mismatch')
        if not set(old['nodeids']).issubset(actual['nodeids']) or old['deselected']!=actual['deselected']:raise ValueError('baseline tests lost')
        cases=list(ET.parse(root/f'{name}.xml').iter('testcase'))
        if len(cases)!=wanted['count'] or any(c.find(t) is not None for c in cases for t in ('failure','error','skipped')):raise ValueError('bad junit')
        counts[name]={'baseline':old['count'],'executed':len(cases),'node_ids_sha256':actual['sha256']}
    if sum(c['executed'] for c in counts.values())!=20259 or sum(c['baseline'] for c in counts.values())!=16787:raise ValueError('wrong denominator')
    if summary['architecture_exit'] or summary['openapi_exit'] or any(summary['builds'].values()) or not all(summary['tracked_sources_clean'].values()):raise ValueError('failed architecture/build/source gate')
    proof[version]=counts
if proof['3.11']!=proof['3.13']:raise ValueError('interpreter inventories diverged')

current=TEMP/('root-'+os.environ['REVIEW_PYTHON'])
for name in inventory:
    if (TEMP/'root-3.11'/f'{name}.source.tar.gz').read_bytes()!=(TEMP/'root-3.13'/f'{name}.source.tar.gz').read_bytes():raise ValueError('source archive divergence')
stack=TEMP/'stack'
for name in inventory:
    dest=stack/name;dest.mkdir(parents=True)
    with tarfile.open(current/f'{name}.source.tar.gz') as archive:archive.extractall(dest,filter='data')

# Read draft from the authorized harness, then prepare only the exact tested tree.
git('checkout','--detach',CODE)
write(HOST/'verification/inventory.json',inventory)
progress=read(HOST/'verification/stage2-progress.json')
progress.update({'latest_verified_source':CODE,'latest_verified_tree':'2a7869613ed508f86e25b202a1302cc5e3164b6a','joint_test_count':20259,'joint_verification_run':34594834647,'publication_receipt':'docs/RC6_STAGE2_RECURSIVE_PUBLICATION.md','status':'in_progress','full_stage2_accepted':False,'tradingview_verified':False})
progress['saved_recursive_wave']={'status':'joint_linux_verified_pending_guarded_release_readback','source':CODE,'source_run':34594834647,'selected_tests_per_interpreter':20259,'previous_tests_retained':16787,'added_tests':3472,'compiler_normalized_target_qualifiers':'open_residual_normal_source_enforced_by_producer','exported_imported_methods_bc':'design_only_not_implemented','full_stage2_accepted':False}
for path in ['docs/RC6_STAGE2_RECURSIVE_PUBLICATION.md','verification/recursive-wave-review-20260911.json']:
    if path not in progress['evidence_paths']:progress['evidence_paths'].append(path)
write(HOST/'verification/stage2-progress.json',progress)
shutil.copy(DRAFT/'progress.md',HOST/'docs/RC6_REVIEW_PROGRESS.md')
shutil.copy(DRAFT/'receipt.md',HOST/'docs/RC6_STAGE2_RECURSIVE_PUBLICATION.md')
write(HOST/'verification/recursive-wave-review-20260911.json',{'schema_id':'openpine.recursive_wave_review.v1','date':'2026-09-11','source':CODE,'baseline':BASE,'root_run':34594834647,'execution':proof,'reviewed_inventory':inventory,'metadata_review':{'raw_manifest_changes':['ta.tsi.short_length:series->simple','ta.tsi.long_length:series->simple','ta.valuewhen.occurrence:series->simple'],'literal_transformed_hash':'sha256:3bbb194318736ff97aff6a50ef4a3c4238fda3191f3971ab2e5fe9e14e27c2f7','old_parser_compiler_tests_unchanged':True,'legacy_catalog_v1_v4_unchanged':True,'recursive_manual_rows_independently_recalculated':20,'recursive_unknown_rows_preserved':13},'full_stage2_accepted':False,'tradingview_verified':False,'integration':'requires_final_gate_and_remote_ref_readback'})
allowed={'verification/inventory.json','verification/stage2-progress.json','verification/recursive-wave-review-20260911.json','docs/RC6_REVIEW_PROGRESS.md','docs/RC6_STAGE2_RECURSIVE_PUBLICATION.md'}
git('add','--',*sorted(allowed))
if set(git('diff','--cached','--name-only',CODE).splitlines())!=allowed:raise ValueError('unexpected code changes')
tree=git('write-tree')
env={**os.environ,'GIT_AUTHOR_NAME':'OpenPine Review','GIT_AUTHOR_EMAIL':'openpine-review@users.noreply.github.com','GIT_COMMITTER_NAME':'OpenPine Review','GIT_COMMITTER_EMAIL':'openpine-review@users.noreply.github.com','GIT_AUTHOR_DATE':'2026-09-11T12:00:00Z','GIT_COMMITTER_DATE':'2026-09-11T12:00:00Z'}
head=git('commit-tree',tree,'-p',os.environ['GITHUB_SHA'],'-m','chore(stage2): bind reviewed Linux inventory and current progress to the saved semantic wave',env=env)
git('checkout','--detach',head)
(OUT/'prepared-head.txt').write_text(head+'\n');(OUT/'prepared-tree.txt').write_text(tree+'\n')

wheels=sorted(str(p) for p in (current/'dist').rglob('*.whl'))
if len(wheels)!=8:raise ValueError('eight exact component wheels required')
subprocess.run([sys.executable,'-m','pip','install',*wheels,'pytest','pytest-asyncio','ruff','build'],check=True)
evidence=OUT/'aggregate';evidence.mkdir()
for name in inventory:shutil.copy(current/f'execution-{name}.inventory.json',evidence/f'{name}.inventory.json')
shutil.copytree(current/'corpus-evidence/observations',evidence/'observations')
sources=read(HOST/'docs/RC6_LIFECYCLE_SOURCES.json')
if {**sources,'openpine':CODE}!=read(current/'candidate-pins.json'):raise ValueError('pins mismatch')
write(evidence/'source-pins.json',sources)
subprocess.run([sys.executable,'-m','openpine.verification','stage1','--host-root',str(HOST),'--stack-root',str(stack),'--evidence',str(evidence)],check=True)
subprocess.run([sys.executable,'-m','pytest','-q','rc6_tests/test_rc6_review_ledger.py','rc6_tests/test_rc6_stage1_foundation.py','--junitxml='+str(OUT/'metadata-regressions.xml')],env={**os.environ,'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'},check=True)
# The permanent lint/build step is executed verbatim, not replaced by a weaker list.
import yaml
workflow=yaml.safe_load((HOST/'.github/workflows/rc6-native.yml').read_text())
step=next(s for s in workflow['jobs']['verify']['steps'] if s.get('name')=='Validate changed runtime lint imports and build clean distributions')
(TEMP/'evidence').mkdir(exist_ok=True)
subprocess.run(['bash','-euo','pipefail','-c',step['run']],env={**os.environ,'PINE_STACK_ROOT':str(stack)},check=True)
if git('status','--porcelain','--untracked-files=no'):raise ValueError('tracked source mutated')
write(OUT/'receipt.json',{'schema_id':'openpine.saved_wave_final_gate.v1','head':head,'tree':tree,'tested_runtime_source':CODE,'functional_execution_run':34594834647,'final_gate_run':int(os.environ['GITHUB_RUN_ID']),'python':os.environ['REVIEW_PYTHON'],'runtime_tests_corpus_and_permanent_workflow_unchanged':True,'reviewed_inventory':20259,'all_old_ids_retained':16787,'stage1_aggregate':True,'metadata_regressions':True,'permanent_lint_build':True,'new_full_test_execution_claimed':False,'full_stage2_accepted':False})
git('update-ref','refs/heads/validated-saved-wave',head)
git('bundle','create',str(OUT/'verified.bundle'),'refs/heads/validated-saved-wave')
for name in allowed:
    dest=OUT/'release-metadata'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy(HOST/name,dest)
