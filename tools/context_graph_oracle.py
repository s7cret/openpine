"""Independent batch formulas for the small private-context examples."""
from fractions import Fraction
from tools.specification import ROOT,read_json

def weighted(bars,length):
    values=[float((b['high']+b['low']+b['close'])/3) for b in bars]
    result=[]
    for index in range(len(values)):
        window=values[max(0,index-length+1):index+1]
        result.append(None if len(window)<length else float(sum((Fraction.from_float(x)*w for w,x in enumerate(window,1)),Fraction())/sum(range(1,length+1))))
    return result

def expected_outputs(version,length=4):
    root=ROOT/'examples/context-graph'
    primary=read_json(root/'bars.json')['bars'];higher=read_json(root/'higher.json')
    bars=higher.get('bars',higher.get('data',{}).get('bars'))
    if bars is None:
        # Uploaded envelopes retain their own raw, canonical bar payload.
        raise ValueError('Context graph higher input has no bars')
    main=weighted(primary,length);avg=weighted(bars,length)
    previous=[None,*avg[:-1]]
    event=[i>0 and x is not None and avg[i-1] is not None and x>avg[i-1] for i,x in enumerate(avg)]
    remembered=[];last=0.
    for x in avg:
        if x is not None and x>0:last=x
        remembered.append(last)
    indices=[i//4 if version<3 else (i+1)//4-1 for i in range(len(primary))]
    return [main,[None if j<0 else previous[j] for j in indices],
        [0 if j<0 else int(event[j]) for j in indices],[None if j<0 else remembered[j] for j in indices]]

def same(actual,expected):
    import math
    return len(actual)==len(expected) and all(a is None if b is None else a is not None and math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-12) for a,b in zip(actual,expected))
