"""Independent list/Fraction oracles and deterministic source fixtures."""
import argparse
from fractions import Fraction
from tools.specification import ROOT,encode,read_json
KINDS=('barssince','valuewhen','cum','sum','wma','vwma','mom','roc','rising','falling','highestbars','lowestbars')
VALUES=[None,1.,4.,4.,None,2.,8.,0.,3.]
VOLUMES=[1.,0.,2.,1.,3.,0.,2.,0.,1.]


def oracle(kind,values,volumes=None,length=2,occurrence=1,version=6,conditions=None):
    output=[];seen=[];events=[];elapsed=None;total=None
    for i,x in enumerate(values):
        v=volumes[i] if volumes else 1.
        if kind=='barssince':
            if x is not None and x>3:elapsed=0
            elif elapsed is not None:elapsed+=1
            y=elapsed
        elif kind=='valuewhen':
            if conditions[i] if conditions is not None else i%2==0:events.append(x)
            y=events[-1-occurrence] if len(events)>occurrence else None
        elif kind=='cum':
            if x is not None:total=(total or Fraction(0))+Fraction(x)
            y=None if total is None else float(total)
        elif kind in ('sum','wma','vwma'):
            if x is not None and (kind!='vwma' or v is not None):seen.append((Fraction(x),Fraction(v)) if kind=='vwma' else Fraction(x))
            window=seen[-length:];y=None
            if len(window)==length:
                if kind=='sum':y=float(sum(window))
                elif kind=='wma':y=float(sum(a*(j+1) for j,a in enumerate(window))/sum(range(1,length+1)))
                else:
                    den=sum(b for a,b in window);y=float(sum(a*b for a,b in window)/den) if den else None
        elif kind in ('mom','roc'):
            old=values[i-length] if i>=length else None
            y=None if x is None or old is None or kind=='roc' and old==0 else float(Fraction(x)-Fraction(old)) if kind=='mom' else float((Fraction(x)-Fraction(old))*100/Fraction(old))
        elif kind in ('rising','falling'):
            y=(x>max(seen[-length:]) if kind=='rising' else x<min(seen[-length:])) if x is not None and len(seen)>=length else False if version==6 else None
            if x is not None:seen.append(x)
        else:
            candidates=[(a,j) for j,a in enumerate(values[max(0,i-length+1):i+1],max(0,i-length+1)) if a is not None]
            if not candidates:y=None
            else:
                extreme=(max if kind=='highestbars' else min)(a for a,j in candidates)
                y=max(j for a,j in candidates if a==extreme)-i
        output.append(y)
    return output


def source(v,body,title='signals'):
    return f'//@version={v}\n'+('study' if v<5 else 'indicator')+f'("{title}")\n'+body+'\n'


