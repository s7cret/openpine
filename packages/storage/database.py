"""SQLite 3.54.1 metadata. The coordinator owns short, serialized transactions."""

import threading
from contextlib import contextmanager
from pathlib import Path

import apsw

from packages.contracts.wire import canonical, loads, digest

SQLITE_VERSION = "3.54.1"
SCHEMA_VERSION = 12

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
""", """
CREATE TABLE market_instruments (instrument_key TEXT PRIMARY KEY, metadata BLOB NOT NULL);
CREATE TABLE data_tasks (task_id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
 status TEXT NOT NULL, request BLOB NOT NULL, state BLOB NOT NULL, created_at REAL NOT NULL);
CREATE INDEX data_tasks_ready ON data_tasks(status,created_at);
CREATE UNIQUE INDEX data_tasks_singleflight ON data_tasks(request_key)
 WHERE status IN ('queued','running','interrupted');
CREATE TABLE data_pages (task_id TEXT NOT NULL REFERENCES data_tasks(task_id),
 cursor INTEGER NOT NULL, stop INTEGER NOT NULL, snapshot_id TEXT REFERENCES datasets(snapshot_id),
 PRIMARY KEY(task_id,cursor)) WITHOUT ROWID;
CREATE TABLE live_feeds (feed_id TEXT PRIMARY KEY, config BLOB NOT NULL, state BLOB NOT NULL);
CREATE TABLE live_observations (feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id),
 sequence INTEGER NOT NULL, payload BLOB, sha256 TEXT NOT NULL,
 PRIMARY KEY(feed_id,sequence)) WITHOUT ROWID;
CREATE TABLE live_parts (feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id),
 first_sequence INTEGER NOT NULL, rows INTEGER NOT NULL, object_id TEXT NOT NULL,
 PRIMARY KEY(feed_id,first_sequence)) WITHOUT ROWID;
CREATE TABLE live_closed (feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id),
 open_time INTEGER NOT NULL, sha256 TEXT NOT NULL, bar BLOB,
 snapshot_id TEXT REFERENCES datasets(snapshot_id), row_offset INTEGER,
 PRIMARY KEY(feed_id,open_time)) WITHOUT ROWID;
CREATE INDEX live_closed_pending ON live_closed(feed_id,open_time) WHERE bar IS NOT NULL;
CREATE TABLE live_sessions (session_id TEXT PRIMARY KEY, feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id),
 replay_id TEXT NOT NULL REFERENCES replays(replay_id), config BLOB NOT NULL, state BLOB NOT NULL,
 desired TEXT NOT NULL DEFAULT 'running');
CREATE INDEX live_sessions_feed ON live_sessions(feed_id);
CREATE INDEX live_sessions_active ON live_sessions(desired,session_id);
""", """
CREATE TABLE live_series (series_key TEXT PRIMARY KEY,
 feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id));
""", """
CREATE TABLE live_bindings (session_id TEXT NOT NULL REFERENCES live_sessions(session_id) ON DELETE CASCADE,
 context_id TEXT NOT NULL, feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id),
 PRIMARY KEY(session_id,context_id));
CREATE INDEX live_bindings_feed ON live_bindings(feed_id,session_id);
INSERT INTO live_bindings SELECT session_id,'primary',feed_id FROM live_sessions;
CREATE TABLE live_arrivals (arrival INTEGER PRIMARY KEY AUTOINCREMENT,
 feed_id TEXT NOT NULL REFERENCES live_feeds(feed_id), source_sequence INTEGER NOT NULL,
 UNIQUE(feed_id,source_sequence));
INSERT INTO live_arrivals(feed_id,source_sequence)
 SELECT feed_id,sequence FROM live_observations ORDER BY feed_id,sequence;
