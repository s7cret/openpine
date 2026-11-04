"""Closed installed-acceptance inventory; every helper belongs to exactly one suite."""
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from tools.specification import ROOT
from tools.acceptance_http import request_json

GROUPS={
    # Measured run 40681726120 on both OSes: schedule the indivisible long
    # helpers first, distribute target helpers among shorter existing suites.
    'lots':('lot',),
    'oca':('order_group',),
    'exits':('pending',),
    'signals':('dynamic_signals','numeric_targets','markers','labels','lines','line_extensions','boxes','tables','linefills'),
    'trailing':('trailing','function_targets'),
    'windows':('dynamic_windows','scalar_targets','corpus_graphs'),
    'targets':('computed_targets','primary_targets','upgrade','corpus_targets'),
    'economics':('economics','corpus'),
    'research':('input','search','research','protection','resilience','preparation','partial'),
    'performance':('performance',),
    'risk':('risk',),
    'numeric':('technical','launch','loop','array','colors','bands','corpus_numeric'),
    'contexts':('context_graph','lower_tf','lower_events','prepared_requests','passport','corpus_contexts'),
    'observed':('observed','environment','corpus_observed'),
    'cycle':('cycle','signal','corpus_cycle'),
    'intrabar':('intrabar','corpus_intrabar'),
    'magnifier':('magnifier','corpus_magnifier'),
}
SUITES=('core',*GROUPS)


def checkpoint_diagnostics(work):
    """Retain bounded timing evidence before temporary attempt files disappear."""
    result={'files':0,'samples':0,'unreadable_files':0,'maximum_seconds':{},'sampled_total_seconds':{}}
    for path in Path(work).rglob('checkpoint-timings.json'):
        try:
            value=json.loads(path.read_bytes())
            if value.get('schema_version')!='openpine.checkpoint-timings.1' or not 1<=len(value['latest'])<=16:raise ValueError('Invalid checkpoint timing sidecar.')
            result['files']+=1
            for row in value['latest']:
                result['samples']+=1
                for key,seconds in row.items():
                    if not key.endswith('_seconds'):continue
                    if type(seconds) not in (float,int) or not 0<=seconds<float('inf'):raise ValueError('Invalid checkpoint timing.')
                    result['maximum_seconds'][key]=max(result['maximum_seconds'].get(key,0.),seconds)
                    result['sampled_total_seconds'][key]=result['sampled_total_seconds'].get(key,0.)+seconds
        except (OSError,ValueError,KeyError,TypeError):result['unreadable_files']+=1
    return result


def verify_helpers(http,cli,work,base,suites):
    result={};completed=[]
    for suite in suites:
        if suite not in GROUPS:raise ValueError('Unknown installed helper suite.')
        for helper in GROUPS[suite]:
            print(f'Installed suite {suite}: {helper}',file=sys.stderr,flush=True)
            verify=importlib.import_module('tools.installed_'+helper+'_smoke').verify
            try:value=verify(http,cli,work,base) if helper in ('research','resilience') else verify(http,cli,work)
            except BaseException:
                print('Checkpoint phase diagnostics: '+json.dumps(checkpoint_diagnostics(work),sort_keys=True),file=sys.stderr,flush=True)
                raise
            if type(value) is not dict or result.keys() & value.keys():raise AssertionError('Installed helper result keys overlap.')
            result.update(value);completed.append(helper)
    diagnostics=checkpoint_diagnostics(work)
    if diagnostics['unreadable_files']:raise AssertionError(diagnostics)
    return {**result,'completed_helpers':completed,'checkpoint_phase_diagnostics':diagnostics}


def run_suite(suite,expected_root=None):
    if suite not in GROUPS:raise ValueError('Unknown installed suite.')
    if sys.version_info[:3]!=(3,14,8) or expected_root and Path(expected_root).resolve()!=ROOT:
        raise AssertionError('Use the installed Python 3.14.8 tree.')
    from apps.ui.server import Server
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix='openpine-'+suite+'-') as directory:
        work=Path(directory)
        def cli(*args):
            value=subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),*map(str,args)],cwd=work,
                capture_output=True,text=True,encoding='utf-8',timeout=90)
            if value.returncode:raise AssertionError(value.stdout[-2000:]+' '+value.stderr[-2000:])
            return json.loads(value.stdout) if value.stdout.strip() else None
        server=Server(('127.0.0.1',0),work/'http');thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{server.server_port}';token=''
        def http(path,payload=None):return request_json(base,token,path,payload)
        try:
            token=http('/api/config')['token'];result=verify_helpers(http,cli,work,base,(suite,))
        finally:server.shutdown();thread.join(timeout=10);server.server_close()
    print(json.dumps({'status':'passed','python':sys.version.split()[0],'root':str(ROOT),'installed_suite':suite,
        'network_required':False,**result},sort_keys=True))
    return 0
