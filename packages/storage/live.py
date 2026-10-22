"""Durable raw observation journal and independently resumable subscribers."""

import csv
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from packages.contracts.wire import canonical, digest, loads
from packages.contracts.market import CandleObservation
from packages.contracts.model import HistoricalBar
from packages.contracts.marketdata import DataQuery
from packages.marketdata.timeframes import next_open
from .columnar import connection, quoted

BLOCK=10000


class LiveRepository:
    def __init__(self,database,objects):self.db,self.objects=database,objects

    @staticmethod
    def series_key(config):
        return digest({k:config[k] for k in ('instrument','timeframe','ingestion_sha256')})

    @staticmethod
    def feed_id(config):
        value={k:config[k] for k in ('instrument','timeframe','ingestion_sha256')}
        if 'generation' in config:value['generation']=config['generation']
        return digest(value)[:32]

    def choose_config(self,base,*,fresh=False):
        rows=self.db.execute('SELECT feed_id FROM live_series WHERE series_key=?',(self.series_key(base),))
        if rows and not fresh:
            config=self.config(rows[0][0])
            if self.series_key(config)!=self.series_key(base) or self.feed_id(config)!=rows[0][0]:raise ValueError('Source alias identity mismatch.')
            return config
        return {**base,'generation':uuid.uuid4().hex if fresh else 'initial'}

    def feed(self,config):
        identifier=self.feed_id(config)
        rows=self.db.execute('SELECT config FROM live_feeds WHERE feed_id=?',(identifier,))
        if rows:
            previous=loads(rows[0][0])
            if (any(previous[k]!=config[k] for k in ('instrument','timeframe','ingestion_sha256'))
                    or previous.get('generation')!=config.get('generation')):raise ValueError('Live feed identity collision.')
            return identifier
        state={'feed_id':identifier,'status':'stopped','accepted':0,'epoch':0,'last_received_at':None,
            'last_closed_ms':None,'last_observation':None,'reconnects':0,'error':None,'updated_at':time.time()}
        self.db.execute('INSERT INTO live_feeds VALUES(?,?,?)',(identifier,canonical(config),canonical(state)))
        self.db.execute('INSERT INTO live_series VALUES(?,?) ON CONFLICT(series_key) DO UPDATE SET feed_id=excluded.feed_id',
            (self.series_key(config),identifier))
        return identifier

    def config(self,identifier):
        rows=self.db.execute('SELECT config FROM live_feeds WHERE feed_id=?',(identifier,))
        if not rows:raise ValueError('Unknown live feed.')
        return loads(rows[0][0])

    def state(self,identifier):
        rows=self.db.execute('SELECT state FROM live_feeds WHERE feed_id=?',(identifier,))
        if not rows:raise ValueError('Unknown live feed.')
        return loads(rows[0][0])

    def save(self,state):
        self.db.execute('UPDATE live_feeds SET state=? WHERE feed_id=?',(canonical({**state,'updated_at':time.time()}),state['feed_id']))

    def append(self,identifier,record):
        if type(record) is not dict or record.get('kind') not in ('observation','gap','recovery','diagnostic'):
            raise ValueError('Invalid live journal record.')
        if record['kind']=='observation':
            observation=CandleObservation.from_payload(record['observation']);config=self.config(identifier)
            if asdict(observation.instrument)!=config['instrument'] or observation.timeframe!=config['timeframe']:
                raise ValueError('Observation belongs to another feed identity.')
        if len(canonical(record))>65536:raise ValueError('Live journal record is oversized.')
        with self.db.transaction():
            state=self.state(identifier);sequence=state['accepted']
            if sequence>=10000000:raise ValueError('Live journal limit reached; rotate into a new state directory.')
            self.db.execute('INSERT INTO live_observations VALUES(?,?,?,?)',(identifier,sequence,canonical(record),digest(record)))
            self.db.execute('INSERT INTO live_arrivals(feed_id,source_sequence) VALUES(?,?)',(identifier,sequence))
            state={**state,'accepted':sequence+1}
            if record['kind']=='observation' and record.get('disposition','apply')=='apply':
                observed=record['observation']
                state.update(last_received_at=observed['received_at'],last_observation=observed)
                if observed['closed']:state['last_closed_ms']=max(observed['open_time'],state['last_closed_ms'] or 0)
            elif record['kind']=='gap':state['epoch']+=1
            self.save(state)
        return sequence

    def closed(self,identifier,bar,*,register=False,snapshot_id=None,row_offset=None):
        value={k:getattr(bar,k) for k in ('open','high','low','close','volume')}
        timestamp=bar.open_time if isinstance(bar,CandleObservation) else bar.time
        sha=digest(value)
        rows=self.db.execute('SELECT sha256 FROM live_closed WHERE feed_id=? AND open_time=?',(identifier,timestamp))
        if rows and rows!=[(sha,)]:raise ValueError('Confirmed source candle changed; rebuild a new session from revised history.')
        if register:
            values=canonical(asdict(HistoricalBar(timestamp,bar.close,bar.open,bar.high,bar.low,bar.volume)))
            self.db.execute('INSERT OR IGNORE INTO live_closed VALUES(?,?,?,?,?,?)',
                (identifier,timestamp,sha,None if snapshot_id else values,snapshot_id,row_offset))
            if snapshot_id:
                self.db.execute('UPDATE live_closed SET bar=NULL,snapshot_id=?,row_offset=? WHERE feed_id=? AND open_time=? AND snapshot_id IS NULL',
                    (snapshot_id,row_offset,identifier,timestamp))
        return bool(rows)

    def flush_closed(self,store,identifier,*,force=False):
        rows=self.db.execute('SELECT open_time,bar FROM live_closed WHERE feed_id=? AND bar IS NOT NULL ORDER BY open_time LIMIT 1000',(identifier,))
        if not rows or len(rows)<1000 and not force:return
        bars=[HistoricalBar(**loads(raw)) for _,raw in rows]
        config=self.config(identifier);key=config['instrument'];tf=config['timeframe']
        snapshot_id=store.ingest({'context_id':'binance.spot.'+key['symbol']+'.'+tf,'symbol':'BINANCE:'+key['symbol'],
            'timeframe':tf,'bar_schema':'openpine.historical-bars.2'},bars,{'provider':'binance.spot.ws.1','feed_id':identifier})
        query=DataQuery('BINANCE:'+key['symbol'],tf,bars[0].time,next_open(bars[-1].time,tf))
        with self.db.transaction():
            start=previous=bars[0].time
            for index,bar in enumerate(bars):
                if index and next_open(previous,tf)!=bar.time:
                    store.cover('binance.spot.ws.1',query,start,next_open(previous,tf),snapshot_id);start=bar.time
                self.db.execute('UPDATE live_closed SET bar=NULL,snapshot_id=?,row_offset=? WHERE feed_id=? AND open_time=? AND bar IS NOT NULL',
                    (snapshot_id,index,identifier,bar.time))
                previous=bar.time
            store.cover('binance.spot.ws.1',query,start,next_open(previous,tf),snapshot_id)

    def candles(self,store,identifier,start_ms,end_ms,*,offset=0,limit=1000):
        rows=self.db.execute('SELECT open_time,bar,snapshot_id,row_offset,sha256 FROM live_closed '
            'WHERE feed_id=? AND open_time>=? AND open_time<? ORDER BY open_time LIMIT ? OFFSET ?',
            (identifier,start_ms,end_ms,limit,offset))
        index=0
        while index<len(rows):
            timestamp,raw,snapshot,row_offset,sha=rows[index]
            if raw is not None:
                bar=HistoricalBar(**loads(raw));index+=1
                if bar.time!=timestamp or digest({k:getattr(bar,k) for k in ('open','high','low','close','volume')})!=sha:
                    raise ValueError('Corrupt live candle tail.')
                yield bar;continue
            stop=index+1
            while stop<len(rows) and rows[stop][2]==snapshot and rows[stop][3]==row_offset+stop-index:stop+=1
            bars=list(store.source(snapshot).rows(offset=row_offset,limit=stop-index))
            if len(bars)!=stop-index:raise ValueError('Truncated live candle snapshot.')
            for expected,bar in zip(rows[index:stop],bars,strict=True):
                if bar.time!=expected[0] or digest({k:getattr(bar,k) for k in ('open','high','low','close','volume')})!=expected[4]:
                    raise ValueError('Live candle index differs from its snapshot.')
                yield bar
            index=stop

    def records(self,identifier,start=0,limit=1000):
        count=self.state(identifier)['accepted'];stop=min(count,start+limit)
        if type(start) is not int or start<0 or type(limit) is not int or not 1<=limit<=10000:raise ValueError('Invalid live journal range.')
        cursor=start
        while cursor<stop:
            parts=self.db.execute('SELECT first_sequence,rows,object_id FROM live_parts WHERE feed_id=? AND first_sequence<=? AND first_sequence+rows>?',(identifier,cursor,cursor))
            if parts:
                first,rows,sha=parts[0];end=min(stop,first+rows)
                with connection(threads=1,memory_mb=32) as db:
                    reader=db.execute('SELECT sequence,record FROM read_parquet(?) WHERE sequence>=? AND sequence<? ORDER BY sequence',
                        (str(self.objects.verify(sha)),cursor,end))
                    while batch:=reader.fetchmany(1000):
                        for seq,raw in batch:
                            value=loads(raw)
                            if seq!=cursor or self.db.execute('SELECT sha256 FROM live_observations WHERE feed_id=? AND sequence=?',(identifier,seq))!=[(digest(value),)]:
                                raise ValueError('Corrupt archived live journal.')
                            yield seq,value;cursor+=1
                if cursor!=end:raise ValueError('Truncated archived live journal.')
            else:
                rows=self.db.execute('SELECT sequence,payload,sha256 FROM live_observations WHERE feed_id=? AND sequence>=? AND sequence<? ORDER BY sequence LIMIT 1000',(identifier,cursor,stop))
                if not rows:raise ValueError('Missing live journal prefix.')
                for seq,raw,sha in rows:
                    if raw is None:break
                    value=loads(raw)
                    if seq!=cursor or digest(value)!=sha:raise ValueError('Corrupt live journal.')
                    yield seq,value;cursor+=1

    def archive(self,identifier,directory):
        start=self.db.execute('SELECT COALESCE(MAX(first_sequence+rows),0) FROM live_parts WHERE feed_id=?',(identifier,))[0][0]
        while self.state(identifier)['accepted']-start>=BLOCK:
            root=Path(directory);root.mkdir(parents=True,exist_ok=True)
            csv_path,parquet=root/'live.csv',root/'live.parquet'
            with csv_path.open('w',encoding='utf-8',newline='') as stream:
                writer=csv.writer(stream)
                for seq,record in self.records(identifier,start,BLOCK):writer.writerow((seq,canonical(record).decode('utf-8')))
            with connection(threads=1,memory_mb=32) as db:
                db.execute(f"COPY (SELECT * FROM read_csv({quoted(csv_path)},header=false,columns={{'sequence':'BIGINT','record':'VARCHAR'}})) TO {quoted(parquet)} (FORMAT PARQUET,COMPRESSION ZSTD)")
            sha=self.objects.publish(parquet)
            with self.db.transaction():
                self.db.execute('INSERT INTO live_parts VALUES(?,?,?,?)',(identifier,start,BLOCK,sha))
                self.db.execute('UPDATE live_observations SET payload=NULL WHERE feed_id=? AND sequence>=? AND sequence<?',(identifier,start,start+BLOCK))
            csv_path.unlink();parquet.unlink();start+=BLOCK

    def create_session(self,feed_id,replay_id,config,state):
        identifier=uuid.uuid4().hex
        state={**state,'session_id':identifier,'feed_id':feed_id,'replay_id':replay_id}
        self.db.execute('INSERT INTO live_sessions(session_id,feed_id,replay_id,config,state) VALUES(?,?,?,?,?)',(identifier,feed_id,replay_id,canonical(config),canonical(state)))
        self.db.execute('INSERT INTO live_bindings VALUES(?,?,?)',(identifier,'primary',feed_id))
        return state

    def arrival(self,feed_id,sequence):
        rows=self.db.execute('SELECT arrival FROM live_arrivals WHERE feed_id=? AND source_sequence=?',(feed_id,sequence))
        return rows[0][0] if rows else None

    @staticmethod
    def cursor(state,feed_id):
        if 'sources' not in state:return state['cursor']
        return next(s['cursor'] for s in state['sources'].values() if s['feed_id']==feed_id)

    def session(self,identifier):
        rows=self.db.execute('SELECT state FROM live_sessions WHERE session_id=?',(identifier,))
        if not rows:raise ValueError('Unknown live session.')
        return loads(rows[0][0])

    def session_config(self,identifier):
        rows=self.db.execute('SELECT config FROM live_sessions WHERE session_id=?',(identifier,))
        if not rows:raise ValueError('Unknown live session.')
        return loads(rows[0][0])

    def save_session(self,state,*,desired=None):
        with self.db.lock:
            previous=self.session(state['session_id'])
            state={**state,'desired':previous['desired'] if desired is None else desired}
            self.db.execute('UPDATE live_sessions SET state=?,desired=? WHERE session_id=?',(canonical(state),state['desired'],state['session_id']))

    def sessions(self,feed_id=None,*,active=False,limit=100):
        clauses=[];params=[]
        if feed_id:clauses.append('session_id IN (SELECT session_id FROM live_bindings WHERE feed_id=?)');params.append(feed_id)
        if active:clauses.append("desired='running'")
        params.append(limit)
        return [loads(row[0]) for row in self.db.execute('SELECT state FROM live_sessions'+
            (' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY session_id LIMIT ?',params)]
