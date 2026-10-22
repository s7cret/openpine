"""Explicit journal rotation and reviewed retention of stopped market sessions."""
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from packages.contracts import OpenPineError
from packages.contracts.wire import digest,loads
from packages.storage.lease import Lease
from packages.storage.lifecycle import reachable
from packages.storage.live import LiveRepository
from packages.storage.objects import identifier,sync_directory
from packages.storage.replay import ReplayRepository


class MarketMaintenance:
    def __init__(self,manager,*,live=None):
        self.manager=manager;self.root=manager.objects.root.parent.resolve();self.live=live

    def rotate(self):
        with self.manager.limiter.slot('prepare'),tempfile.TemporaryDirectory(prefix='market-rotate-',dir=self.root) as directory:
            raw=LiveRepository(self.manager.db,self.manager.objects);replay=ReplayRepository(self.manager.db,self.manager.objects)
            for (feed,) in self.manager.db.execute('SELECT feed_id FROM live_feeds'):raw.archive(feed,Path(directory)/feed,force=True)
            for (identifier_,) in self.manager.db.execute('SELECT replay_id FROM replays'):replay.archive(identifier_,Path(directory)/identifier_,force=True)
        return {'status':'rotated',**self.segments()}

    def segments(self):
        db=self.manager.db
        return {'raw_segments':db.execute('SELECT COUNT(*) FROM live_parts')[0][0],
            'event_segments':db.execute('SELECT COUNT(*) FROM replay_parts')[0][0],
            'raw_tail_records':db.execute('SELECT COUNT(*) FROM live_observations WHERE payload IS NOT NULL')[0][0],
            'event_tail_records':db.execute('SELECT COUNT(*) FROM replay_events WHERE payload IS NOT NULL')[0][0]}

    def _plan(self,before_ms,grace_seconds):
        if type(before_ms) is not int or not 0<=before_ms<=int(time.time()*1000):raise ValueError('Retention cutoff must be a past UTC millisecond timestamp.')
        if type(grace_seconds) is not int or not 0<=grace_seconds<=31536000:raise ValueError('Invalid orphan grace seconds.')
        db=self.manager.db;objects=self.manager.objects
        sessions=db.execute("SELECT session_id,replay_id FROM live_sessions WHERE desired='stopped' AND json_extract(CAST(state AS TEXT),'$.created_at')<? ORDER BY session_id LIMIT 1001",(before_ms,))
        truncated=len(sessions)>1000;sessions=sessions[:1000];session_ids=[row[0] for row in sessions];replays={row[1] for row in sessions}
        placeholders=','.join('?' for _ in session_ids)
        protected='SELECT DISTINCT feed_id FROM live_bindings'+(f' WHERE session_id NOT IN ({placeholders})' if session_ids else '')
        feeds=db.execute(f"SELECT feed_id FROM live_feeds WHERE feed_id NOT IN ({protected}) AND json_extract(CAST(state AS TEXT),'$.updated_at')<? ORDER BY feed_id LIMIT 1001",(*session_ids,before_ms/1000))
        truncated=truncated or len(feeds)>1000;feeds={row[0] for row in feeds[:1000]}
        marked=reachable(db,objects,omit_replays=replays,omit_feeds=feeds)
        if objects.root.is_symlink() or not objects.root.resolve().is_relative_to(self.root):raise ValueError('Unsafe immutable object root.')
        candidates=[];cutoff=time.time()-grace_seconds
        for path in sorted(objects.root.glob('[0-9a-f][0-9a-f]/*')):
            identifier(path.name)
            if not path.is_file() or path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(objects.root.resolve()):raise ValueError('Unsafe retention object path.')
            stat=path.stat()
            if path.name not in marked and stat.st_mtime<=cutoff:
                if len(candidates)<10000:candidates.append({'object_id':path.name,'bytes':stat.st_size})
                else:truncated=True
        blockers={'jobs':db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')")[0][0],
            'history':db.execute("SELECT COUNT(*) FROM data_tasks WHERE status IN ('queued','running')")[0][0],
            'live_sessions':db.execute("SELECT COUNT(*) FROM live_sessions WHERE desired='running'")[0][0],
            'workers':len(getattr(self.manager,'external_workers',{}))+len(getattr(self.manager,'children',{})),
            'physical_feeds':len(self.live.hub.active) if self.live else 0,'replay_attempts':0}
        from packages.storage.experiments import ExperimentRepository
        blockers['experiments']=len(ExperimentRepository(db).groups(active=True,limit=16))
        # After coordinator death, its child can still be alive until watch_parent
        # observes the loss. A new coordinator must respect the child's OS lease.
        for path in self.root.glob('replay-attempt-*'):
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):raise ValueError('Unsafe replay attempt path.')
            marker=path/'.active.lock'
            if not marker.exists():continue
            if marker.is_symlink():raise ValueError('Unsafe replay attempt lease.')
            try:lease=Lease(marker)
            except OpenPineError:blockers['replay_attempts']+=1
            else:lease.close()
        plan={'schema_version':'openpine.market-retention.1','before_ms':before_ms,'grace_seconds':grace_seconds,
            'sessions':session_ids,'replays':sorted(replays),'feeds':sorted(feeds),'objects':candidates,
            'reclaimable_object_bytes':sum(row['bytes'] for row in candidates),'blockers':blockers,'truncated':truncated,
            'preserved':'datasets, coverage, input pins, remaining sessions and their shared feeds'}
        return plan

    def preview(self,before_ms,*,grace_seconds=3600):
        with self.manager.limiter.slot('read'):
            plan=self._plan(before_ms,grace_seconds)
        return {**plan,'preview_id':digest(plan),'apply_allowed':not any(plan['blockers'].values())}

    def apply(self,before_ms,preview_id,*,grace_seconds=3600):
        with self.manager.limiter.slot('prepare'),ExitStack() as leases:
            plan=self._plan(before_ms,grace_seconds)
            for replay in plan['replays']:leases.enter_context(Lease(self.root/('.replay-'+replay+'.lock')))
            with self.manager.db.transaction():
                plan=self._plan(before_ms,grace_seconds)
                if digest(plan)!=preview_id:raise ValueError('Retention preview is stale; obtain a new preview.')
                if any(plan['blockers'].values()):raise ValueError('Stop active work before applying market retention.')
                db=self.manager.db
                for session in plan['sessions']:db.execute('DELETE FROM live_sessions WHERE session_id=?',(session,))
                for replay in plan['replays']:
                    db.execute('DELETE FROM replay_parts WHERE replay_id=?',(replay,));db.execute('DELETE FROM replay_events WHERE replay_id=?',(replay,))
                    db.execute('DELETE FROM replays WHERE replay_id=?',(replay,))
                for feed in plan['feeds']:
                    for table in ('live_series','live_arrivals','live_parts','live_closed','live_observations'):
                        db.execute(f'DELETE FROM {table} WHERE feed_id=?',(feed,))
                    db.execute('DELETE FROM live_feeds WHERE feed_id=?',(feed,))
                # Recheck every remaining root before publishing the catalog deletion.
                reachable(db,self.manager.objects)
            for row in plan['objects']:
                path=self.manager.objects.path(row['object_id'])
                if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(self.manager.objects.root.resolve()):raise ValueError('Unsafe retention deletion path.')
                path.unlink();sync_directory(path.parent)
            self.manager.db.check()
        return {'status':'applied','sessions_removed':len(plan['sessions']),'feeds_removed':len(plan['feeds']),
            'objects_removed':len(plan['objects']),'object_bytes_removed':plan['reclaimable_object_bytes']}
