"""Pin publication inputs; a partial final tape block is copied in bounded chunks."""
from .replay_tape import freeze,part_events,verified_chains,SCHEMA
from .traces import put
from packages.contracts.wire import digest

def prefix(repo,key,count):
    row=repo.row(key)
    if type(count) is not int or not 1<=count<=row[3]:raise ValueError('Comparison requires a published replay prefix.')
    saved=repo.objects.get(freeze(repo,key),max_bytes=1024*1024);parts=[];chain=repo.config(key)['identity']
    for part in saved['parts']:
        if part['first']>=count:break
        if part['first']+part['rows']<=count:
            parts.append(part);chain=part['after'];continue
        verified_chains(repo,part);pending=[];first=part['first'];before=chain
        for event in part_events(repo,part,part['first'],count):
            chain=digest({'previous':chain,'event':event.payload()});pending.append(event.payload())
            if len(pending)==1024:
                parts.append({'format':'json','object_id':repo.objects.put(pending),'first':first,'rows':len(pending),'before':before,'after':chain})
                first+=len(pending);pending=[];before=chain
        if pending:parts.append({'format':'json','object_id':repo.objects.put(pending),'first':first,'rows':len(pending),'before':before,'after':chain})
    return repo.objects.put({'schema_version':SCHEMA,'config_ref':row[0],'events':count,'input_chain':chain,'parts':parts})

def clone(repo,ref):
    frozen=repo.objects.get(ref,max_bytes=1024*1024)
    if frozen['schema_version']!=SCHEMA:raise ValueError('Invalid comparison tape.')
    config=repo.objects.get(frozen['config_ref']);key=repo.create(config)
    repo.db.execute('UPDATE replays SET accepted=?,input_chain=? WHERE replay_id=?',(frozen['events'],frozen['input_chain'],key))
    put(repo.db,'trace:replay-tape:'+key,ref)
    return key
