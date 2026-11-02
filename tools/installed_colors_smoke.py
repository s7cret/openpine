"""Shared-wheel color values through CLI, HTTP, workers, restore and chart windows."""
import sys,time,uuid
from tools.specification import ROOT
from packages.contracts.wire import canonical

def verify(http,cli,work):
    bars=ROOT/'conformance/functions/fixed.json'
    snapshot=http('/api/snapshots',{'provider':'upload','raw':bars.read_text(encoding='utf-8')})['sha256']
    def finish(job,expected):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            value=http('/api/jobs/'+job)
            if value['status']==expected:return value
            if value['status'] in ('failed','cancelled','timed_out','succeeded'):raise AssertionError(value)
            time.sleep(.1)
        raise TimeoutError(value)
    for v in range(1,7):
        print(f'Installed colors: v{v} source/IR, worker pause/resume and range',file=sys.stderr,flush=True)
        source=ROOT/f'examples/corpus/colors-routing-v{v}.pine'
        compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.as_posix()})
        if compiled.get('status')!='compiled':raise AssertionError(compiled)
        launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':{},'timeout_seconds':120,
            'gap_policy':'fail','checkpoint_interval_bars':2,'pause_after_bars':2})
        if launch['status']!='ready':raise AssertionError(launch)
        job=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id']
        finish(job,'paused');http('/api/jobs/'+job+'/resume',{});state=finish(job,'succeeded')
        result=http('/api/jobs/'+job+'/result')['result'];out=result['outputs'][0]
        expected=['#00FF00FF']*2+(['#F23645FF']*2 if v==6 else ['#FF5252FF']*2)
        if state['resumed_from_bar']!=2 or out['colors']!=expected or out['values']!=[10.,30.,60.,100.] or (out['linewidth'],out['style'])!=(3,'line'):
            raise AssertionError('Color worker or output-prefix restore differs.')
        chart=http(f'/api/jobs/{job}/chart?start=1&limit=2')['outputs'][0]
        if chart['colors']!=expected[1:3] or chart['values']!=[30.,60.]:raise AssertionError('Color chart range differs.')
        artifact=work/f'colors-v{v}.json';cli('compile','--source',source,'--profile','historical','--output',artifact)
        primary=work/f'colors-contexts-v{v}.json'
        primary.write_bytes(canonical({'schema_version':'openpine.context-inputs.1','primary_context':result['primary_context'],'contexts':[]}))
        direct=cli('job','--artifact',artifact,'--bars',bars,'--contexts',primary,'--state-dir',work/f'colors-cli-{v}')
        if direct['response']['result']!=result:
            other=direct['response']['result']
            raise AssertionError({'color_routes_differ':{k:{'cli':other.get(k),'http':result.get(k)} for k in other.keys()|result.keys() if other.get(k)!=result.get(k)}})
    return {'color_versions':list(range(1,7)),'color_worker_resume_bar':2,'color_rows':24,
        'color_source_saved_ir_equal':True,'color_parquet_chart_ranges':True,'color_legacy_palette_reconstructed':True,'color_external_equivalence':False}
