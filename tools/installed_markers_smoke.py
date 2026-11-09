"""Shared-wheel signal colors, frozen settings, durable offsets and window halos."""
import sys,time,uuid
from tools.specification import ROOT
from packages.contracts.wire import canonical

def verify(http,cli,work):
    bars=ROOT/'conformance/visual-signals/bars.json'
    snapshot=http('/api/snapshots',{'provider':'upload','raw':bars.read_text(encoding='utf-8')})['sha256']
    def finish(job,wanted):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            state=http('/api/jobs/'+job)
            if state['status']==wanted:return state
            if state['status'] in ('failed','cancelled','timed_out','succeeded'):raise AssertionError(state)
            time.sleep(.1)
        raise TimeoutError(state)
    for v in range(1,7):
        print(f'Installed markers v{v}: color settings, durable offsets, ranges and saved IR',file=sys.stderr,flush=True)
        source=ROOT/f'examples/corpus/signals-routing-v{v}.pine'
        compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.as_posix()})
        if compiled.get('status')!='compiled':raise AssertionError(compiled)
        values={'c':'#abcdef80'} if v>=4 else {}
        if v>=4:
            preset=http('/api/presets',{'input_ref':compiled['input_ref'],'name':f'markers-{v}','inputs':values,'expected_revision':None})
            values=preset['inputs']
        launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':values,'timeout_seconds':120,'gap_policy':'fail',
            'checkpoint_interval_bars':2,'pause_after_bars':2})
        if launch['status']!='ready':raise AssertionError(launch)
        job=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];finish(job,'paused')
        if v>=4:http('/api/presets',{'input_ref':compiled['input_ref'],'name':f'markers-{v}','inputs':{'c':'#FFFFFF00'},'expected_revision':preset['revision']})
        http('/api/jobs/'+job+'/resume',{});state=finish(job,'succeeded')
        result=http('/api/jobs/'+job+'/result')['result'];outputs=result['outputs'];color='#ABCDEF80' if v>=4 else '#12345680'
        if state['resumed_from_bar']!=2 or [o['kind'] for o in outputs[1:]]!=['bgcolor','barcolor','plotshape','plotchar']:raise AssertionError('Signal worker/resume graph differs.')
        if outputs[3]['colors']!=[color]*4 or outputs[3]['textcolors']!=['#FFFF0080']*2+['#FFFFFFFF']*2 or outputs[3]['offset']!=1 or outputs[4]['offset']!=-1:raise AssertionError('Signal colors, source alignment or offsets differ.')
        if outputs[1]['colors']!=[None,None,color,color] or outputs[3]['values']!=[None,None,1.,1.]:raise AssertionError('Conditional signals differ.')
        chart=http(f'/api/jobs/{job}/chart?start=1&limit=2')
        if chart['outputs'][3]['colors']!=[color]*2 or chart['outputs'][0]['values']!=[20.,30.]:raise AssertionError('Signal chart window differs.')
        if chart['outputs'][3]['halo']!=[{'index':-1,'value':None,'color':color,'textcolor':'#FFFF0080'}] or chart['outputs'][4]['halo']!=[{'index':2,'value':40.,'color':color,'textcolor':'#FFFFFFFF'}]:raise AssertionError('Signal boundary halos differ.')
        artifact=work/f'markers-{v}.json';cli('compile','--source',source,'--profile','historical','--output',artifact)
        contexts=work/f'markers-contexts-{v}.json';contexts.write_bytes(canonical({'schema_version':'openpine.context-inputs.1','primary_context':result['primary_context'],'contexts':[]}))
        inputs=work/f'markers-inputs-{v}.json';inputs.write_bytes(canonical(values))
        direct=cli('job','--artifact',artifact,'--bars',bars,'--inputs',inputs,'--contexts',contexts,'--state-dir',work/f'markers-cli-{v}')
        if direct['response']['result']!=result:raise AssertionError('Saved IR CLI and resumed HTTP signal result differ.')
    source = work / 'plot-family-display-v6.pine'
    source.write_text('''//@version=6
indicator("Plot family display")
show=input.bool(false)
plotshape(close>20,"Boolean",location=location.absolute,offset=1,force_overlay=true,display=show ? display.none : display.pane + display.data_window)
plotchar(close==30 ? na : bar_index-1,"Number",offset=-1,display=display.status_line + display.data_window)
plotarrow(close-20,"Arrow",display=display.all - display.pane)
plotbar(open,high,low,close,"OHLC",force_overlay=true,display=display.all - display.pane)
''', encoding='utf-8', newline='\n')
    compiled = http('/api/compile', {'source': source.read_text(encoding='utf-8'), 'source_name': source.as_posix()})
    if compiled['status'] != 'compiled':
        raise AssertionError(compiled)
    setting = {'show': False}
    preset = http('/api/presets', {'input_ref': compiled['input_ref'], 'name': 'plot-family-display',
                                   'inputs': setting, 'expected_revision': None})
    launch = http('/api/launches', {'schema_version': 'openpine.launch-request.1', 'request_id': uuid.uuid4().hex,
        'input_ref': compiled['input_ref'], 'primary': {'kind': 'snapshot', 'snapshot_id': snapshot}, 'contexts': [],
        'inputs': preset['inputs'], 'timeout_seconds': 120, 'gap_policy': 'fail',
        'checkpoint_interval_bars': 2, 'pause_after_bars': 2})
    job = http('/api/launches/' + launch['launch_id'] + '/start', {})['job_id']
    finish(job, 'paused')
    http('/api/presets', {'input_ref': compiled['input_ref'], 'name': 'plot-family-display',
                          'inputs': {'show': True}, 'expected_revision': preset['revision']})
    http('/api/jobs/' + job + '/resume', {})
    if finish(job, 'succeeded')['resumed_from_bar'] != 2:
        raise AssertionError('Plot family display did not resume its published prefix.')
    result = http('/api/jobs/' + job + '/result')['result']
    shape, char, arrow, ohlc = result['outputs']
    if (shape['display'] != ['pane', 'data_window'] or shape['values'] != [False, False, True, True]
            or any(type(value) is not bool for value in shape['values'])
            or char['values'] != [-1, 0, None, 2] or char['display'] != ['status_line', 'data_window']
            or arrow['display'] != ['status_line', 'price_scale', 'data_window']
            or ohlc['values'] != [None] * 4 or ohlc['bars'][-1]['close'] != 40.):
        raise AssertionError('Frozen display, source samples or OHLC output differ.')
    old, last = (http(f'/api/jobs/{job}/chart?start={start}&limit=1')['outputs'] for start in (1, 3))
    if (old[0]['values'][0] is not False or old[1]['values'] != [0] or old[-1]['bars'][0]['close'] != 20.
            or old[-1]['last_close'] != 40. or last[-1]['last_close'] != 40.):
        raise AssertionError('Plot family source rows or the page-independent OHLC axis value differ.')
    artifact = work / 'plot-family-display-ir.json'
    cli('compile', '--source', source, '--profile', 'historical', '--output', artifact)
    contexts = work / 'plot-family-display-contexts.json'
    contexts.write_bytes(canonical({'schema_version': 'openpine.context-inputs.1',
                                   'primary_context': result['primary_context'], 'contexts': []}))
    inputs = work / 'plot-family-display-inputs.json'
    inputs.write_bytes(canonical(setting))
    direct = cli('job', '--artifact', artifact, '--bars', bars, '--inputs', inputs, '--contexts', contexts,
                 '--state-dir', work / 'plot-family-display-cli')
    if direct['response']['result'] != result:
        raise AssertionError('Plot family display saved IR CLI and resumed HTTP results differ.')
    return {'markers_versions':list(range(1,7)),'markers_color_input_versions':[4,5,6],'markers_worker_resume_bar':2,
        'markers_frozen_settings_after_preset_edit':True,'markers_cli_http_equal':True,'markers_parquet_chart_range':True,'markers_offset_window_halos':True,'markers_external_equivalence':False,
        'plot_family_display_frozen_checkpoint_and_export':True,'plot_family_display_ohlc_axis_across_pages':True}
