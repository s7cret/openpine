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
    'research':('input','search','research','protection'),
    'numeric':('technical','launch','loop','array'),
    'contexts':('environment','signal','context_graph'),
    'exits':('partial','pending'),
    'lots':('lot',),
    'oca':('order_group',),
    'trailing':('trailing',),
    'economics':('economics',),
    'risk':('risk',),
}
SUITES=('core',*GROUPS)


def verify_helpers(http,cli,work,base,suites):
    result={};completed=[]
    for suite in suites:
        if suite not in GROUPS:raise ValueError('Unknown installed helper suite.')
        for helper in GROUPS[suite]:
            print(f'Installed suite {suite}: {helper}',file=sys.stderr,flush=True)
            verify=importlib.import_module('tools.installed_'+helper+'_smoke').verify
            value=verify(http,cli,work,base) if helper=='research' else verify(http,cli,work)
            if type(value) is not dict or result.keys() & value.keys():raise AssertionError('Installed helper result keys overlap.')
            result.update(value);completed.append(helper)
    return {**result,'completed_helpers':completed}


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
