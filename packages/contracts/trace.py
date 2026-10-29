"""Closed diagnostic capture policy; it never changes Pine execution settings."""
import re

SCHEMA='openpine.trace.1'
def options(value):
    defaults={'start_bar':0,'end_bar':1,'lines':[],'sites':[],'max_events':10000,'max_bytes':8*1024*1024,'array_preview':8}
    if type(value) is not dict or value.keys()-defaults.keys():raise ValueError('Unknown trace option.')
    result={**defaults,**value}
    for key,low,high in (('start_bar',0,10000000),('end_bar',1,10000000),('max_events',1,100000),('max_bytes',1024,32*1024*1024),('array_preview',0,16)):
        if type(result[key]) is not int or not low<=result[key]<=high:raise ValueError('Invalid trace '+key)
    if not result['start_bar']<result['end_bar']<=result['start_bar']+1000:raise ValueError('Select between 1 and 1000 primary bars.')
    for key in ('lines','sites'):
        rows=result[key]
        if type(rows) is not list or len(rows)>128 or len(set(map(str,rows)))!=len(rows):raise ValueError('Invalid trace selection.')
        if key=='lines' and any(type(x) is not int or not 1<=x<=100000 for x in rows):raise ValueError('Invalid trace source line.')
        if key=='sites' and any(type(x) is not str or not re.fullmatch(r'[\w:./@-]{1,160}',x) for x in rows):raise ValueError('Invalid trace site.')
    return result
