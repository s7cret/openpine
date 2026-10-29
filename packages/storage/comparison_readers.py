"""Bounded ordered output readers, shared by online and offline comparison controllers."""
import itertools
from .results import series_rows
from .trades import event_rows

def rows(objects,side,start=0):
    publication=side['publication'];outputs=side['outputs'];order={o['output_id']:i for i,o in enumerate(outputs)};width=len(order)
    if width==0:return
    bar=start//width;skip=start%width
    if 'series' not in publication:raise ValueError('This legacy publication has no streamed series.')
    summary={'observation_count':side['bars'],'series':publication['series']}
    cursor=bar
    for index,group in itertools.groupby(series_rows(objects,summary,bar),key=lambda r:r[0]):
        if index!=cursor:raise ValueError('Comparison output has a missing bar.')
        batch=list(itertools.islice(group,width+1))
        if len(batch)!=width or {r[2] for r in batch}!=order.keys():raise ValueError('Comparison output columns differ from their declaration.')
        for row in sorted(batch,key=lambda r:order[r[2]])[skip:]:
            yield {'bar':row[0],'time':row[1],'output':order[row[2]],'value':row[3]}
        skip=0;cursor+=1
    if cursor!=side['bars']:raise ValueError('Comparison output ended before its pinned boundary.')

def events(objects,side,start=0):
    journal=(side['publication'].get('simulation') or {}).get('journal',{'records':0,'parts':[]})
    cursor=start
    for event in event_rows(objects,journal,start):
        if event['sequence']!=cursor:raise ValueError('Comparison journal sequence differs.')
        yield event;cursor+=1
    if cursor!=journal['records']:raise ValueError('Comparison journal ended early.')
