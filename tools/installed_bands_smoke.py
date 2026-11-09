"""Shared-wheel visual references and frozen color settings through both routes."""
import sys,time,uuid
from tools.specification import ROOT
from packages.contracts.wire import canonical

def verify(http,cli,work):
    bars=ROOT/'conformance/functions/fixed.json'
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
        print(f'Installed bands v{v}: color settings, durable references, ranges and saved IR',file=sys.stderr,flush=True)
        source=ROOT/f'examples/corpus/bands-routing-v{v}.pine'
        compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.as_posix()})
        if compiled.get('status')!='compiled':raise AssertionError(compiled)
        values={'c':'#abcdef80'} if v>=4 else {}
        if v>=4:
            preset=http('/api/presets',{'input_ref':compiled['input_ref'],'name':f'bands-{v}','inputs':values,'expected_revision':None})
            values=preset['inputs']
        launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':compiled['input_ref'],
            'primary':{'kind':'snapshot','snapshot_id':snapshot},'contexts':[],'inputs':values,'timeout_seconds':120,'gap_policy':'fail',
            'checkpoint_interval_bars':2,'pause_after_bars':2})
        if launch['status']!='ready':raise AssertionError(launch)
        job=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id'];finish(job,'paused')
        if v>=4:http('/api/presets',{'input_ref':compiled['input_ref'],'name':f'bands-{v}','inputs':{'c':'#FFFFFF00'},'expected_revision':preset['revision']})
        http('/api/jobs/'+job+'/resume',{});state=finish(job,'succeeded')
        result=http('/api/jobs/'+job+'/result')['result'];outputs=result['outputs'];color='#ABCDEF80' if v>=4 else '#12345680'
        if state['resumed_from_bar']!=2 or [o['kind'] for o in outputs]!=['plot','plot','fill','hline','hline','fill']:raise AssertionError('Visual worker/resume graph differs.')
        for i,ends in ((2,(0,1)),(5,(3,4))):
            if outputs[i]['colors']!=[color]*4 or outputs[i]['references']!=[outputs[j]['output_id'] for j in ends] or outputs[i]['fillgaps'] is not False:raise AssertionError('Frozen color/reference identity or default gap policy differs.')
        chart=http(f'/api/jobs/{job}/chart?start=1&limit=2')
        if chart['outputs'][2]['colors']!=[color]*2 or chart['outputs'][0]['values']!=[20.,30.]:raise AssertionError('Visual chart window differs.')
        artifact=work/f'bands-{v}.json';cli('compile','--source',source,'--profile','historical','--output',artifact)
        contexts=work/f'bands-contexts-{v}.json';contexts.write_bytes(canonical({'schema_version':'openpine.context-inputs.1','primary_context':result['primary_context'],'contexts':[]}))
        inputs=work/f'bands-inputs-{v}.json';inputs.write_bytes(canonical(values))
        direct=cli('job','--artifact',artifact,'--bars',bars,'--inputs',inputs,'--contexts',contexts,'--state-dir',work/f'bands-cli-{v}')
        if direct['response']['result']!=result:raise AssertionError('Saved IR CLI and resumed HTTP visual result differ.')
    source=work/'bands-window-gradient.pine'
    appearance=(ROOT/'conformance/visual-bands/v6-plot-appearance.pine').read_text(encoding='utf-8')
    assert 'indicator("Bands")' in appearance
    appearance=appearance.replace(
        'indicator("Bands")',
        'indicator("Bands",format=format.percent,precision=4,scale=scale.left,explicit_plot_zorder=true,behind_chart=false)',1)
    source.write_text(appearance+
        'fill(a,b,close+8,close-5,#12345680,close<40 ? #0000FF : na,fillgaps=true,display=display.all)\n'
        'hi=hline(30,display=display.none)\nlo=hline(5)\nfill(hi,lo,close,0,#76543280,na)\n'
        'show=input.bool(true)\nplaces=input.int(0)\n'
        'hidden=plot(close+1000,offset=1,display=show ? display.all : display.none)\n'
        'fill(hidden,b,#12345680,display=display.none)\n'
        'plot(close-10,"Destinations",offset=-1,show_last=n,force_overlay=true,format=format.price,precision=places,'
        'display=(show ? display.none : display.all - display.pane) - display.status_line)\n',encoding='utf-8')
    values={'n':2,'show':False,'places':16}
    # Match the CLI source bytes, including CRLF on Windows.
    compiled=http('/api/compile',{'source':source.read_bytes().decode('utf-8'),'source_name':source.as_posix()})
    assert compiled['status']=='compiled',compiled
    preset=http('/api/presets',{'input_ref':compiled['input_ref'],'name':'bands-display','inputs':values,'expected_revision':None})
    launch=http('/api/launches',{'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,
        'input_ref':compiled['input_ref'],'primary':{'kind':'snapshot','snapshot_id':snapshot},
        'contexts':[],'inputs':preset['inputs'],'timeout_seconds':120,'gap_policy':'fail',
        'checkpoint_interval_bars':2,'pause_after_bars':2})
    assert launch['status']=='ready',launch
    job=http('/api/launches/'+launch['launch_id']+'/start',{})['job_id']
    finish(job,'paused')
    http('/api/presets',{'input_ref':compiled['input_ref'],'name':'bands-display','inputs':{**values,'show':True,'places':1},
        'expected_revision':preset['revision']})
    http('/api/jobs/'+job+'/resume',{})
    assert finish(job,'succeeded')['resumed_from_bar']==2
    result=http('/api/jobs/'+job+'/result')['result']
    a,b,fill,gradient,high,low,levels,hidden,hidden_fill,destinations=result['outputs']
    assert (a['show_last'],b['show_last'],fill['show_last'])==(2,None,2)
    assert fill['references']==[a['output_id'],b['output_id']] and fill['fillgaps']
    assert a['values']==[10.,20.,30.,40.] and b['values']==[12.,22.,32.,42.]
    assert (a['histbase'],a['trackprice'],a['linestyle'],b['join'])==(25,True,'dotted',True)
    assert gradient['mask']=='plot' and gradient['references']==[a['output_id'],b['output_id']]
    assert levels['mask']=='hline' and levels['references']==[high['output_id'],low['output_id']]
    assert [item.get('display') for item in (gradient,high,levels,hidden,hidden_fill)]==['all','none',None,'none','none']
    assert hidden['values']==[1010.,1020.,1030.,1040.] and hidden_fill['references']==[hidden['output_id'],b['output_id']]
    assert [row['top_value'] for row in gradient['gradients']]==[18.,28.,38.,48.]
    assert gradient['gradients'][-1]['bottom_color'] is None and levels['gradients'][0]['top_value']==10.
    left,right,_,window,*_,hidden_window,hidden_fill_window,axis=http(f'/api/jobs/{job}/chart?start=1&limit=2')['outputs']
    assert window['gradients']==gradient['gradients'][1:3] and window['values']==[None,None]
    assert [(p['index'],p['value']) for p in left['halo']]==[(2,40.)]
    assert [(p['index'],p['value']) for p in right['halo']]==[(-1,12.)]
    assert hidden_window['display']==hidden_fill_window['display']=='none'
    assert hidden_window['values']==[1020.,1030.] and [(p['index'],p['value']) for p in hidden_window['halo']]==[(-1,1010.)]
    first_window=http(f'/api/jobs/{job}/chart?start=0&limit=1')['outputs']
    first=first_window[0]
    assert (left['last'],left['last_color'])==(first['last'],first['last_color'])==(40.,'#2196F3FF')
    assert axis['display']==destinations['display']==['price_scale','data_window']
    assert destinations['values']==[0.,10.,20.,30.] and axis['values']==[10.,20.]
    assert result['declaration']['number_format']=={
        'declaration':{'format':'percent','precision':4},
        'plots':[{'output_id':destinations['output_id'],'style':{'format':'price','precision':16}}]}
    summary=http(f'/api/jobs/{job}/summary')['result']
    assert result['declaration']['chart_scale']==summary['declaration']['chart_scale']=='left'
    metadata=http(f'/api/jobs/{job}/chart?start=1&limit=1')
    assert metadata['price_precision'] is None and metadata['chart_scale']=='left'
    order={'explicit_plot_zorder':True,'behind_chart':False}
    assert result['declaration']['visual_order']==summary['declaration']['visual_order']==metadata['visual_order']==order
    assert axis['last']==first_window[-1]['last']==30. and axis['force_overlay'] and axis['show_last']==2
    artifact=work/'bands-window.json'
    cli('compile','--source',source,'--profile','historical','--output',artifact)
    contexts=work/'bands-window-contexts.json'
    contexts.write_bytes(canonical({'schema_version':'openpine.context-inputs.1','primary_context':result['primary_context'],'contexts':[]}))
    inputs=work/'bands-window-inputs.json'
    inputs.write_bytes(canonical(values))
    direct=cli('job','--artifact',artifact,'--bars',bars,'--inputs',inputs,'--contexts',contexts,'--state-dir',work/'bands-window-cli')
    assert direct['response']['result']==result
    return {'bands_versions':list(range(1,7)),'bands_color_input_versions':[4,5,6],'bands_worker_resume_bar':2,
        'bands_frozen_settings_after_preset_edit':True,'bands_cli_http_equal':True,'bands_parquet_chart_range':True,
        'bands_default_fillgaps_false':True,'bands_external_equivalence':False,'bands_windows_worker_resume_bar':2,
        'bands_windows_cli_http_equal':True,'bands_windows_offset_halos':True,
        'bands_appearance_checkpoint_and_export':True,'bands_appearance_track_across_pages':True,
        'bands_gradient_checkpoint_export_range':True,'bands_gradient_masks':['plot','hline'],
        'bands_display_frozen_after_preset_edit':True,'bands_display_checkpoint_export_range':True,
        'plot_destinations_frozen_checkpoint_and_export':True,'plot_destinations_axis_across_pages':True,
        'number_format_frozen_checkpoint_and_export':True,'number_format_raw_values_preserved':True,
        'chart_scale_checkpoint_export_and_ranges':True,'visual_order_checkpoint_export_and_ranges':True}