""", """
CREATE INDEX datasets_created ON datasets(created_at DESC,snapshot_id);
CREATE INDEX data_tasks_snapshot ON data_tasks(json_extract(CAST(state AS TEXT),'$.snapshot_id'),created_at DESC);
CREATE INDEX coverage_snapshot ON coverage(snapshot_id);
""", """
CREATE TABLE input_presets (preset_id TEXT PRIMARY KEY, schema_sha256 TEXT NOT NULL,
 name TEXT NOT NULL, input_ref TEXT NOT NULL, settings BLOB NOT NULL,
 revision INTEGER NOT NULL, updated_at REAL NOT NULL, UNIQUE(schema_sha256,name));
CREATE INDEX input_presets_schema ON input_presets(schema_sha256,updated_at DESC);
CREATE TABLE experiments (experiment_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
 desired TEXT NOT NULL CHECK(desired IN ('running','paused','cancelled')), manifest BLOB NOT NULL,
 manifest_sha256 TEXT NOT NULL, error TEXT);
CREATE TRIGGER immutable_experiments BEFORE UPDATE OF manifest,manifest_sha256 ON experiments
 BEGIN SELECT RAISE(ABORT,'Experiment manifest is immutable'); END;
CREATE TABLE experiment_variants (experiment_id TEXT NOT NULL REFERENCES experiments(experiment_id),
 ordinal INTEGER NOT NULL, candidate_id TEXT NOT NULL UNIQUE, request BLOB NOT NULL,
 plan BLOB NOT NULL, plan_sha256 TEXT NOT NULL,
 job_id TEXT UNIQUE REFERENCES jobs(job_id), PRIMARY KEY(experiment_id,ordinal));
CREATE TRIGGER immutable_experiment_variants BEFORE UPDATE OF experiment_id,ordinal,candidate_id,request,plan,plan_sha256 ON experiment_variants
 BEGIN SELECT RAISE(ABORT,'Experiment variant is immutable'); END;
CREATE TRIGGER immutable_experiment_link BEFORE UPDATE OF job_id ON experiment_variants
 WHEN OLD.job_id IS NOT NULL AND NEW.job_id IS NOT OLD.job_id
 BEGIN SELECT RAISE(ABORT,'Experiment job link is immutable'); END;
CREATE INDEX experiments_ready ON experiments(desired,created_at,experiment_id);
CREATE INDEX experiment_variants_job ON experiment_variants(job_id);
""", """
CREATE TABLE searches (search_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
 desired TEXT NOT NULL CHECK(desired IN ('running','paused','cancelled')), manifest BLOB NOT NULL,
 manifest_sha256 TEXT NOT NULL, selection_id TEXT NOT NULL UNIQUE REFERENCES experiments(experiment_id),
 holdout_id TEXT UNIQUE REFERENCES experiments(experiment_id), decision BLOB, decision_sha256 TEXT, error TEXT);
CREATE INDEX searches_ready ON searches(desired,created_at,search_id);
CREATE TRIGGER immutable_search BEFORE UPDATE OF manifest,manifest_sha256,selection_id ON searches
 BEGIN SELECT RAISE(ABORT,'Search composition is immutable'); END;
CREATE TRIGGER immutable_search_decision BEFORE UPDATE OF decision,decision_sha256 ON searches
 WHEN OLD.decision IS NOT NULL AND (NEW.decision IS NOT OLD.decision OR NEW.decision_sha256 IS NOT OLD.decision_sha256)
 BEGIN SELECT RAISE(ABORT,'Search selection is immutable'); END;
CREATE TRIGGER immutable_search_holdout BEFORE UPDATE OF holdout_id ON searches
 WHEN OLD.holdout_id IS NOT NULL AND NEW.holdout_id IS NOT OLD.holdout_id
 BEGIN SELECT RAISE(ABORT,'Search holdout link is immutable'); END;
CREATE TABLE search_holdouts (search_id TEXT NOT NULL REFERENCES searches(search_id), ordinal INTEGER NOT NULL,
 request BLOB NOT NULL, plan BLOB NOT NULL, plan_sha256 TEXT NOT NULL, PRIMARY KEY(search_id,ordinal));
CREATE TRIGGER immutable_search_holdouts BEFORE UPDATE ON search_holdouts
 BEGIN SELECT RAISE(ABORT,'Holdout plan is immutable'); END;
