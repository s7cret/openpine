"""SQLite 3.54.1 metadata. The coordinator owns short, serialized transactions."""

import threading
from contextlib import contextmanager
from pathlib import Path

import apsw

from packages.contracts.wire import canonical, loads

SQLITE_VERSION = "3.54.1"
SCHEMA_VERSION = 4

# SQL stays in Python so the source/installed wheel have the same migrations.
MIGRATIONS = ("""
CREATE TABLE jobs (job_id TEXT PRIMARY KEY, status TEXT NOT NULL, created_at REAL NOT NULL,
 state BLOB NOT NULL, request BLOB NOT NULL, attempt_id TEXT, result_id TEXT);
CREATE INDEX jobs_ready ON jobs(status, created_at, job_id);
CREATE INDEX jobs_recent ON jobs(created_at DESC, job_id);
CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(job_id),
 number INTEGER NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL, heartbeat REAL,
 finished_at REAL, error BLOB, UNIQUE(job_id, number));
CREATE INDEX attempts_job ON attempts(job_id, number);
CREATE TABLE datasets (snapshot_id TEXT PRIMARY KEY, manifest BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE coverage (id INTEGER PRIMARY KEY, namespace TEXT NOT NULL, symbol TEXT NOT NULL,
 timeframe TEXT NOT NULL, start_ms INTEGER NOT NULL, end_ms INTEGER NOT NULL,
 snapshot_id TEXT REFERENCES datasets(snapshot_id), CHECK(start_ms<end_ms));
CREATE INDEX coverage_lookup ON coverage(namespace,symbol,timeframe,start_ms,end_ms);
CREATE TABLE results (result_id TEXT PRIMARY KEY, manifest BLOB NOT NULL, created_at REAL NOT NULL);
CREATE TABLE imports (path TEXT PRIMARY KEY, job_id TEXT, error TEXT);
""", """
CREATE TABLE run_plans (job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
 plan_id TEXT NOT NULL, plan BLOB NOT NULL);
CREATE TRIGGER immutable_run_plans BEFORE UPDATE ON run_plans
 BEGIN SELECT RAISE(ABORT,'Run plans are immutable'); END;
CREATE TABLE selection_cache (cache_key TEXT PRIMARY KEY,
 snapshot_id TEXT NOT NULL REFERENCES datasets(snapshot_id) ON DELETE CASCADE);
CREATE TABLE pins (name TEXT PRIMARY KEY, object_id TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE settings (name TEXT PRIMARY KEY, value BLOB NOT NULL);
ALTER TABLE coverage ADD COLUMN created_at REAL NOT NULL DEFAULT 0;
""", """
CREATE TABLE checkpoints (job_id TEXT PRIMARY KEY REFERENCES jobs(job_id) ON DELETE CASCADE,
 checkpoint_id TEXT NOT NULL, completed INTEGER NOT NULL CHECK(completed>0),
 attempt_id TEXT NOT NULL, published_at REAL NOT NULL);
""", """
CREATE TABLE replays (replay_id TEXT PRIMARY KEY, config_ref TEXT NOT NULL,
 checkpoint_ref TEXT, applied INTEGER NOT NULL DEFAULT -1, accepted INTEGER NOT NULL DEFAULT 0,
 input_chain TEXT NOT NULL, state BLOB NOT NULL);
CREATE TABLE replay_events (replay_id TEXT NOT NULL REFERENCES replays(replay_id),
 sequence INTEGER NOT NULL, event_sha TEXT NOT NULL, chain TEXT NOT NULL, payload BLOB,
 PRIMARY KEY(replay_id,sequence)) WITHOUT ROWID;
CREATE TABLE replay_parts (replay_id TEXT NOT NULL REFERENCES replays(replay_id),
 first_sequence INTEGER NOT NULL, rows INTEGER NOT NULL, object_id TEXT NOT NULL,
 PRIMARY KEY(replay_id,first_sequence)) WITHOUT ROWID;
""")


class Database:
    def __init__(self, path):
        if apsw.sqlitelibversion() != SQLITE_VERSION:
            raise RuntimeError(f"SQLite {SQLITE_VERSION} required, found {apsw.sqlitelibversion()}.")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = apsw.Connection(str(self.path))
        self.connection.set_busy_timeout(5000)
        self.connection.execute("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL")
        version = self.connection.pragma("user_version")
        if version > SCHEMA_VERSION:
            self.close()
            raise ValueError("Storage schema belongs to a newer OpenPine installation.")
        for index in range(version, SCHEMA_VERSION):
            with self.transaction():
                self.connection.execute(MIGRATIONS[index])
                self.connection.pragma("user_version", index + 1)

    @contextmanager
    def transaction(self):
        with self.lock:
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self
                self.connection.execute("COMMIT")
            except BaseException:
                if not self.connection.get_autocommit():
                    self.connection.execute("ROLLBACK")
                raise

    def execute(self, sql, bindings=()):
        with self.lock:
            return list(self.connection.execute(sql, bindings))

    def get(self, job_id):
        rows = self.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,))
        if not rows:
            raise KeyError(job_id)
        return loads(rows[0][0])

    def list(self, limit=100):
        return [loads(row[0]) for row in self.execute(
            "SELECT state FROM jobs ORDER BY created_at DESC,job_id LIMIT ?", (limit,))]

    def active(self):
        return [loads(row[0]) for row in self.execute(
            "SELECT state FROM jobs WHERE status IN ('queued','running') ORDER BY created_at,job_id")]

    def save(self, state):
        self.execute("UPDATE jobs SET status=?,state=?,attempt_id=?,result_id=? WHERE job_id=?",
            (state["status"], canonical(state), state.get("attempt_id"), state.get("result_id"), state["job_id"]))

    def check(self):
        if self.execute("PRAGMA integrity_check") != [("ok",)] or self.execute("PRAGMA foreign_key_check"):
            raise ValueError("Storage integrity check failed.")

    def backup(self, path):
        target = apsw.Connection(str(path))
        try:
            with self.lock, target.backup("main", self.connection, "main") as backup:
                while not backup.step(256):
                    pass
        finally:
            target.close()

    def close(self):
        with self.lock:
            self.connection.close()
