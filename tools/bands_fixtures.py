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
    def add(v,name,body,values=None,types=None,kinds=None,refs=None,colors=None,qualifiers=None,code=None,category='LANGUAGE_INVALID',inputs=None):
        text=source(v,body);filename=f'v{v}-{name}.pine';files[filename]=text.encode()
        outputs=[]
        # Source positions are derived without compiling or invoking a kernel.
        sites=['call:'+str(i)+':'+str(i+len(line)) for i,line in _lines(text) if line.startswith(('plot(','hline(','fill('))]
        for i,(row,typ) in enumerate(zip(values or [],types or [],strict=True)):
            out={'title':'','type':typ,'values':row}
            if kinds and kinds[i]:out.update(kind=kinds[i],references=[sites[j] for j in (refs or {}).get(i,[])],fillgaps=kinds[i]=='fill' and name=='gaps',colors=colors[i],linewidth=1,style='fill' if kinds[i]=='fill' else 'solid' if kinds[i]=='hline' else 'line')
            elif colors:out.update(colors=colors[i],linewidth=1,style='line')
            outputs.append(out)
        expected={'status':'rejected','category':category,'code':code} if code else dict(status='executed',title='Bands',shorttitle='',overlay=False,outputs=outputs,observation_count=4,qualifiers=qualifiers or ['series']*len(outputs))
        case=dict(case_id=f'v{v}-{name}',language_version=v,source=filename,bars='bars.json',expected=expected)
        if inputs is not None:case['inputs']=inputs
        cases.append(case)
    for v in range(1,7):
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
