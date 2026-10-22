"""Acceptance shared by installed-wheel verification and the source-tree gate."""
import json
import time
from packages.compiler.semantic.specification import ROOT


def verify(http,cli,work):
    example=ROOT/'examples/inputs';expected={}
    for version in range(1,7):
        source=example/f'v{version}.pine'
        expected[version]=cli('run','--source',source,'--bars',example/'bars.json','--inputs',example/'overrides.json')['result']
        if expected[version]['settings']['values']['input:length']!=4:raise AssertionError('Installed CLI ignored inputs.')
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(example/'bars.json').read_text(encoding='utf-8')})['sha256']
    exact=http('/api/compile',{'source':'//@version=6\nindicator("exact export")\nn=input.int(9007199254740993)\nplot(n)','source_name':'int64.pine'})
    if http('/api/artifacts/'+exact['artifact_ref'])!=exact['artifact']:raise AssertionError('Installed artifact download changed exact integer content.')
    for version in (1,6):
        source=example/f'v{version}.pine';compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.as_posix()})
        if len(compiled['inputs']['definitions'])!=5:raise AssertionError('Installed input metadata missing.')
        preset=http('/api/presets',{'input_ref':compiled['input_ref'],'name':f'v{version}','inputs':{'length':4,'scale':2,'src':'open'},'expected_revision':None})
        group=http('/api/experiments',{'name':f'Installed v{version}','input_ref':compiled['input_ref'],'snapshot_id':snapshot,'timeout_seconds':60,'inputs':preset['inputs'],'grid':{'scale':[2,3]}})
        identifier=group['experiment_id'];deadline=time.monotonic()+90
        while time.monotonic()<deadline:
            group=http('/api/experiments/'+identifier)
            if group['status']!='running':break
            time.sleep(.04)
        if group['status']!='succeeded':raise AssertionError('Installed experiment failed: '+str(group))
        result=http('/api/jobs/'+group['variants'][0]['job_id']+'/result')['result']
        if result!=expected[version]:raise AssertionError('Installed configured CLI/HTTP results differ.')
    group=cli('experiment','run','--source',example/'v6.pine','--bars',example/'bars.json','--request',example/'experiment.json','--state-dir',work/'input-cli')
    if group['status']!='succeeded' or group['total']!=6:raise AssertionError('Installed experiment CLI did not finish six shared variants.')
    return {'input_cli_versions':list(range(1,7)),'input_http_versions':[1,6],'input_cli_http_equal':True,'finite_experiments':True,'preset_revision':True}
