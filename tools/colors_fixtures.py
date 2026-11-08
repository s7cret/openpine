"""Owned color programs and manual finite RGBA expectations, without execution."""
import argparse
from tools.specification import ROOT
from packages.contracts.wire import canonical

def routing(v):
    color='red' if v<4 else 'color.red'
    return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Colors")\n'+f'''shade(x)=>x>20 ? {color} : #00FF00
var total=0.
total:=total+close
c=shade(close)
plot(total,"Total",c,3)
plot(bar_index>0 and c[1]==#00FF00 ? 1 : 0)
'''

def corpus_sources():return {f'colors-routing-v{v}.pine':routing(v).encode() for v in range(1,7)}

def corpus_cases(record):
    return [dict(id=f'colors-routing-v{v}',version=v,family='colors-routing',
        files={'source':record(f'examples/corpus/colors-routing-v{v}.pine'),'bars':record('conformance/functions/fixed.json')},
        inputs={},expected={'status':'compiled'},oracle={'reason':'Owned cumulative closes10/20/30/40 and prior-bar green flag; RGBA presentation is independently checked by the color conformance and installed HTTP range checks.',
            'outputs':[[10.,30.,60.,100.],[0,1,1,0]]},execution={},
        history_replay={'equivalent':True,'reason':'Primary close-only indicator; matching confirmed observations.'}) for v in range(1,7)]

def fixtures():
    files={};cases=[]
    def add(v,name,body,values=None,types=None,presentations=None,code=None,category='LANGUAGE_INVALID',complete=False):
        path=f'v{v}-{name}.pine';files[path]=(body if complete else f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Colors")\n'+body+'\n').encode()
        outputs=[{'title':'','type':t,'values':row} for t,row in zip(types or ['int']*len(values or []),values or [],strict=True)]
        if presentations:
            for i,p in presentations.items():outputs[i].update(p)
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'Colors','shorttitle':'','overlay':False,'outputs':outputs,'observation_count':4,'qualifiers':(['const']*len(outputs) if name in ('missing','channels') else ['series']*len(outputs))}
        cases.append({'case_id':f'v{v}-{name}','language_version':v,'source':path,'bars':'bars.json','expected':expected})
    for v in range(1,7):
        red='red' if v<4 else 'color.red';redhex='#F23645FF' if v==6 else '#FF5252FF'
        paint=dict(colors=['#12345680']*4,linewidth=2,style='areabr')
        options=dict(histbase=25.5,join=False,trackprice=True,linestyle='solid')
        if v<3:
            add(v,'plot-archive-appearance',
                'plot(close,"",#12345680,2,areabr,true,0,25.5,-1,false)\n'
                'plot(close,"",#12345680,areabr,2,true,0,25.5,1,false)',
                [[10.,20.,30.,40.]]*2,['float']*2,
                {0:dict(paint,**options,offset=-1),1:dict(paint,**options,offset=1)})
            add(v,'plot-archive-bad-track','plot(close,"",#12345680,2,areabr,na,0)',code='ARGUMENT_TYPE')
        else:
            area='areabr' if v<4 else 'plot.style_areabr'
            add(v,'plot-appearance',
                f'plot(close>20 ? close : na,"",#12345680,2,{area},histbase=25.5,trackprice=true,offset=-1,show_last=3)',
                [[None,None,30.,40.]],['float'],{0:dict(paint,**options,offset=-1,show_last=3)})
            add(v,'plot-series-base','plot(close,histbase=close)',code='ARGUMENT_QUALIFIER')
        if v<6:
            add(v,'plot-linestyle-version','plot(close,linestyle="dashed")',code='PLOT_LINESTYLE_VERSION')
        else:
            add(v,'plot-linestyle',
                'toggle=input.bool(false)\nls=toggle ? plot.linestyle_solid : plot.linestyle_dashed\n'
                'plot(close,linestyle=ls,force_overlay=true)',
                [[10.,20.,30.,40.]],['float'],{0:dict(colors=['#2196F3FF']*4,linewidth=1,
                    style='line',offset=0,histbase=0.0,join=False,trackprice=False,linestyle='dashed',force_overlay=True)})
            add(v,'plot-linestyle-type','plot(close,linestyle=plot.style_line)',code='ARGUMENT_TYPE')
        if v>=5:
            add(v,'plot-step-styles','plot(close,style=plot.style_steplinebr)\nplot(close,style=plot.style_stepline_diamond)',
                [[10.,20.,30.,40.]]*2,['float']*2,
                {0:dict(colors=['#2196F3FF']*4,linewidth=1,style='steplinebr'),
                 1:dict(colors=['#2196F3FF']*4,linewidth=1,style='stepline_diamond')})
        add(v,'routing',routing(v),[[10.,30.,60.,100.],[0,1,1,0]],['float','int'],
            {0:dict(title='Total',colors=['#00FF00FF']*2+[redhex]*2,linewidth=3,style='line')},complete=True)
        add(v,'dynamic',f'c=close>20 ? {red} : #00FF00\nplot(close,"",c,2)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#00FF00FF']*2+[redhex]*2,linewidth=2,style='line')})
        add(v,'missing','color c=na\nplot(na(c) ? 1 : 0)',[[1]*4])
        add(v,'hidden','color c=na\nplot(close,"",c)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=[None]*4,linewidth=1,style='line')})
        add(v,'literal','c=#abcdef80\nplot(close,"",c)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#ABCDEF80']*4,linewidth=1,style='line')})
        transparent=('plot(close,"",#123456,1,line,90)' if v<3 else
            'plot(close,color=#123456,transp=90)' if v<6 else 'plot(close,color=color.new(#123456,90))')
        add(v,'transparency90',transparent,[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#1234561A']*4,linewidth=1,style='line')})
        add(v,'tuple','f(x)=>[x,close]\n[c,n]=f(#123456)\nplot(n,"",c)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#123456FF']*4,linewidth=1,style='line')})
        add(v,'bad-hex','c=#12345\nplot(1)',code='COLOR_LITERAL')
        add(v,'bad-string','color c="#123456FF"\nplot(1)',code='ASSIGNMENT_TYPE')
        add(v,'bad-numeric',f'plot({red})',code='ARGUMENT_TYPE')
        add(v,'bad-width',f'plot(close,"",{red},bar_index+1)',code='ARGUMENT_QUALIFIER')
        if v>=2:
            add(v,'loop','c=for i=0 to 2\n    i==2 ? #FFFFFF : #000000\nplot(close,"",c)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#FFFFFFFF']*4,linewidth=1,style='line')})
        if v in (2,3):
            add(v,'legacy','plot(close,"",color(red,100))',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#FF525200']*4,linewidth=1,style='line')})
        if v>=4:
            add(v,'channels','c=color.rgb(10,20,30,50)\nplot(color.r(c))\nplot(color.g(c))\nplot(color.b(c))',[[10.]*4,[20.]*4,[30.]*4],['float']*3)
            add(v,'gradient','c=color.from_gradient(close,10,30,#000000,#FFFFFF)\nplot(close,color=c)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#000000FF','#808080FF','#FFFFFFFF','#FFFFFFFF'],linewidth=1,style='line')})
            add(v,'mixed-gradient','c=color.from_gradient(1152921504606847006,1152921504606846976.0,1152921504606847036,#000000,#FFFFFF)\nplot(close,color=c)',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#808080FF']*4,linewidth=1,style='line')})
            add(v,'array','a=array.new_color(1,#123456)\nplot(close,color=array.get(a,0))',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#123456FF']*4,linewidth=1,style='line')})
            add(v,'range','plot(color.r(color.rgb(256,0,0)))',code='COLOR_RANGE',category='RUNTIME_ERROR')
        if v>=5:
            add(v,'udt-map-matrix','type N\n    color c=#123456\nn=N.new()\nm=map.new<color,color>()\nm.put(n.c,#ABCDEF)\ng=matrix.new<color>(1,1,m.get(n.c))\nplot(close,color=g.get(0,0))',[[10.,20.,30.,40.]],['float'],{0:dict(colors=['#ABCDEFFF']*4,linewidth=1,style='line')})
            add(v,'empty-loop','color c=while false\n    #123456\nplot(na(c) ? 1 : 0)',[[1]*4])
    files['bars.json']=(ROOT/'conformance/functions/fixed.json').read_bytes()
    files['cases.json']=canonical({'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1',
        'oracle':{'kind':'independent_manual_expectations','basis':'Closes10/20/30/40 and manually specified RGBA bytes; not generated by a color kernel or captured from TradingView.','external_pine_execution':False},'rule_refs':[],'assertion_refs':[],'cases':cases})+b'\n'
    return files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    files={'conformance/colors/'+n:b for n,b in fixtures().items()}
    files.update({'examples/corpus/'+n:b for n,b in corpus_sources().items()})
    for name,raw in files.items():
        path=ROOT/name
        if a.check:assert path.read_bytes()==raw,name
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Owned color fixtures verified.')
if __name__=='__main__':main()
