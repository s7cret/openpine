"""Owned v1-v6 signal programs and manual finite expectations."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def source(v,body):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Signals","",true)\n'+body+'\n'

def routing(v):
    setting='c=#12345680' if v<4 else 'c=input(#12345680,type=input.color)' if v==4 else 'c=input.color(#12345680)'
    legacy=',0' if v<=4 else ''
    size=',size=size.small' if v>=3 else ''
    return source(v,setting+'\ntc=close>20 ? #FFFFFFFF : #FFFF0080\nplot(close)\nbgcolor(close>20 ? c : na)\nbarcolor(close>20 ? c : na)\n'+
        f'plotshape(close>20,"Entry",shape.triangleup,location.belowbar,c{legacy},1,"BUY",tc{size})\n'+
        f'plotchar(close,"Price","*",location.absolute,c{legacy},-1,"",tc{size})')

def corpus_sources():return {f'signals-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}

def corpus_cases(record):
    return [dict(id=f'signals-routing-v{v}',version=v,family='signals-routing',
        files={'source':record(f'examples/corpus/signals-routing-v{v}.pine'),'bars':record('conformance/visual-signals/bars.json')},inputs={},
        expected={'status':'compiled'},oracle={'reason':'Manual closes10/20/30/40; bool signal only bars2/3; colors and offsets do not rewrite source indices.',
            'outputs':[[10.,20.,30.,40.],[None]*4,[None]*4,[None,None,1.,1.],[10.,20.,30.,40.]]},execution={},
        history_replay={'equivalent':True,'reason':'Confirmed primary OHLC observations; no context visibility differences.'}) for v in range(1,7)]

def fixtures():
    cases=[];files={}
    def plot(values):return dict(title='',type='float',values=values)
    def signal(kind,values,color,*,title='',style=None,location='absolute',size='auto',offset=0,text='',char='',textcolor=None):
        return dict(title=title,type='float',values=values,kind=kind,location=location,size=size,offset=offset,text=text,char=char,
            textcolors=textcolor or [None]*4,colors=color,linewidth=1,style=style or kind)
    def add(v,name,body,outputs=None,*,code=None,category='LANGUAGE_INVALID'):
        filename=f'v{v}-{name}.pine';files[filename]=source(v,body).encode()
        expected=dict(status='rejected',category=category,code=code) if code else dict(status='executed',title='Signals',shorttitle='',overlay=True,
            outputs=outputs,observation_count=4,qualifiers=['series']*len(outputs))
        cases.append(dict(case_id=f'v{v}-{name}',language_version=v,source=filename,bars='bars.json',expected=expected))
    for v in range(1,7):
        c=['#12345680']*4;tc=['#FFFF0080']*2+['#FFFFFFFF']*2;sz='small' if v>=3 else 'auto'
        add(v,'routing',routing(v).split('\n',2)[2].rstrip(),[plot([10.,20.,30.,40.]),
            signal('bgcolor',[None]*4,[None,None,*c[:2]]),signal('barcolor',[None]*4,[None,None,*c[:2]]),
            signal('plotshape',[None,None,1.,1.],c,title='Entry',style='triangleup',location='belowbar',size=sz,offset=1,text='BUY',textcolor=tc),
            signal('plotchar',[10.,20.,30.,40.],c,title='Price',location='absolute',size=sz,offset=-1,char='*',textcolor=tc)])
        add(v,'defaults','bgcolor(#123456)\nbarcolor(#123456)\nplotshape(close>20)\nplotchar(close>20)',[
            signal('bgcolor',[None]*4,['#1234561A' if v<6 else '#123456FF']*4),signal('barcolor',[None]*4,['#123456FF']*4),
            signal('plotshape',[None,None,1.,1.],['#2196F3FF']*4,style='xcross',location='abovebar',textcolor=['#2196F3FF']*4),
            signal('plotchar',[None,None,1.,1.],['#2196F3FF']*4,location='abovebar',char='★',textcolor=['#2196F3FF']*4)])
        legacy=',0' if v<=4 else ''
        add(v,'zero-absolute',f'plotshape(0,"",shape.circle,location.absolute,#12345680{legacy},0,"",#FFFFFF)',[
            signal('plotshape',[0.]*4,c,style='circle',location='absolute',textcolor=['#FFFFFFFF']*4)])
        add(v,'nested','if close>20\n    bgcolor(#123456)\nplot(close)',code='GLOBAL_ONLY_CALL')
        add(v,'dynamic-offset',f'barcolor(#123456,bar_index)',code='ARGUMENT_QUALIFIER')
        add(v,'void-assignment','p=plotshape(close>20)\nplot(close)',code='NESTED_EFFECT',category='PROFILE_UNCOVERED')
        if v>=3:
            add(v,'dynamic-text','plotshape(close>20,text=close>20 ? "A" : "B")',code='ARGUMENT_QUALIFIER')
            add(v,'input-size','s=input("small")\nplotshape(close>20,size=s)',code='ARGUMENT_QUALIFIER')
            add(v,'bad-shape','plotshape(close>20,style="unknown")',code='VISUAL_SIGNAL',category='RUNTIME_ERROR')
            add(v,'bad-offset','barcolor(#123456,offset=10001)',code='VISUAL_SIGNAL',category='RUNTIME_ERROR')
        if v==6:add(v,'transp-unavailable','bgcolor(#123456,transp=50)',code='UNKNOWN_ARGUMENT')
        if v<3:add(v,'size-unavailable','plotshape(close>20,size=size.small)',code='UNDEFINED_VARIABLE')
    files['bars.json']=canonical(dict(schema_version='openpine.historical-bars.2',context_id='signals.test',symbol='TEST:SIGNALS',timeframe='1',
        bars=[dict(time=i*60000,open=c-1,high=c+2,low=c-2,close=c,volume=1.) for i,c in enumerate((10.,20.,30.,40.))]))+b'\n'
    files['cases.json']=canonical(dict(schema_version='openpine.conformance.cases.1',implementation_profile_id='openpine.historical-bars.1',
        oracle=dict(kind='independent_manual_expectations',basis='Manual OHLC and bool/zero/color/default expectations; no runtime-derived oracle.',external_pine_execution=False),
        rule_refs=[],assertion_refs=[],cases=cases))+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/visual-signals/'+n:b for n,b in fixtures().items()};files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Visual signal fixtures verified.')
if __name__=='__main__':main()
