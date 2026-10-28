"""Durable input inbox, bounded Parquet tape, and atomic replay publication."""

import csv
import uuid
from contextlib import nullcontext
from pathlib import Path

from packages.contracts.realtime import MarketEvent, replay_id
from packages.contracts.wire import canonical, loads, digest
from .columnar import connection, quoted

BLOCK = 10000


class ReplayRepository:
    def __init__(self, database, objects): self.db, self.objects = database, objects

    def create(self, config):
        identifier = uuid.uuid4().hex
        config_ref = self.objects.put(config)
        state = {'schema_version': 'openpine.replay-publication.1', 'sequence': -1,
            'confirmed_bars': 0, 'preview': None, 'simulation': None, 'input_chain': config['identity'],
            'outputs': [], 'series': [], 'contexts': {},**({'settings':config['settings']} if config.get('settings') else {})}
        self.db.execute('INSERT INTO replays VALUES(?,?,NULL,-1,0,?,?)',
            (identifier, config_ref, config['identity'], canonical(state)))
        return identifier

    def row(self, identifier):
        replay_id(identifier)
        rows = self.db.execute('SELECT config_ref,checkpoint_ref,applied,accepted,input_chain,state FROM replays WHERE replay_id=?', (identifier,))
        if not rows: raise ValueError('Unknown replay.')
        return rows[0]

    def repin_empty(self,identifier,config):
        with self.db.transaction():
            row=self.row(identifier)
            if row[1] is not None or row[2]!=-1 or row[3]!=0:raise ValueError('Warmup cannot change after accepting replay events.')
            old=self.objects.get(row[0])
            if {k:v for k,v in old.items() if k not in ('lower_tf','identity','policy')}!={k:v for k,v in config.items() if k not in ('lower_tf','identity','policy')}:
                raise ValueError('Warmup pinning changed another replay setting.')
            state=loads(row[5]);state['input_chain']=config['identity']
            self.db.execute('UPDATE replays SET config_ref=?,input_chain=?,state=? WHERE replay_id=?',
                (self.objects.put(config),config['identity'],canonical(state),identifier))

    def config(self, identifier): return self.objects.get(self.row(identifier)[0])

    def status(self, identifier):
        row = self.row(identifier)
        return {'replay_id': identifier, 'accepted_events': row[3], 'pending_events': row[3]-row[2]-1,
            'checkpoint_ref': row[1], **loads(row[5])}

    def append(self, identifier, events, *, transaction=True):
        if type(events) is not list or not 1 <= len(events) <= 10000: raise ValueError('Append one to 10000 events per batch.')
        events = [MarketEvent.from_payload(e) for e in events]
        if not transaction and self.db.connection.get_autocommit():raise ValueError('Projection append requires an existing transaction.')
        with self.db.transaction() if transaction else nullcontext():
            config_ref, _, _, count, chain, _ = self.row(identifier)
            contexts = {c['context_id'] for c in self.objects.get(config_ref)['contexts']}
            for event in events:
                if event.context_id not in contexts: raise ValueError('Event context is absent from the pinned replay.')
                payload = event.payload(); sha = digest(payload)
                if event.sequence < count:
                    rows = self.db.execute('SELECT event_sha FROM replay_events WHERE replay_id=? AND sequence=?', (identifier, event.sequence))
                    if rows != [(sha,)]: raise ValueError('Conflicting duplicate input event.')
                    continue
                if event.sequence != count: raise ValueError('Missing/out-of-order input sequence.')
                chain = digest({'previous': chain, 'event': payload})
                self.db.execute('INSERT INTO replay_events VALUES(?,?,?,?,?)',
                    (identifier, count, sha, chain, canonical(payload)))
                count += 1
            self.db.execute('UPDATE replays SET accepted=?,input_chain=? WHERE replay_id=?', (count, chain, identifier))
        return self.status(identifier)

    def events(self, identifier, start, stop):
        self.row(identifier)
        cursor = start
        while cursor < stop:
            parts = self.db.execute('SELECT first_sequence,rows,object_id FROM replay_parts WHERE replay_id=? AND first_sequence<=? AND first_sequence+rows>?', (identifier, cursor, cursor))
            if parts:
                first, count, sha = parts[0]
                end = min(stop,first+count)
                with connection(threads=1, memory_mb=32) as db:
                    reader = db.execute('SELECT sequence,event_json FROM read_parquet(?) WHERE sequence>=? AND sequence<? ORDER BY sequence',
                        (str(self.objects.verify(sha)), cursor, end))
                    while batch := reader.fetchmany(1024):
                        for sequence, raw in batch:
                            value=loads(raw)
                            expected=self.db.execute('SELECT event_sha FROM replay_events WHERE replay_id=? AND sequence=?',(identifier,sequence))
                            if sequence != cursor or expected != [(digest(value),)]: raise ValueError('Archived input event differs from its durable cursor/identity.')
                            yield MarketEvent.from_payload(value)
                            cursor += 1
                if cursor != end: raise ValueError('Incomplete archived input range.')
                continue
            rows = self.db.execute('SELECT sequence,payload,event_sha FROM replay_events WHERE replay_id=? AND sequence>=? AND sequence<? ORDER BY sequence LIMIT 1024', (identifier, cursor, stop))
            if not rows: raise ValueError('Missing durable input event.')
            for sequence, raw, sha in rows:
                if sequence != cursor: raise ValueError('Missing durable input sequence.')
                if raw is None:
                    # A concurrent compactor may have moved this accepted tail
                    # after the block lookup. Retry the exact cursor, never skip
                    # to the newest block boundary.
                    if not self.db.execute('SELECT 1 FROM replay_parts WHERE replay_id=? AND first_sequence<=? AND first_sequence+rows>?',(identifier,cursor,cursor)):
                        raise ValueError('Missing input tape block.')
                    break
                value = loads(raw)
                if digest(value) != sha: raise ValueError('Input tape checksum mismatch.')
                yield MarketEvent.from_payload(value)
                cursor = sequence + 1

    def archive(self, identifier, directory, *, force=False):
        """Compact accepted payloads; per-sequence hashes remain for old retries."""
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        stop=self.row(identifier)[3]
        while True:
            start = self.db.execute('SELECT COALESCE(MAX(first_sequence+rows),0) FROM replay_parts WHERE replay_id=?', (identifier,))[0][0]
            if stop-start < BLOCK and not (force and start<stop): return
            count=min(BLOCK,stop-start)
            rows = self.db.execute('SELECT sequence,payload FROM replay_events WHERE replay_id=? AND sequence>=? AND sequence<? ORDER BY sequence', (identifier, start, start+count))
            if len(rows) != count: raise ValueError('Invalid tape archive range.')
            if any(raw is None for _, raw in rows):
                if self.db.execute('SELECT COALESCE(MAX(first_sequence+rows),0) FROM replay_parts WHERE replay_id=?',(identifier,))[0][0]==start:
                    raise ValueError('Missing archived input tape block.')
                continue  # Another compactor published this immutable block.
            path = directory/'tape.csv'; parquet = directory/'tape.parquet'
            with path.open('w', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                for sequence, raw in rows: writer.writerow((sequence, bytes(raw).decode('utf-8')))
            with connection(threads=1, memory_mb=32, temporary=directory) as db:
                db.execute(f"COPY (SELECT * FROM read_csv({quoted(path)},header=false,auto_detect=false,columns={{'sequence':'BIGINT','event_json':'VARCHAR'}},buffer_size=262144,max_line_size=65536)) TO {quoted(parquet)} (FORMAT PARQUET,COMPRESSION ZSTD,ROW_GROUP_SIZE 2048)")
            sha = self.objects.publish(parquet)
            with self.db.transaction():
                current = self.db.execute('SELECT COALESCE(MAX(first_sequence+rows),0) FROM replay_parts WHERE replay_id=?', (identifier,))[0][0]
                if current == start:
                    self.db.execute('INSERT INTO replay_parts VALUES(?,?,?,?)', (identifier, start, count, sha))
                    self.db.execute('UPDATE replay_events SET payload=NULL WHERE replay_id=? AND sequence>=? AND sequence<?', (identifier, start, start+count))
            path.unlink(); parquet.unlink()

    def publish(self, identifier, checkpoint, publication, *, previous):
        sequence = publication['sequence']
        from .checkpoint_records import put
        from packages.contracts.memory import job_memory
        sha = put(self.objects,checkpoint,limit_bytes=job_memory(self.config(identifier)['memory_mb'])['restore_reserve_bytes'])
        with self.db.transaction():
            row = self.row(identifier)
            if row[2] != previous or not previous <= sequence < row[3]: raise ValueError('Stale replay checkpoint publication.')
            chain = self.db.execute('SELECT chain FROM replay_events WHERE replay_id=? AND sequence=?', (identifier, sequence))
            if chain != [(publication['input_chain'],)]: raise ValueError('Execution consumed another input prefix.')
            self.db.execute('UPDATE replays SET checkpoint_ref=?,applied=?,state=? WHERE replay_id=?',
                (sha, sequence, canonical(publication), identifier))
        return sha

    def checkpoint(self, identifier):
        row = self.row(identifier)
        if row[1] is None: return None
        from .checkpoint_records import get
        from packages.contracts.memory import job_memory
        value = get(self.objects,row[1],limit_bytes=job_memory(self.config(identifier).get('memory_mb',512))['restore_reserve_bytes'])
        if (value.keys() != {'schema_version','config_ref','sequence','engine','output'}
                or value['schema_version'] != 'openpine.replay-checkpoint.1' or value['config_ref'] != row[0]
                or value['sequence'] != row[2] or value['engine']['sequence'] != row[2]
                or value['engine']['chain'] != loads(row[5])['input_chain']):
            raise ValueError('Replay checkpoint differs from its catalog publication.')
        return value


def references(database, objects, mark, *, omit_replays=()):
    """Explicit references for the shared backup, audit and garbage collector."""
    for replay_id,config_ref, checkpoint_ref, raw in database.execute('SELECT replay_id,config_ref,checkpoint_ref,state FROM replays'):
        if replay_id in omit_replays:continue
        mark(config_ref)
        config = objects.get(config_ref)
        for key in ('artifact_ref','input_ref'): mark(config[key])
        content = objects.get(config['input_ref'])
        for key in ('source_ref','artifact_ref'):
            if key in content: mark(content[key])
        if checkpoint_ref:
            mark(checkpoint_ref)
            from .checkpoint_records import references as block_references
            block_references(objects,checkpoint_ref,mark)
            from .checkpoint_records import get
            checkpoint = get(objects,checkpoint_ref)
            output = checkpoint['output']
            for part in output['files']: mark(part['object_id'])
            if output['pending']: mark(output['pending']['object_id'])
            trade = output.get('simulation')
            if trade:
                for part in trade['files']: mark(part['object_id'])
                if trade['pending']: mark(trade['pending']['object_id'])
        state = loads(raw)
        for part in state.get('series', []): mark(part['object_id'])
        for part in state.get('simulation', {}).get('journal', {}).get('parts', []) if state.get('simulation') else []: mark(part['object_id'])
    for replay_id,sha in database.execute('SELECT replay_id,object_id FROM replay_parts'):
        if replay_id not in omit_replays:mark(sha)
