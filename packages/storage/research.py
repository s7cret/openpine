"""Integrity checked, disposable cache of metrics over immutable results."""
from packages.contracts.wire import canonical, loads, digest


class ResearchMetricRepository:
    def __init__(self, db):
        self.db = db

    def get(self, result_id, implementation):
        rows = self.db.execute('SELECT payload,sha256 FROM research_metrics WHERE result_id=? AND implementation=?',
                               (result_id, implementation))
        if not rows:return None
        value = loads(rows[0][0])
        if digest(value) != rows[0][1]:raise ValueError('Research metric checksum differs.')
        return value

    def put(self, result_id, implementation, value):
        self.db.execute('INSERT OR IGNORE INTO research_metrics VALUES(?,?,?,?)',
                        (result_id, implementation, canonical(value), digest(value)))
        if self.get(result_id, implementation) != value:raise ValueError('Research metric cache conflict.')
