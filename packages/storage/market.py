"""Short transactions for instrument discovery and resumable acquisition tasks."""

import time
import uuid
from dataclasses import replace

from packages.contracts.market import InstrumentKey, InstrumentMetadata
from packages.contracts.wire import canonical, digest, loads


class MarketRepository:
    def __init__(self, database): self.db = database

    def replace_instruments(self, rows):
        # Validate the entire response before replacing any catalogue row.
        if any(type(row) is not InstrumentMetadata for row in rows): raise ValueError('Invalid catalogue entry.')
        with self.db.transaction():
            old = {key: InstrumentMetadata.from_payload(loads(raw)) for key,raw in self.db.execute('SELECT * FROM market_instruments')}
            seen = set()
            for row in rows:
                key = row.instrument.key
                if key in seen: raise ValueError('Duplicate catalogue identity.')
                seen.add(key)
                if key in old: row = replace(row, first_available_ms=old[key].first_available_ms)
                self.db.execute('INSERT OR REPLACE INTO market_instruments VALUES(?,?)', (key, canonical(row.payload())))
            for key in old.keys()-seen:
                self.db.execute('UPDATE market_instruments SET metadata=? WHERE instrument_key=?',
                    (canonical(replace(old[key],status='UNAVAILABLE').payload()), key))

    def instrument(self, instrument):
        if type(instrument) is not InstrumentKey: raise ValueError('Invalid instrument identity.')
        rows = self.db.execute('SELECT metadata FROM market_instruments WHERE instrument_key=?', (instrument.key,))
        return InstrumentMetadata.from_payload(loads(rows[0][0])) if rows else None

    def first_available(self, instrument, timestamp):
        row = self.instrument(instrument)
        if row is not None:
            row = replace(row,first_available_ms=timestamp)
            self.db.execute('UPDATE market_instruments SET metadata=? WHERE instrument_key=?', (canonical(row.payload()),instrument.key))

    def upsert_instrument(self, row):
        previous = self.instrument(row.instrument)
        if previous is not None: row = replace(row, first_available_ms=previous.first_available_ms)
        self.db.execute('INSERT OR REPLACE INTO market_instruments VALUES(?,?)',(row.instrument.key,canonical(row.payload())))

    def search_instruments(self, query='', limit=100):
        if type(query) is not str or len(query)>64 or type(limit) is not int or not 1<=limit<=200:
            raise ValueError('Invalid bounded instrument search.')
        # instr avoids treating user '%' or '_' as wildcard SQL.
        return [loads(row[0]) for row in self.db.execute(
            'SELECT metadata FROM market_instruments WHERE instr(upper(instrument_key),?)>0 ORDER BY instrument_key LIMIT ?',
            (query.upper() if query else '',limit))] if query else [loads(row[0]) for row in self.db.execute(
            'SELECT metadata FROM market_instruments ORDER BY instrument_key LIMIT ?', (limit,))]

    def submit(self, request):
        key = digest(request)
        with self.db.transaction():
            rows = self.db.execute("SELECT state FROM data_tasks WHERE request_key=? AND status IN ('queued','running','interrupted')", (key,))
            if rows: return {**loads(rows[0][0]),'joined':True}
            if self.db.execute("SELECT count(*) FROM data_tasks WHERE status IN ('queued','running','interrupted')")[0][0]>=32:
                raise ValueError('Acquisition queue is full.')
            identifier = uuid.uuid4().hex
            state = {'task_id':identifier,'status':'queued','cursor':None,'pages':0,'bars':0,'snapshot_id':None,
                'coverage':None,'as_of':None,'first_available_ms':None,'error':None,'cancel_requested':False,
                'attempts':0,'created_at':time.time(),'updated_at':time.time()}
            self.db.execute('INSERT INTO data_tasks VALUES(?,?,?,?,?,?)',
                (identifier,key,'queued',canonical(request),canonical(state),state['created_at']))
            return state

    def task(self, identifier):
        if type(identifier) is not str or len(identifier)!=32: raise ValueError('Invalid acquisition identifier.')
        rows = self.db.execute('SELECT state FROM data_tasks WHERE task_id=?',(identifier,))
        if not rows: raise ValueError('Unknown acquisition task.')
        return loads(rows[0][0])

    def request(self, identifier):
        self.task(identifier)
        return loads(self.db.execute('SELECT request FROM data_tasks WHERE task_id=?',(identifier,))[0][0])

    def save_task(self, state, *, reset_cancel=False):
        with self.db.lock:
            previous=self.task(state['task_id'])
            state = {**state,'updated_at':time.time(),
                'cancel_requested':state['cancel_requested'] or previous['cancel_requested'] and not reset_cancel}
            self.db.execute('UPDATE data_tasks SET status=?,state=? WHERE task_id=?', (state['status'],canonical(state),state['task_id']))

    def tasks(self, status=None, limit=100):
        return [loads(row[0]) for row in self.db.execute('SELECT state FROM data_tasks '+
            ('WHERE status=? ' if status else '')+'ORDER BY created_at DESC LIMIT ?', (status,limit) if status else (limit,))]

    def recover(self):
        for state in [*self.tasks('running'),*self.tasks('interrupted')]:
            self.save_task({**state,'status':'queued','error':'Coordinator restarted; continuing the durable page cursor.'})

    def commit_page(self, state, store, namespace, query, start, stop, snapshot_id):
        with self.db.transaction():
            current = self.task(state['task_id'])
            if current['status']!='running' or current['cancel_requested']: raise InterruptedError('Acquisition cancelled before publication.')
            store.cover(namespace,query,start,stop,snapshot_id)
            self.db.execute('INSERT INTO data_pages VALUES(?,?,?,?)',(state['task_id'],start,stop,snapshot_id))
            self.save_task(state)
