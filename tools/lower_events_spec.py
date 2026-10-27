"""Reproducible event policy and explicit finite tapes for the lower adapter."""
import argparse
from dataclasses import asdict
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.lower_events import policy
from packages.contracts.wire import canonical,digest,read_json
from tools.specification import ROOT

def files():
    profile={'schema_version':'openpine.lower-events-profile.1','source_lock_sha256':digest(read_json(ROOT/'spec/lock.json')),
        'historical_profile_sha256':digest(read_json(ROOT/'spec/lower-tf-profile.json')),'policy':policy(),
        'decisions':['Event adapter is an explicit OpenPine policy, not independently verified TradingView execution.',
        'Without a pinned warmup count, private state starts at the first received close; calc_bars_count does not rebase.',
        'Live pins the actual finite snapshot counts before projecting the first event. Future closes append to that state.',
        'Missing candles are absent; an observed unfinished child is rejected at parent close unless metadata mode is selected.',
        'Late chronological child closes in metadata mode update future private TA state, never committed arrays.']}
    contexts=[MarketContext('primary','TEST:CHART','4','recorded.1'),MarketContext('lower','TEST:LOWER','1','recorded.1')]
    config={'contexts':[asdict(c) for c in contexts],'primary_id':'primary','lower_tf':{
        'schema_version':'openpine.lower-events.1','late':'reject','warmup':{'lower':{'count':16,'end_ms':960000}}}}
    events=[]
    for i in range(16):
        p=float(i+1);t=i*60000
        events.append(MarketEvent(len(events),'lower','close',t,t+60000,0,p,p,p,p,10.).payload())
        if i%4==3:
            t=i//4*240000;events.append(MarketEvent(len(events),'primary','close',t,t+240000,0,100.,100.,100.,100.,10.).payload())
    result={'spec/lower-events-profile.json':canonical(profile)+b'\n','examples/lower-events/contexts.json':canonical(config)+b'\n',
        'examples/lower-events/events.ndjson':b''.join(canonical(e)+b'\n' for e in events),
        'examples/lower-events/expected.json':canonical({'counts':[0,0,4,4],'sums':[None,None,100.,308.]})+b'\n'}
    for v in (5,6):
        result[f'examples/lower-events/v{v}.pine']=(f'//@version={v}\nindicator("Closed child events")\n'
            'a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close),calc_bars_count=8)\nplot(array.size(a))\nplot(array.sum(a))\n').encode()
    return result

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    for name,raw in files().items():
        path=ROOT/name
        if args.check:
            if not path.exists() or path.read_bytes()!=raw:raise ValueError('Stale lower event artifact: '+name)
        else:path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    print('Lower event profile and independent finite examples verified.');return 0
if __name__=='__main__':raise SystemExit(main())
