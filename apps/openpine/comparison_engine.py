"""Resumable streaming comparison; it never executes Pine or interprets its IR."""
from itertools import zip_longest,islice
from pathlib import Path
from packages.storage.comparison_readers import rows,events
from packages.storage.comparison_ids import IdIndex
from apps.openpine.compare_values import difference,chained,small
from packages.contracts.wire import digest

MAX_ITEMS=50000000
def compare(manager,key,sides,state,save,cancelled):
    missing={'comparison_missing':True};programs=[s['program_sha256'] for s in sides]
    index=None
    try:
        if programs[0]!=programs[1]:
            from packages.storage.comparison_scratch import directory
            index=IdIndex(directory(manager.directory,key)/'ids.sqlite3',programs,sites=[s['sites'] for s in sides])
            if state['cursor']['channel'] in ('events','statistics','done'):
                # Rebuild scratch indexes after restart; committed progress remains authoritative.
                for side in (0,1):
                    for event in islice(events(manager.objects,sides[side]),state['checked']['events']):
                        if cancelled():raise InterruptedError()
                        index.normalize(side,event)
        channels=('rows','events','statistics')
        start_channel=state['cursor']['channel']
        if start_channel=='done':
            state['status']='equal' if state['compatibility']['status']=='comparable' else 'data_equal'
            return state
        for channel in channels[channels.index(start_channel):]:
            start=state['cursor']['index'] if state['cursor']['channel']==channel else 0
            state['cursor']={'channel':channel,'index':start}
            if channel=='statistics':
                readers=[iter([(s['publication'].get('simulation') or {}).get('statistics')]) if start==0 else iter(()) for s in sides]
            else:readers=[(rows if channel=='rows' else events)(manager.objects,s,start) for s in sides]
            try:
                for a,b in zip_longest(*readers,fillvalue=missing):
                    if cancelled():raise InterruptedError()
                    if sum(state['checked'].values())>=MAX_ITEMS:raise OverflowError('Comparison reached its fifty-million-item limit.')
                    raw=(a,b)
                    if index is not None and channel!='rows':
                        a,b=[index.normalize(side,v) if v is not missing else v for side,v in enumerate(raw)]
                    found=difference(a,b)
                    for name,value in (('left',a),('right',b)):state['chains'][name]=chained(state['chains'][name],value)
                    position=state['cursor']['index'];state['cursor']['index']+=1;state['checked'][channel]+=1
                    if found:
                        exemplar=next((v for v in raw if type(v) is dict and v is not missing),{})
                        state.update(status='different',first_difference={'channel':channel,'index':position,**found,
                            'bar':exemplar.get('bar',exemplar.get('bar_index')),'time':exemplar.get('time'),
                            'primary_contexts':[side.get('primary_context') for side in sides],
                            'left_raw':small(raw[0]),'right_raw':small(raw[1]),'output':exemplar.get('output')})
                        return state
                    if state['cursor']['index']%512==0:save(state)
            finally:
                for reader in readers:
                    if hasattr(reader,'close'):reader.close()
            ordinal=channels.index(channel)+1
            state['cursor']={'channel':channels[ordinal] if ordinal<len(channels) else 'done','index':0};save(state)
        state['cursor']={'channel':'done','index':0};state['status']='equal' if state['compatibility']['status']=='comparable' else 'data_equal'
        return state
    finally:
        if index:index.close()
