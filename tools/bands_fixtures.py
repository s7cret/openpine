"""Owned visual band programs and independently specified finite observations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def source(v,body,title='Bands'):
    return f'//@version={v}\n'+('study' if v<5 else 'indicator')+f'("{title}")\n'+body+'\n'

def routing(v):
    setting='c=#12345680' if v<4 else 'c=input(#12345680,type=input.color)' if v==4 else 'c=input.color(#12345680)'
    return source(v,setting+'\na=plot(close)\nb=plot(close+2)\nfill(a,b,c)\nx=hline(10)\ny=hline(30)\nfill(x,y,c)')

def examples():
    return {'bands-rsi-v6.pine':source(6,'c=input.color(#2196F340)\nr=ta.rsi(close,2)\nplot(r,"RSI")\na=hline(30)\nb=hline(70)\nfill(a,b,c)','RSI zones'),
        'bands-ma-channel-v6.pine':source(6,'c=input.color(#FF980040)\nm=ta.sma(close,2)\na=plot(m-2,"Lower")\nb=plot(m+2,"Upper")\nfill(a,b,c)','MA channel')}

def corpus_sources():return {**{f'bands-routing-v{v}.pine':routing(v).encode() for v in range(1,7)},**{n:s.encode() for n,s in examples().items()}}

def corpus_cases(record):
    rows=[]
    for name in corpus_sources():
        v=int(name[-6]);key=name.removesuffix('.pine')
        values=([[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4,[10]*4,[30]*4,[None]*4] if 'routing' in name else
            [[None,None,100.,100.],[30]*4,[70]*4,[None]*4] if 'rsi' in name else [[None,13.,23.,33.],[None,17.,27.,37.],[None]*4])
        rows.append(dict(id=key,version=v,family='bands-routing',files={'source':record('examples/corpus/'+name),'bars':record('conformance/functions/fixed.json')},
            inputs={},expected={'status':'compiled'},oracle={'reason':'Independent closes10/20/30/40; RSI2=100 after two changes, SMA2=(15,25,35); fill carries presentation and no scalar.', 'outputs':values},execution={},
            history_replay={'equivalent':True,'reason':'Primary close-only indicator on identical confirmed observations.'}))
    return rows

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,kinds=None,refs=None,colors=None,qualifiers=None,code=None,category='LANGUAGE_INVALID',inputs=None,forced=(),views=None):
        text=source(v,body);filename=f'v{v}-{name}.pine';files[filename]=text.encode()
        outputs=[]
        # Source positions are derived without compiling or invoking a kernel.
        sites=['call:'+str(i)+':'+str(i+len(line)) for i,line in _lines(text) if line.startswith(('plot(','hline(','fill('))]
        for i,(row,typ) in enumerate(zip(values or [],types or [],strict=True)):
            out={'title':'','type':typ,'values':row}
            view=(views or {}).get(i,{})
            if 'gradients' in view:
                out.update(kind='fill',references=[sites[j] for j in refs[i]],style='gradient')
            elif kinds and kinds[i]:out.update(kind=kinds[i],references=[sites[j] for j in (refs or {}).get(i,[])],fillgaps=kinds[i]=='fill' and v>=4 and name in ('gaps','fill-window','plot-appearance'),colors=colors[i],linewidth=1,style='fill' if kinds[i]=='fill' else 'solid' if kinds[i]=='hline' else 'line')
            elif colors:out.update(colors=colors[i],linewidth=1,style='line')
            if i in forced:out['force_overlay']=True
            out.update(view)
            outputs.append(out)
        expected={'status':'rejected','category':category,'code':code} if code else dict(status='executed',title='Bands',shorttitle='',overlay=False,outputs=outputs,observation_count=4,qualifiers=qualifiers or ['series']*len(outputs))
        case=dict(case_id=f'v{v}-{name}',language_version=v,source=filename,bars='bars.json',expected=expected)
        if inputs is not None:case['inputs']=inputs
        cases.append(case)

    def sample(top,bottom,top_color,bottom_color):
        return dict(top_value=top,bottom_value=bottom,top_color=top_color,bottom_color=bottom_color)

    for v in range(1,7):
        plot_pair='a=plot(close)\nb=plot(close+2)\n'
        hline_pair='a=hline(10)\nb=hline(40)\n'
        if v<4:
            add(v,'plot-display-version','plot(close,display=display.none)',code='VISUAL_DISPLAY_VERSION')
        elif v==4:
            blue=['#2196F3FF']*4
            add(v,'plot-display','show=input(true)\n'
                'plot(close,offset=-1,show_last=2,display=show ? display.all : display.none)\n'
                'a=plot(close+2,display=display.all)',
                [[10.,20.,30.,40.],[12.,22.,32.,42.]],['float']*2,[None,'plot'],colors=[blue,blue],inputs={'show':False},
                views={0:dict(offset=-1,show_last=2,display='none'),1:dict(display='all')})
            add(v,'hline-display-version','hline(10,display=display.none)',code='VISUAL_DISPLAY_VERSION')
            add(v,'fill-display-version',plot_pair+'fill(a,b,#12345680,display=display.none)',
                code='VISUAL_DISPLAY_VERSION')
            add(v,'plot-destinations-version','plot(close,display=display.data_window)',code='VISUAL_DISPLAY_VERSION')
            add(v,'plot-destinations-combination-version','plot(close,display=display.all - display.none)',
                code='VISUAL_DISPLAY_VERSION')
        else:
            blue=['#2196F3FF']*4
            add(v,'band-display','show=input.bool(true)\n'
                'a=plot(close,offset=-1,show_last=3,force_overlay=true,display=show ? display.all : display.none)\n'
                'b=plot(close+2,offset=1,force_overlay=true,display=display.none)\n'
                'fill(a,b,#12345680,show_last=2,fillgaps=true,display=display.all)\n'
                'fill(a,b,40,10,#FF000080,#0000FF,display=display.none)\n'
                'x=hline(10,display=show ? display.all : display.none)\n'
                'y=hline(40,display=display.all)\n'
                'fill(x,y,#12345680,display=display.none)\n'
                'fill(x,y,close,10,#FF000080,na)',
                [[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4,[None]*4,[10]*4,[40]*4,[None]*4,[None]*4],
                ['float']*4+['int','int','float','float'],['plot','plot','fill','fill','hline','hline','fill','fill'],
                {2:[0,1],3:[0,1],6:[4,5],7:[4,5]},[blue,blue,['#12345680']*4,None,blue,blue,['#12345680']*4,None],
                ['series']*4+['const','const','series','series'],inputs={'show':False},forced=(0,1),
                views={0:dict(offset=-1,show_last=3,display='none'),1:dict(offset=1,display='none'),
                       2:dict(show_last=2,fillgaps=True,display='all'),
                       3:dict(mask='plot',fillgaps=False,display='none',gradients=[sample(40,10,'#FF000080','#0000FFFF')]*4),
                       4:dict(display='none'),5:dict(display='all'),6:dict(display='none'),
                       7:dict(mask='hline',fillgaps=False,gradients=[sample(c,10,'#FF000080',None) for c in (10.,20.,30.,40.)])})
            add(v,'plot-display-series','plot(close,display=close>20 ? display.all : display.none)',
                code='VISUAL_DISPLAY_VALUE')
            add(v,'plot-display-mode','plot(close,display=display.pane)',
                [[10.,20.,30.,40.]],['float'],colors=[blue],views={0:dict(display=['pane'])})
            add(v,'plot-destinations','show=input.bool(true)\n'
                'a=plot(close-10,offset=-1,show_last=2,force_overlay=true,display=show ? display.pane : (display.status_line + display.data_window))\n'
                'b=plot(close+2,offset=1,force_overlay=true,display=display.all - (display.pane + display.status_line))\n'
                'fill(a,b,#12345680)\n'
                'plot(close>20 ? close : na,display=display.pane - display.pane)',
                [[0.,10.,20.,30.],[12.,22.,32.,42.],[None]*4,[None,None,30.,40.]],['float']*4,
                ['plot','plot','fill',None],{2:[0,1]},[blue,blue,['#12345680']*4,blue],inputs={'show':False},
                forced=(0,1),views={0:dict(offset=-1,show_last=2,display=['status_line','data_window']),
                                   1:dict(offset=1,display=['price_scale','data_window']),3:dict(display=[])})
            add(v,'plot-destinations-dynamic','plot(close,display=close>20 ? display.pane : display.data_window)',
                code='VISUAL_DISPLAY_VALUE')
            add(v,'marker-destinations','plotarrow(close,display=display.status_line)',
                [[10.,20.,30.,40.]],['float'],views={0:dict(kind='plotarrow',style='plotarrow',
                    colors=['#00BCD4FF']*4,textcolors=[None]*4,location='relative',size='auto',offset=0,
                    text='',char='',linewidth=1,minheight=5,maxheight=100,max_abs=40.,display=['status_line'])})
            add(v,'hline-destinations','hline(10,display=display.data_window)',
                code='VISUAL_DISPLAY_MODE',category='PROFILE_UNCOVERED')
            add(v,'hline-gradient-display',hline_pair+'fill(a,b,40,10,#FF000080,#0000FF,display=display.none)',
                code='VISUAL_DISPLAY_MODE',category='PROFILE_UNCOVERED')
        if v==6:
            add(v,'plot-display-raw','plot(close,display=1)',code='VISUAL_DISPLAY_VALUE')
            add(v,'plot-display-arithmetic','plot(close,display=display.all - display.none)',
                [[10.,20.,30.,40.]],['float'],colors=[blue],
                views={0:dict(display=['pane','status_line','price_scale','data_window'])})
            add(v,'plot-destinations-operator','plot(close,display=display.pane * display.data_window)',
                code='VISUAL_DISPLAY_MODE',category='PROFILE_UNCOVERED')
            add(v,'plot-destinations-screener','plot(close,display=display.pine_screener)',
                code='VISUAL_DISPLAY_MODE',category='PROFILE_UNCOVERED')
        if v<5:
            for mask,pair in (('plot',plot_pair),('hline',hline_pair)):
                add(v,f'gradient-{mask}-version',pair+'fill(a,b,40,10,#FF0000,#0000FF)',
                    code='GRADIENT_FILL_VERSION',category='PROFILE_UNCOVERED')
        else:
            blue=['#2196F3FF']*4
            add(v,'gradient-plot','a=plot(close,offset=-1,show_last=3)\nb=plot(close+2,offset=1)\n'
                'fill(a,b,close+8,close-5,close>20 ? #FF000080 : #00FF00,close<40 ? #0000FF : na,fillgaps=true)',
                [[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4],['float']*3,['plot','plot','fill'],
                {2:[0,1]},[blue,blue,None],
                views={0:dict(offset=-1,show_last=3),1:dict(offset=1),
                       2:dict(mask='plot',fillgaps=True,gradients=[
                           sample(18.,5.,'#00FF00FF','#0000FFFF'),
                           sample(28.,15.,'#00FF00FF','#0000FFFF'),
                           sample(38.,25.,'#FF000080','#0000FFFF'),
                           sample(48.,35.,'#FF000080',None)])})
            add(v,'gradient-hline',hline_pair+'fill(hline1=a,hline2=b,top_value=close<20 ? na : close,'
                'bottom_value=30,top_color=#12345680,bottom_color=na,title="Levels")',
                [[10]*4,[40]*4,[None]*4],['int','int','float'],['hline','hline','fill'],
                {2:[0,1]},[blue,blue,None],['const','const','series'],
                views={2:dict(mask='hline',title='Levels',fillgaps=False,
                              gradients=[sample(c,30,'#12345680',None) for c in (None,20.,30.,40.)])})
            add(v,'gradient-mixed','a=plot(close)\nb=hline(40)\nfill(a,b,40,10,#FF0000,#0000FF)',
                code='NO_MATCHING_OVERLOAD')
            add(v,'gradient-gap-input','g=input.bool(true)\n'+plot_pair+
                'fill(a,b,40,10,#FF0000,#0000FF,fillgaps=g)',code='NO_MATCHING_OVERLOAD')
            add(v,'gradient-numeric-type',plot_pair+'fill(a,b,true,10,#FF0000,#0000FF)',
                code='NO_MATCHING_OVERLOAD')
        if v<3:
            add(v,'plot-offset-version','plot(close,offset=1)',code='SIGNAL_WINDOW_VERSION')
        else:
            force = ',force_overlay=true' if v>=5 else ''
            add(v,'plot-window',f'plot(close,offset=-1,show_last=2{force})\na=plot(close+2,offset=1)',
                [[10.,20.,30.,40.],[12.,22.,32.,42.]],['float']*2,[None,'plot'],
                colors=[['#2196F3FF']*4]*2,forced=(0,) if force else (),
                views={0:{'offset':-1,'show_last':2},1:{'offset':1}})
            add(v,'plot-offset-series','plot(close,offset=bar_index)',code='ARGUMENT_QUALIFIER')
        if v<3:
            add(v,'fill-window-version','a=plot(close)\nb=plot(close+2)\nfill(a,b,#12345680,show_last=2)',
                code='FILL_WINDOW_VERSION',category='PROFILE_UNCOVERED')
        else:
            setting = '' if v==3 else 'n=input(3)\n'
            last = '3' if v==3 else 'n'
            gaps = ',fillgaps=true' if v>=4 else ''
            add(v,'fill-window',setting+f'a=plot(close,offset=-1,show_last={last})\nb=plot(close+2,offset=1)\n'
                f'fill(a,b,#12345680,show_last={last}{gaps})',
                [[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4],['float']*3,
                ['plot','plot','fill'],{2:[0,1]},[['#2196F3FF']*4]*2+[['#12345680']*4],
                views={0:{'offset':-1,'show_last':3},1:{'offset':1},2:{'show_last':3}})
            add(v,'hline-window-rejected','a=hline(1)\nb=hline(2)\nfill(a,b,#12345680,show_last=3)',
                code='ARGUMENT_TYPE')
        if v==6:
            add(v,'offset-limit','plot(close,offset=10001)',code='COLOR_RANGE',category='RUNTIME_ERROR')
            add(v,'plot-appearance','n=input(3)\n'
                'a=plot(close,style=plot.style_area,offset=-1,show_last=n,histbase=25,trackprice=true,linestyle=plot.linestyle_dotted)\n'
                'b=plot(close+2,style=plot.style_cross,offset=1,join=true)\n'
                'fill(a,b,#12345680,show_last=n,fillgaps=true)',
                [[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4],['float']*3,
                ['plot','plot','fill'],{2:[0,1]},[['#2196F3FF']*4]*2+[['#12345680']*4],
                views={0:dict(offset=-1,show_last=3,style='area',histbase=25,join=False,trackprice=True,linestyle='dotted'),
                       1:dict(offset=1,style='cross',histbase=0.0,join=True,trackprice=False,linestyle='solid'),
                       2:dict(show_last=3)})
        body=routing(v).split('\n',2)[2].rstrip()
        add(v,'routing',body,[[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4,[10]*4,[30]*4,[None]*4],['float','float','float','int','int','float'],
            ['plot','plot','fill','hline','hline','fill'],{2:[0,1],5:[3,4]},[['#2196F3FF']*4,['#2196F3FF']*4,['#12345680']*4,['#2196F3FF']*4,['#2196F3FF']*4,['#12345680']*4],['series','series','series','const','const','series'])
        add(v,'nz','color c=na\nplot(close,"",nz(c))',[[10.,20.,30.,40.]],['float'],colors=[['#00000000']*4])
        add(v,'mixed','a=plot(close)\nx=hline(10)\nfill(a,x,#123456)',code='NO_MATCHING_OVERLOAD')
        add(v,'dynamic-hline','x=hline(close)',code='ARGUMENT_QUALIFIER')
        add(v,'mutable','var a=plot(close)\nplot(1)',code='VISUAL_REFERENCE_SCOPE',category='PROFILE_UNCOVERED')
        if v<4:add(v,'color-input-unavailable','c=input(#123456)\nplot(1)',code='COLOR_INPUT',category='PROFILE_UNCOVERED')
        else:
            setting='c=input(#123456,type=input.color)' if v==4 else 'c=input.color(#123456)'
            add(v,'color-override',setting+'\nplot(close,"",c)',[[10.,20.,30.,40.]],['float'],colors=[['#ABCDEF80']*4],inputs={'c':'#abcdef80'})
        if v>=4:add(v,'gaps','a=plot(close)\nb=plot(close+2)\nfill(a,b,#12345680,fillgaps=true)',[[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4],['float']*3,['plot','plot','fill'],{2:[0,1]},[['#2196F3FF']*4,['#2196F3FF']*4,['#12345680']*4])
        if v>=4:add(v,'input-gaps-rejected','g=input(false)\na=plot(close)\nb=plot(close+2)\nfill(a,b,#12345680,fillgaps=g)',code='NO_MATCHING_OVERLOAD')
        if v < 5:
            add(v, 'overlay-version', 'a=plot(close, force_overlay=true)', code='VISUAL_OVERLAY_VERSION')
        else:
            add(v, 'overlay', 'a=plot(close, force_overlay=true)\nb=plot(close+2, force_overlay=false)\nfill(a,b,#12345680)\nplot(close+3, force_overlay=true)',
                [[10.,20.,30.,40.],[12.,22.,32.,42.],[None]*4,[13.,23.,33.,43.]], ['float']*4,
                ['plot','plot','fill',None], {2:[0,1]}, [['#2196F3FF']*4,['#2196F3FF']*4,['#12345680']*4,['#2196F3FF']*4], forced=(0,3))
            add(v, 'overlay-input', 'flag=input.bool(true)\nplot(close, force_overlay=flag)', code='ARGUMENT_QUALIFIER')
        if v==3:add(v,'input-hline-color-rejected','b=input(true)\nc=b ? #123456 : #FFFFFF\nh=hline(1,"",c)',code='ARGUMENT_QUALIFIER')
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical(dict(schema_version='openpine.conformance.cases.1',implementation_profile_id='openpine.historical-bars.1',
        oracle=dict(kind='independent_manual_expectations',basis='Manual finite scalar/color expectations and source-position references; no runtime oracle, no TradingView capture.',external_pine_execution=False),rule_refs=[],assertion_refs=[],cases=cases))+b'\n'
    return files

def _lines(text):
    offset=0
    for line in text.splitlines(True):
        value=line.rstrip('\n');start=offset+(value.index('=')+1 if value.startswith(('a=','b=','x=','y=')) else 0)
        yield start,value.split('=',1)[1] if value.startswith(('a=','b=','x=','y=')) else value
        offset+=len(line)

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/visual-bands/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Visual bands fixtures verified.')
if __name__=='__main__':main()
