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
            'outputs': [], 'series': [], 'contexts': {}}
        self.db.execute('INSERT INTO replays VALUES(?,?,NULL,-1,0,?,?)',
            (identifier, config_ref, config['identity'], canonical(state)))
        return identifier

    def row(self, identifier):
        replay_id(identifier)
        rows = self.db.execute('SELECT config_ref,checkpoint_ref,applied,accepted,input_chain,state FROM replays WHERE replay_id=?', (identifier,))
        if not rows: raise ValueError('Unknown replay.')
        return rows[0]

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

    def archive(self, identifier, directory):
        """Compact accepted payloads; per-sequence hashes remain for old retries."""
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        while True:
            start = self.db.execute('SELECT COALESCE(MAX(first_sequence+rows),0) FROM replay_parts WHERE replay_id=?', (identifier,))[0][0]
            count = self.row(identifier)[3]
            if count-start < BLOCK: return
            rows = self.db.execute('SELECT sequence,payload FROM replay_events WHERE replay_id=? AND sequence>=? AND sequence<? ORDER BY sequence', (identifier, start, start+BLOCK))
            if len(rows) != BLOCK: raise ValueError('Invalid tape archive range.')
            if any(raw is None for _, raw in rows): continue  # Another compactor published this immutable block.
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
                    self.db.execute('INSERT INTO replay_parts VALUES(?,?,?,?)', (identifier, start, BLOCK, sha))
                    self.db.execute('UPDATE replay_events SET payload=NULL WHERE replay_id=? AND sequence>=? AND sequence<?', (identifier, start, start+BLOCK))
            path.unlink(); parquet.unlink()

    def publish(self, identifier, checkpoint, publication, *, previous):
        sequence = publication['sequence']
        sha = self.objects.put_stream(checkpoint)
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
        value = self.objects.get(row[1], max_bytes=256*1024*1024)
        if (value.keys() != {'schema_version','config_ref','sequence','engine','output'}
                or value['schema_version'] != 'openpine.replay-checkpoint.1' or value['config_ref'] != row[0]
                or value['sequence'] != row[2] or value['engine']['sequence'] != row[2]
                or value['engine']['chain'] != loads(row[5])['input_chain']):
            raise ValueError('Replay checkpoint differs from its catalog publication.')
        return value


def references(database, objects, mark):
    """Explicit references for the shared backup, audit and garbage collector."""
    for config_ref, checkpoint_ref, raw in database.execute('SELECT config_ref,checkpoint_ref,state FROM replays'):
        mark(config_ref)
        config = objects.get(config_ref)
        for key in ('artifact_ref','input_ref'): mark(config[key])
        content = objects.get(config['input_ref'])
        for key in ('source_ref','artifact_ref'):
            if key in content: mark(content[key])
        if checkpoint_ref:
            mark(checkpoint_ref)
            checkpoint = objects.get(checkpoint_ref, max_bytes=256*1024*1024)
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
    for (sha,) in database.execute('SELECT object_id FROM replay_parts'): mark(sha)
