"""Installed comparison acceptance over actual committed worker publications."""
import uuid,time

def compare(http,left,right,mode='compatible'):
    key=uuid.uuid4().hex
    value=http('/api/comparisons',{'request_id':key,'left':left,'right':right,'mode':mode})
    deadline=time.monotonic()+120
    while value['state']['status'] in ('queued','running') and time.monotonic()<deadline:
        time.sleep(.1);value=http('/api/comparisons/'+key)
    return value

def verify(http,job,trace,replay,clone):
    history=compare(http,{'kind':'job','id':job},{'kind':'job','id':trace})
    assert history['state']['status']=='equal',history
    observed=compare(http,{'kind':'replay','id':replay},{'kind':'replay','id':clone})
    assert observed['state']['status']=='equal',observed
    blocked=compare(http,{'kind':'job','id':job},{'kind':'replay','id':replay})
    assert blocked['state']['status']=='not_comparable' and blocked['report'],blocked
    assert history['report']['whole_program_equivalence_verified'] is False
    return {'comparison_history_equal':True,'comparison_replay_equal':True,'comparison_model_guard':True,
        'comparison_history_chain':history['state']['chains']['left'],'comparison_replay_chain':observed['state']['chains']['left']}
