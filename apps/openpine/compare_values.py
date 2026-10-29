"""Exact scalar/structural comparison, reusing the corpus type encoding."""
from apps.openpine.corpus_compare import tagged
from packages.contracts.wire import digest,canonical

def small(value):
    raw=canonical(value)
    return value if len(raw)<=4096 else {'omitted':True,'bytes':len(raw),'sha256':digest(tagged(value))}

def difference(left,right):
    if tagged(left)==tagged(right):return None
    path=[];a,b=left,right
    while True:
        if type(a) is dict and type(b) is dict and a.keys()==b.keys():
            key=next(k for k in sorted(a) if tagged(a[k])!=tagged(b[k]))
        elif type(a) is list and type(b) is list:
            key=next((i for i in range(min(len(a),len(b))) if tagged(a[i])!=tagged(b[i])),None)
            if key is None:break
        else:break
        path.append(key);a,b=a[key],b[key]
    return {'path':path,'left':small(a),'right':small(b),'left_tagged':small(tagged(a)),'right_tagged':small(tagged(b))}

def chained(previous,value):return digest({'previous':previous,'value':tagged(value)})
