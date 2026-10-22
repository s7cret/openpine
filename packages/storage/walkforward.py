"""Immutable study composition and write-once fold receipts."""
import time
import uuid
from packages.contracts.wire import canonical, digest, loads
from packages.storage.optimization import SearchRepository


class WalkForwardRepository:
    def __init__(self, db):self.db = db

    def insert(self, manifest, searches):
        identifier = uuid.uuid4().hex
        self.db.execute('INSERT INTO walkforwards VALUES(?,?,?,?,?,NULL)',
                        (identifier, time.time(), 'running', canonical(manifest), digest(manifest)))
        for ordinal, search in enumerate(searches):
            self.db.execute('INSERT INTO walkforward_folds VALUES(?,?,?,NULL,NULL)', (identifier, ordinal, search))
        return identifier

    def get(self, identifier):
        rows = self.db.execute('SELECT desired,manifest,manifest_sha256,error FROM walkforwards WHERE walkforward_id=?', (identifier,))
        if not rows:raise ValueError('Unknown walk-forward study.')
        desired, raw, sha, error = rows[0]
        manifest = loads(raw)
        if digest(manifest) != sha:raise ValueError('Walk-forward manifest checksum differs.')
        return {'walkforward_id': identifier, 'desired': desired, 'manifest': manifest, 'manifest_sha256': sha, 'error': error}

    def folds(self, identifier):
        manifest = self.get(identifier)['manifest']
        rows = self.db.execute('SELECT ordinal,search_id,receipt,receipt_sha256 FROM walkforward_folds WHERE walkforward_id=? ORDER BY ordinal', (identifier,))
        if [r[0] for r in rows] != list(range(len(manifest['folds']))):raise ValueError('Walk-forward fold count differs.')
        result = []
        for ordinal, search, raw, sha in rows:
            saved = SearchRepository(self.db).get(search)
            if saved['manifest_sha256'] != manifest['folds'][ordinal]['search_manifest_sha256']:
                raise ValueError('Walk-forward child search differs from its frozen manifest.')
            receipt = loads(raw) if raw is not None else None
            if receipt is not None and digest(receipt) != sha:raise ValueError('Walk-forward receipt checksum differs.')
            if receipt is not None:
                evidence = SearchRepository(self.db).assessments(search, 'holdout')
                if (receipt['search_id'] != search or receipt['decision'] != saved['decision']
                        or receipt['holdout'] != (evidence[0] if evidence else None)
                        or receipt['windows'] != saved['manifest']['windows']):
                    raise ValueError('Walk-forward receipt differs from its immutable search evidence.')
            result.append({'ordinal': ordinal, 'search_id': search, 'receipt': receipt})
        return result

    def list(self, *, active=False, limit=50):
        return [r[0] for r in self.db.execute('SELECT walkforward_id FROM walkforwards'+
                (" WHERE desired='running' AND EXISTS(SELECT 1 FROM walkforward_folds f WHERE f.walkforward_id=walkforwards.walkforward_id AND f.receipt IS NULL)" if active else '')+
                ' ORDER BY created_at,walkforward_id LIMIT ?', (limit,))]

    def control(self, identifier, desired, error=None):
        self.get(identifier)
        self.db.execute('UPDATE walkforwards SET desired=?,error=? WHERE walkforward_id=?', (desired, error, identifier))

    def record(self, identifier, ordinal, receipt):
        self.db.execute('UPDATE walkforward_folds SET receipt=?,receipt_sha256=? WHERE walkforward_id=? AND ordinal=? AND receipt IS NULL',
                        (canonical(receipt), digest(receipt), identifier, ordinal))