def prepare():
    files={};cases=[]
    def put(name,value):files[name]=value.encode() if isinstance(value,str) else encode(value)
    bars={'schema_version':'openpine.historical-bars.2','context_id':'signals','symbol':'TEST:SIGNAL','timeframe':'1',
        'bars':[{'time':i*60000,'open':x,'high':x+1 if x is not None else None,'low':x-1 if x is not None else None,'close':x,'volume':v} for i,(x,v) in enumerate(zip(VALUES,VOLUMES))]}
    put('conformance/signals/bars.json',bars)
    def case(v,name,body,values=None,kind='float',code=None,category='LANGUAGE_INVALID',contexts=None):
        name=f'v{v}-{name}';put(f'conformance/signals/{name}.pine',source(v,body))
        expected={'status':'rejected','category':category,'code':code} if code else {'status':'executed','title':'signals','shorttitle':'','overlay':False,'observation_count':len(VALUES),'qualifiers':['series'],'outputs':[{'title':'','type':kind,'values':values}]}
        row={'case_id':name,'language_version':v,'source':name+'.pine','bars':'bars.json','expected':expected}
        if contexts:row['context_inputs']=contexts
        cases.append(row)
    for v in range(1,7):
        p='' if v<5 else 'ta.'
        for name in KINDS:
            call=(p+name if name!='sum' or v<5 else 'math.sum')
            args='close>3' if name=='barssince' else 'bar_index%2==0,close,1' if name=='valuewhen' else 'close' if name=='cum' else 'close,2'
            values=oracle(name,VALUES,VOLUMES,version=v);kind='int' if name in ('barssince','highestbars','lowestbars') else 'float'
            if name in ('rising','falling'):args+=') ? 1:0';body=f'plot({call}({args})';values=[int(bool(x)) for x in values];kind='int'
            else:body=f'plot({call}({args}))'
            case(v,name,body,values,kind)
        case(v,'integer-events',f'plot({p}valuewhen(bar_index%2==0,bar_index,1))',oracle('valuewhen',list(range(9))),'int')
        case(v,'bool-events',f'plot({p}valuewhen(bar_index%2==0,close>3,1) ? 1:0)',[int(bool(x)) for x in oracle('valuewhen',[x is not None and x>3 for x in VALUES])],'int')
        for name,shift in (('highestbars',1),('lowestbars',-1)):
            case(v,name+'-default',f'plot({p}{name}(2))',oracle(name,[x+shift if x is not None else None for x in VALUES]),'int')
        case(v,'invalid-length',f'plot({p}wma(close,0))',code='SIGNAL_PARAMETER',category='RUNTIME_ERROR')
        case(v,'invalid-occurrence',f'plot({p}valuewhen(true,close,-1))',code='SIGNAL_PARAMETER',category='RUNTIME_ERROR')
        # Dynamic-window admission preserves this source/case ID. The initial
        # missing source keeps the defined-sample count below bar_index+1.
        case(v,'series-length',f'plot({p}wma(close,bar_index+1))',[None]*len(VALUES))
        case(v,'string-event',f'plot({p}valuewhen(true,"text",0))',code='ARGUMENT_TYPE')
        if v<3:case(v,'legacy-named',f'plot({p}wma(source=close,length=2))',code='NAMED_ARGUMENT_UNVERIFIED',category='PROFILE_UNCOVERED')
        request='security' if v<5 else 'request.security'
        vals=[None]*3+[4.]*3+[2.]*3 if v<3 else [None]*5+[4.]*3+[2.]
        case(v,'requested-event',f'plot({request}("TEST:SIGNAL","3",{p}valuewhen(close>open,close,0)))',vals,contexts='contexts.json')
    context=lambda cid,tf:{'context_id':cid,'symbol':'TEST:SIGNAL','timeframe':tf,'source':'uploaded-file.1','timezone':'UTC','session':'24x7','time_rule':'fixed-duration-utc.1'}
    higher={**bars,'context_id':'higher','timeframe':'3','bars':[{'time':i*180000,'open':3.,'high':5.,'low':0.,'close':x,'volume':1.} for i,x in enumerate((1.,4.,2.))]}
    # The last requested close remains 4 because its final condition is false.
    for row in cases:
        if row['case_id'].endswith('requested-event'):row['expected']['outputs'][0]['values']=[None]*3+[4.]*6 if row['language_version']<3 else [None]*5+[4.]*4
    put('conformance/signals/higher.json',higher)
    put('conformance/signals/contexts.json',{'schema_version':'openpine.context-inputs.1','primary_context':context('signals','1'),'contexts':[{'context':context('higher','3'),'bars':'higher.json'}]})
    put('conformance/signals/cases.json',{'schema_version':'openpine.conformance.cases.1','implementation_profile_id':'openpine.historical-bars.1','oracle':{'kind':'independent_manual_expectations','external_pine_execution':False,'basis':'Independent Fraction/list arithmetic and explicitly stated event/NA policies; no runtime-generated expectations.'},'assertion_refs':[],'rule_refs':[],'cases':cases})
    example=read_json(ROOT/'examples/environment/bars.json');example.update(context_id='signals',symbol='TEST:SIGNAL')
    high=read_json(ROOT/'examples/environment/higher.json');high.update(context_id='higher',symbol='TEST:SIGNAL')
    put('examples/signals/bars.json',example);put('examples/signals/higher.json',high)
    put('examples/signals/input.json',{'schema_version':'openpine.market-input.1','data':example,'environment':{'schema_version':'openpine.instrument-environment.1','instrument':{'mintick':'0.25','pointvalue':'1','currency':'USD','basecurrency':'SIGNAL','type':'custom'}}})
    put('examples/signals/contexts.json',{'schema_version':'openpine.context-inputs.1','primary_context':context('signals','1'),'contexts':[{'context':context('higher','4'),'bars':'higher.json'}]})
    for v in range(1,7):
        p='' if v<5 else 'ta.';inp='input' if v<5 else 'input.int';request='security' if v<5 else 'request.security';sumname='sum' if v<5 else 'math.sum';direction='true' if v==1 else 'strategy.long'
        text=f'''//@version={v}
strategy("Signal and weighted trend",default_qty_type=strategy.fixed,initial_capital=1000)
length={inp}(3,title="Length",minval=1,maxval=100)
average={p}wma(close,length)
weighted={p}vwma(close,length)
up={p}rising(close,length)
momentum={p}mom(close,length)
rate={p}roc(close,length)
event=close>average and up and momentum>0 and rate>0
signalPrice={p}valuewhen(event,close,0)
age={p}barssince(event)
totalVolume={p}cum(volume)
windowVolume={sumname}(volume,length)
peak={p}highestbars(high,length)
trough={p}lowestbars(low,length)
down={p}falling(close,length)
higher={request}("TEST:SIGNAL","4",{p}valuewhen(close>open,close,0))
if strategy.position_size==0 and event and peak==0 and trough<0 and totalVolume>=windowVolume and close>higher
    strategy.entry("L",{direction})
if strategy.position_size>0
    strategy.exit("X","L",stop=strategy.position_avg_price-4*syminfo.mintick,limit=strategy.position_avg_price+6*syminfo.mintick)
if strategy.position_size>0 and (age>length or down)
    strategy.close("L")
plot(average,title="WMA")
plot(weighted,title="VWMA")
plot(signalPrice,title="Signal price")
plot(age,title="Signal age")
plot(strategy.equity,title="Equity")
'''
        put(f'examples/signals/event-trend-v{v}.pine',text)
    for name in ('experiment','search','walkforward'):
        data=read_json(ROOT/f'examples/environment/{name}.json');data['name']='Signal '+name
        key='grid' if name=='experiment' else 'space';data[key]={'length':[2,3]};put(f'examples/signals/{name}.json',data)
    for v in (1,6):
        p='' if v==1 else 'ta.'
        put(f'examples/signals/phase-v{v}.pine',source(v,f'plot({p}cum(volume))\nplot({p}wma(close,2))\nplot({p}valuewhen(close>0,close,0))\nplot({p}barssince(close>0))\nplot({p}highestbars(high,2))','Signal phases'))
    return files


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args()
    for name,raw in prepare().items():
        target=ROOT/name
        if args.check:
            if not target.is_file() or target.read_bytes()!=raw:raise SystemExit('Signal fixture differs: '+name)
        else:target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    print('Independent signal fixtures verified.')


if __name__=='__main__':main()
