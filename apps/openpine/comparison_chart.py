"""Bounded chart view of a pinned publication, including old replay prefixes."""
from itertools import islice
from apps.openpine.comparison_sources import read
from packages.storage.comparison_readers import rows

def view(service,key,side=0,start=0,limit=100):
    if type(side) is not int or side not in (0,1) or type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=500:raise ValueError('Invalid pinned chart range.')
    source=read(service.manager,service.repository.get(key)['sources'][side]);width=len(source['outputs'])
    reader=rows(service.manager.objects,source,start*width)
    try:values=list(islice(reader,limit*width))
    finally:reader.close()
    return {'comparison_id':key,'side':side,'start':start,'total':source['bars'],'rows':values,'outputs':source['outputs'],'pinned':True}
