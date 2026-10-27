"""Run the combined file/worker/recovery flow from the installed tree."""
import json,subprocess,sys
from tools.specification import ROOT

def verify(http,cli,work):
    command=[sys.executable,'-X','utf8','-c',"import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('tools.preparation_flow',run_name='__main__')",str(ROOT),'--directory',str(work/'preparation-flow')]
    value=subprocess.run(command,cwd=work,capture_output=True,text=True,encoding='utf-8',timeout=600)
    if value.returncode:raise AssertionError(value.stdout[-2000:]+' '+value.stderr[-4000:])
    result=json.loads(value.stdout);assert result['status']=='passed' and [r['version'] for r in result['versions']]==list(range(1,7))
    return {'preparation_flow_versions':list(range(1,7)),'preparation_flow_offline_versions':[1,6],'preparation_flow_lower_versions':[5,6],'preparation_flow_restart':True}
