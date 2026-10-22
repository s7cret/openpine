"""Independent temporal folds over existing searches, experiments and worker budgets."""
import threading
from packages.contracts.walkforward import SCHEMA, TERMINAL, fold_windows
from packages.contracts.wire import digest
from packages.storage.walkforward import WalkForwardRepository


class WalkForwards:
    def __init__(self, manager, searches, *, start=True):
        self.manager, self.searches = manager, searches
        self.repository = WalkForwardRepository(manager.db)
        self.stopping = threading.Event()
        self.thread = None
        self.fatal = None
        if start:
            self.thread = threading.Thread(target=self._loop, name='openpine-walkforward', daemon=True)
            self.thread.start()

    def create(self, payload):
        if type(payload) is not dict or 'walkforward' not in payload or 'windows' in payload:
            raise ValueError('Use a fixed search request with walkforward policy instead of windows.')
        windows = fold_windows(payload['walkforward'], payload.get('budget'))
        with self.manager.lock, self.manager.limiter.slot('prepare'):
            if len(self.repository.list(active=True, limit=4)) >= 4:raise ValueError('At most four active walk-forward studies.')
            prepared = []
            for ordinal, window in enumerate(windows):
                request = {k: v for k, v in payload.items() if k != 'walkforward'}
                request.update(name=payload['name']+' / fold '+str(ordinal+1), windows=window)
                prepared.append(self.searches.prepare(request))
            manifest = {'schema_version': SCHEMA, 'name': payload['name'], 'policy': payload['walkforward'],
                        'input_ref': payload['input_ref'], 'parent_snapshot_id': payload['snapshot_id'],
                        'state_policy': 'independent_fresh_runtime_and_broker_per_fold_no_capital_or_position_carry',
                        'folds': [{'windows': window, 'search_manifest_sha256': digest(part[0])} for window, part in zip(windows, prepared, strict=True)],
                        'maximum_jobs': len(windows)*(payload['budget']+1)}
            with self.manager.db.transaction():
                identifiers = [self.searches.publish_prepared(part, paused=True) for part in prepared]
                identifier = self.repository.insert(manifest, identifiers)
        return self.status(identifier)

    def status(self, identifier, *, start=0, limit=50):
        if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Walk-forward page requires start >= 0 and limit 1..100.')
        with self.manager.limiter.slot('read'):
            saved = self.repository.get(identifier)
            folds = self.repository.folds(identifier)
            done = all(f['receipt'] is not None for f in folds)
            if saved['desired'] != 'running':status = saved['desired']
            elif not done:status = 'running'
            else:status = 'succeeded' if all(f['receipt']['status'] == 'succeeded' for f in folds) else 'completed_with_errors'
            visible = [{**f, 'windows': saved['manifest']['folds'][f['ordinal']]['windows']} for f in folds[start:start+limit]]
            return {'schema_version': SCHEMA, 'walkforward_id': identifier, 'name': saved['manifest']['name'],
                    'manifest_sha256': saved['manifest_sha256'], 'policy': saved['manifest']['policy'],
                    'status': status, 'desired': saved['desired'], 'error': saved['error'] or self.fatal,
                    'completed': sum(f['receipt'] is not None for f in folds), 'total': len(folds),
                    'start': start, 'next': min(len(folds), start+limit), 'folds': visible}

    def list(self):
        return [self.status(identifier) for identifier in self.repository.list()]

    def control(self, identifier, action):
        if action not in ('pause', 'resume', 'cancel'):raise ValueError('Unknown study control.')
        with self.manager.lock:
            saved = self.repository.get(identifier)
            if saved['desired'] == 'cancelled' and action != 'cancel':raise ValueError('Cancelled studies cannot resume.')
            if all(f['receipt'] is not None for f in self.repository.folds(identifier)):return self.status(identifier)
            if action == 'resume' and saved['desired'] != 'running' and len(self.repository.list(active=True, limit=4)) >= 4:
                raise ValueError('At most four active walk-forward studies.')
            desired = {'pause': 'paused', 'resume': 'running', 'cancel': 'cancelled'}[action]
            folds = [f for f in self.repository.folds(identifier) if f['receipt'] is None]
            with self.manager.db.transaction():
                self.repository.control(identifier, desired)
                if action != 'resume':
                    for fold in folds:
                        child = self.searches.repository.get(fold['search_id'])
                        self.searches.repository.control(fold['search_id'], desired)
                        for group in [child['selection_id'], *([child['holdout_id']] if child['holdout_id'] else [])]:
                            self.searches.experiments.repository.control(group, desired)
            if action != 'resume':
                for fold in folds:self.searches.control(fold['search_id'], action, study_owner=True)
            elif folds:
                # An explicit resume acknowledges a previous error. Initial
                # and future folds stay paused until admitted by tick.
                child = self.searches.repository.get(folds[0]['search_id'])
                self.searches.repository.control(folds[0]['search_id'], child['desired'])
        return self.status(identifier)

    def tick(self):
        if self.stopping.is_set() or self.manager.stopping.is_set():return
        for identifier in self.repository.list(active=True, limit=4):
            try:
                saved = self.repository.get(identifier)
                fold = next((f for f in self.repository.folds(identifier) if f['receipt'] is None), None)
                if fold is None:continue
                search_id = fold['search_id']
                state = self.searches.status(search_id, limit=1)
                if state['status'] in TERMINAL:
                    with self.manager.lock, self.manager.db.transaction():
                        if self.repository.get(identifier)['desired'] != 'running':continue
                        self.repository.record(identifier, fold['ordinal'], {
                            'status': state['status'], 'search_id': search_id,
                            'decision': state['decision'], 'holdout': state['holdout'],
                            'windows': saved['manifest']['folds'][fold['ordinal']]['windows']})
                elif state['desired'] == 'paused':
                    if state['error']:raise ValueError(state['error'])
                    with self.manager.lock:
                        if len(self.searches.repository.list(active=True,limit=8)) >= 8:continue
                        if len(self.searches.experiments.repository.groups(active=True,limit=16)) >= 16:continue
                        if self.repository.get(identifier)['desired'] == 'running':self.searches.control(search_id, 'resume', study_owner=True)
            except Exception as error:
                with self.manager.lock:
                    self.control(identifier, 'pause')
                    self.repository.control(identifier, 'paused', str(error))

    def _loop(self):
        while not self.stopping.wait(.2):
            try:self.tick()
            except Exception as error:self.fatal = str(error);return

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():raise RuntimeError('Walk-forward supervisor did not stop.')
