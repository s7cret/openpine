"""Bounded chart view of a pinned publication, including old replay prefixes."""
from itertools import islice
from apps.openpine.comparison_sources import read
from packages.storage.comparison_readers import rows

MAX_ROWS=5000

def view(service,key,side=0,start=0,limit=100):
    if type(side) is not int or side not in (0,1) or type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=500:raise ValueError('Invalid pinned chart range.')
    source=read(service.manager,service.repository.get(key)['sources'][side]);width=len(source['outputs']);start=min(start,source['bars'])
    end=min(source['bars'],start+min(limit,max(1,MAX_ROWS//max(1,width))))
    reader=rows(service.manager.objects,source,start*width)
    try:values=list(islice(reader,min(MAX_ROWS,(end-start)*width)))
    finally:reader.close()
    return {'comparison_id':key,'side':side,'start':start,'next':end,'total':source['bars'],'rows':values,'outputs':source['outputs'],
        'columns_limited':width>MAX_ROWS,'pinned':True}
