"""Deterministic duration-aware shards, with no skipped or repeated modules."""
import hashlib,json,re
from pathlib import Path

MODEL=Path(__file__).with_name('test_timings.json')

def weights():
    value=json.loads(MODEL.read_text(encoding='utf-8'))
    if value['schema_version']!='openpine.test-duration-model.1':raise ValueError('Unknown test duration model.')
    result={**value['modules'],**value['development_modules']};default=value['default_seconds']
    if type(default) is not int or not 1<=default<=21600:raise ValueError('Invalid default test duration.')
    for name,seconds in result.items():
        if not re.fullmatch(r'test_\w+\.py',name) or type(seconds) is not int or not 1<=seconds<=21600:raise ValueError('Invalid measured module duration.')
    return result,default

def partition(modules,count,estimated=None,default=60):
    if type(count) is not int or not 1<=count<=16 or len(modules)!=len(set(modules)):raise ValueError('Invalid shard inventory.')
    if any(type(n) is not str or re.fullmatch(r'test_\w+\.py',n) is None for n in modules):raise ValueError('Invalid test module name.')
    if estimated is None:estimated,default=weights()
    if type(default) is not int or default<=0 or any(type(v) is not int or v<=0 for v in estimated.values()):raise ValueError('Invalid shard durations.')
    if count==1:return [list(modules)]
    buckets=[[] for _ in range(count)];loads=[0]*count;order={n:i for i,n in enumerate(modules)}
    for name in sorted(modules,key=lambda n:(-estimated.get(n,default),order[n])):
        target=min(range(count),key=lambda i:(loads[i],i));buckets[target].append(name);loads[target]+=estimated.get(name,default)
    return [sorted(row,key=order.__getitem__) for row in buckets]

def identity():return hashlib.sha256(MODEL.read_bytes()).hexdigest()