CREATE TABLE search_assessments (search_id TEXT NOT NULL REFERENCES searches(search_id),
 phase TEXT NOT NULL CHECK(phase IN ('selection','holdout')), ordinal INTEGER NOT NULL,
 job_id TEXT NOT NULL REFERENCES jobs(job_id), result_id TEXT, assessment BLOB NOT NULL,
 assessment_sha256 TEXT NOT NULL, PRIMARY KEY(search_id,phase,ordinal));
CREATE TRIGGER immutable_search_assessments BEFORE UPDATE ON search_assessments
 BEGIN SELECT RAISE(ABORT,'Search assessment is immutable'); END;
""", """
CREATE TABLE research_metrics (result_id TEXT NOT NULL REFERENCES results(result_id) ON DELETE CASCADE,
 implementation TEXT NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL,
 PRIMARY KEY(result_id,implementation));
CREATE TRIGGER immutable_research_metrics BEFORE UPDATE ON research_metrics
 BEGIN SELECT RAISE(ABORT,'Research metrics are immutable'); END;
""", """
CREATE TABLE walkforwards (walkforward_id TEXT PRIMARY KEY, created_at REAL NOT NULL,
 desired TEXT NOT NULL CHECK(desired IN ('running','paused','cancelled')),
 manifest BLOB NOT NULL, manifest_sha256 TEXT NOT NULL, error TEXT);
CREATE TRIGGER immutable_walkforward BEFORE UPDATE OF manifest,manifest_sha256 ON walkforwards
 BEGIN SELECT RAISE(ABORT,'Walk-forward composition is immutable'); END;
CREATE INDEX walkforwards_ready ON walkforwards(desired,created_at,walkforward_id);
CREATE TABLE walkforward_folds (walkforward_id TEXT NOT NULL REFERENCES walkforwards(walkforward_id),
 ordinal INTEGER NOT NULL, search_id TEXT NOT NULL UNIQUE REFERENCES searches(search_id),
 receipt BLOB, receipt_sha256 TEXT, PRIMARY KEY(walkforward_id,ordinal));
CREATE TRIGGER immutable_walkforward_fold BEFORE UPDATE OF walkforward_id,ordinal,search_id ON walkforward_folds
 BEGIN SELECT RAISE(ABORT,'Walk-forward fold is immutable'); END;
CREATE TRIGGER immutable_walkforward_receipt BEFORE UPDATE OF receipt,receipt_sha256 ON walkforward_folds
 WHEN OLD.receipt IS NOT NULL AND (NEW.receipt IS NOT OLD.receipt OR NEW.receipt_sha256 IS NOT OLD.receipt_sha256)
 BEGIN SELECT RAISE(ABORT,'Walk-forward receipt is immutable'); END;
""")


class Database:
    def __init__(self, path):
        if apsw.sqlitelibversion() != SQLITE_VERSION:
            raise RuntimeError(f"SQLite {SQLITE_VERSION} required, found {apsw.sqlitelibversion()}.")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = apsw.Connection(str(self.path))
        try:
            self.connection.set_busy_timeout(5000)
            self.connection.execute("PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL")
            version = self.connection.pragma("user_version")
            if version > SCHEMA_VERSION:
                raise ValueError("Storage schema belongs to a newer OpenPine installation.")
            for index in range(version, SCHEMA_VERSION):
                with self.transaction():
                    self.connection.execute(MIGRATIONS[index])
                    if index==5:
                        for feed_id,raw in self.execute('SELECT feed_id,config FROM live_feeds'):
                            config=loads(raw)
                            if {'instrument','timeframe','ingestion_sha256'}<=config.keys():
                                key=digest({k:config[k] for k in ('instrument','timeframe','ingestion_sha256')})
                                self.execute('INSERT OR IGNORE INTO live_series VALUES(?,?)',(key,feed_id))
                    self.connection.pragma("user_version", index + 1)
        except BaseException:
            self.close()
            raise

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
