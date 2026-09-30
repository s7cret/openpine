"""Pinned frontend command declaration and exact primary execution."""
import json,os,sys,shutil
from pathlib import Path
OUT=Path(os.environ['OPENPINE_ATTEMPT_ROOT']).resolve()
HOST=OUT/'build-copies/openpine';BASE=OUT/'frontend';UI=BASE/'source'
NPM='/home/moltbot1/.local/share/openpine/rc6-stabilization-20260930/toolchain/npm'
PY='/home/moltbot1/.local/share/openpine/rc6-stabilization-20260930/backend-campaign/native-envs/py313/bin/python'

def specifications():
    return [
        {'role':'frontend-openapi','argv':[PY,'-B',str(HOST/'scripts/export_openapi.py'),str(BASE/'openapi.json')],'cwd':str(HOST)},
        {'role':'frontend-install','argv':[NPM,'ci','--proxy=http://127.0.0.1:1083','--https-proxy=http://127.0.0.1:1083'],'cwd':str(UI)},
        {'role':'frontend-tests','argv':[NPM,'run','test:coverage','--','--reporter=default','--reporter=json','--outputFile.json='+str(BASE/'vitest.json')],'cwd':str(UI)},
        {'role':'frontend-build','argv':[NPM,'run','build'],'cwd':str(UI)},
        {'role':'frontend-node','argv':[NPM,'run','test:node'],'cwd':str(UI)},
        {'role':'frontend-client-generation','argv':[PY,'-B',str(HOST/'scripts/generate_openapi_ts.py'),str(BASE/'openapi.json'),str(BASE/'openapi.ts')],'cwd':str(UI)},
        {'role':'frontend-client-drift','argv':[PY,'-I','-c','from pathlib import Path; import sys; assert Path(sys.argv[1]).read_bytes()==Path(sys.argv[2]).read_bytes()',str(BASE/'openapi.ts'),str(UI/'src/api/generated/openapi.ts')],'cwd':str(UI)},
        {'role':'frontend-e2e','argv':[NPM,'run','test:e2e','--','--workers=1','--reporter=json','--output='+str(BASE/'test-results')],'cwd':str(UI)},
        {'role':'frontend-audit','argv':[NPM,'audit','--audit-level=moderate','--json','--proxy=http://127.0.0.1:1083','--https-proxy=http://127.0.0.1:1083'],'cwd':str(UI)},
    ]

def main():
    sys.path.insert(0,str(HOST))
    from openpine.verification.execution_process import run_logged
    from openpine.verification.execution_campaign import descriptor
    from openpine.verification.stabilization_evidence import verify_frontend
    from openpine.verification.execution_identity import write_once_json
    policy=json.loads((HOST/'verification/execution-policy.json').read_text())['stabilization']['frontend']
    assert policy['commands']==specifications()
    plan=json.loads(Path(os.environ['OPENPINE_PLAN']).read_text())
    files={n.removeprefix('openpine-ui/'):m for n,m in plan['source']['components']['openpine']['files'].items() if n.startswith('openpine-ui/')}
    binding={'plan_hash':plan['content_hash'],'candidate_hash':plan['source']['content_hash'],'source_root':str(UI),'source_files':files}
    frozen=json.loads((HOST/'verification/execution-policy.json').read_text())['stabilization']['package_harness_inputs']
    inputs={**frozen, **{'frontend-source:'+n:{'path':str(UI/n),'sha256':m['sha256']} for n,m in files.items()}}
    BASE.mkdir(exist_ok=False)
    shutil.copytree(HOST/'openpine-ui',UI,ignore=shutil.ignore_patterns('node_modules','dist','coverage','test-results','playwright-report','*.tsbuildinfo'))
    private=BASE/'private';private.mkdir()
    for name in ['home','cache']:(private/name).mkdir()
    tmp=Path(os.environ['TMPDIR'])/('rc6-ui-'+OUT.name)
    tmp.mkdir(exist_ok=False)
    env={k:v for k,v in os.environ.items() if k in ['PATH','LANG','LC_ALL','TZ']}
    env.update(HOME=str(private/'home'),TMPDIR=str(tmp),XDG_CACHE_HOME=str(private/'cache'),npm_config_cache=str(private/'cache/npm'),PYTHONDONTWRITEBYTECODE='1',PYTHONPATH=str(HOST),OPENPINE_OPENAPI_SCHEMA=str(BASE/'openapi.json'),PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/usr/bin/chromium',HTTPS_PROXY='http://127.0.0.1:1083',HTTP_PROXY='http://127.0.0.1:1083',NO_PROXY='localhost,127.0.0.1,::1',NODE_OPTIONS='--max-old-space-size=1024')
    commands=[]
    for index,spec in enumerate(policy['commands']):
        folder=BASE/'commands'/str(index)
        def primary_artifacts():
            return {'tests':descriptor(OUT,BASE/'vitest.json'),'outputs':[descriptor(OUT,p) for p in sorted((UI/'dist').rglob('*')) if p.is_file()]}
        r=run_logged(spec['argv'],cwd=Path(spec['cwd']),output=folder,env=env,timeout=1200,inputs=inputs,binding=binding,artifacts=primary_artifacts if index==len(policy['commands'])-1 else None)
        commands.append(descriptor(OUT,folder/'command.json'))
        write_once_json(BASE/f'command-{index}-descriptor.json',commands[-1])
        print('FRONTEND',spec['role'],r['returncode'],flush=True)
        assert r['ok'],(folder/'stdout.log').read_text()+(folder/'stderr.log').read_text()
    entry={'binding':binding,'commands':commands,'tests':descriptor(OUT,BASE/'vitest.json'),'outputs':[descriptor(OUT,p) for p in sorted((UI/'dist').rglob('*')) if p.is_file()]}
    policy['commands']=[{**s,'inputs':frozen} for s in policy['commands']]
    result=verify_frontend(plan,OUT,entry,policy)
    write_once_json(BASE/'entry.json',entry);write_once_json(BASE/'owner-readback.json',result)
    print('FRONTEND_OWNER_READBACK',json.dumps(result),flush=True)
if __name__=='__main__': main()
