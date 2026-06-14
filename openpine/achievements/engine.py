"""Achievement engine — compute, cache, unlock.

Two phases per refresh:

1. ``recompute_stats()`` — rebuild ``achievement_stats`` from authoritative
   source tables (candle_manifests, orders, strategy_instances, …). This
   is the "self-heal" path: even if event hooks miss updates, a periodic
   recompute brings counters back in line with reality.

2. ``check_unlocks()`` — for each metric that changed, compare its current
   value against every achievement's ``target_value`` and insert
   ``achievement_unlocks`` rows for any newly-met targets (idempotent on
   the composite primary key).

Plus ``get_state()`` — read-only snapshot for the API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from openpine._compat import structlog
from openpine.storage.sqlite_storage import SQLiteStorage

log = structlog.get_logger(__name__)


# SQL fragments that compute a metric from the source-of-truth tables.
# Each returns a single scalar value (or NULL → treated as 0). Missing
# source columns (e.g. bars_per_sec on backtest_runs) are kept out of
# the dict below and the engine's recompute_stats() will simply skip
# them via try/except — graceful degradation by design.
_METRIC_SQL: dict[str, str] = {
    # Data lake
    "bars_loaded":  "SELECT COALESCE(SUM(row_count), 0) FROM candle_manifests",
    "bars_cached":  "SELECT COALESCE(SUM(row_count), 0) FROM candle_manifests",
    # Orders / fills
    "trades":       "SELECT COUNT(*) FROM orders WHERE status IN ('filled','closed')",
    "symbols":      "SELECT COUNT(DISTINCT symbol) FROM orders",
    "exchanges":    "SELECT COUNT(DISTINCT COALESCE(account_id, symbol)) FROM orders",
    # Strategies
    "strategies":   "SELECT COUNT(*) FROM strategy_instances",
    "udt_strategies": "SELECT COUNT(*) FROM strategy_instances",
    # Backtest outcomes — pnl_peak is the best-ever pct return across
    # all completed runs. max_drawdown_pct is stored as a positive
    # number (the drawdown), so the achievement target is the *lowest*
    # drawdown achieved (we invert the comparison in the catalog).
    "backtests":     "SELECT COUNT(*) FROM backtest_runs WHERE status='done'",
    "parity_matches": "SELECT COUNT(*) FROM backtest_runs WHERE status='done'",
    "pnl_peak_pct":  "SELECT COALESCE(MAX(net_profit_pct), 0) FROM backtest_runs WHERE status='done'",
    "max_drawdown_pct": "SELECT COALESCE(MIN(max_drawdown_pct), 100) FROM backtest_runs WHERE status='done' AND max_drawdown_pct IS NOT NULL",
    "sharpe":        "SELECT COALESCE(MAX(sharpe), 0) FROM backtest_runs WHERE status='done' AND sharpe IS NOT NULL",
    "winrate_pct":   "SELECT COALESCE(MAX(win_rate), 0) * 100 FROM backtest_runs WHERE status='done' AND win_rate IS NOT NULL",
    # Live uptime: longest single-session uptime (in hours) for any
    # strategy that has been enabled and was running. We approximate
    # by (updated_at - created_at) for any non-pending instance.
    "live_uptime_h": (
        "SELECT COALESCE(MAX((updated_at - created_at) / 3600.0), 0) "
        "FROM strategy_instances WHERE status NOT IN ('pending','disabled')"
    ),
    # AST node count: source column does not exist yet. We expose a
    # 0-valued entry so the achievement engine wiring is in place;
    # the migration that adds ast_node_count to pine_artifacts can
    # update this SQL without touching the engine.
    "ast_nodes":     "SELECT 0",
}


@dataclass(frozen=True)
class AchievementState:
    """Snapshot of one achievement's progress + unlock status."""
    id: str
    tier: str
    icon: str
    title: str
    description: str
    metric: str
    target: float
    current: float
    reward: str
    hidden: bool
    unlocked: bool
    unlocked_at: int | None
    sort_order: int


class AchievementEngine:
    """Stat recompute + unlock detector + snapshot reader."""

    def __init__(self, storage: SQLiteStorage) -> None:
        self.storage = storage

    # ── Phase 1: recompute derived stats ─────────────────
    def recompute_stats(self) -> dict[str, float]:
        """Rebuild ``achievement_stats`` from source tables.

        Returns a {metric: value} dict for what was actually written.
        Missing source tables (e.g. a metric hasn't been wired yet) are
        logged and skipped — they keep their previous value.
        """
        results: dict[str, float] = {}
        now = int(time.time())
        for metric, sql in _METRIC_SQL.items():
            try:
                row = self.storage.execute(sql).fetchone()
            except Exception as exc:  # table/column may not exist yet
                log.debug("achievement_metric_skip", metric=metric, error=str(exc))
                continue
            value = float(row[0]) if row and row[0] is not None else 0.0
            self.storage.execute(
                """
                INSERT INTO achievement_stats(metric, value, last_event_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(metric) DO UPDATE SET
                    value = excluded.value,
                    last_event_at = excluded.last_event_at,
                    updated_at = excluded.updated_at
                """,
                (metric, value, now, now),
            )
            results[metric] = value
        self.storage.commit()
        log.info("achievement_stats_recomputed", count=len(results))
        return results

    # ── Phase 2: unlock check ────────────────────────────
    def check_unlocks(self, stats: dict[str, float]) -> list[str]:
        """For each metric in ``stats``, find any newly-met targets.

        Returns the list of achievement ids that were just unlocked.
        """
        unlocked: list[str] = []
        now = int(time.time())
        for metric, value in stats.items():
            try:
                rows = self.storage.execute(
                    """
                    SELECT a.id, a.target_value
                    FROM achievements a
                    WHERE a.metric = ?
                      AND a.id NOT IN (
                          SELECT achievement_id FROM achievement_unlocks
                          WHERE user_id IS NULL
                      )
                    """,
                    (metric,),
                ).fetchall()
            except Exception as exc:
                log.debug("achievement_unlock_query_fail", metric=metric, error=str(exc))
                continue
            for ach_id, target in rows:
                if value >= float(target):
                    try:
                        self.storage.execute(
                            """
                            INSERT OR IGNORE INTO achievement_unlocks(
                                achievement_id, user_id, unlocked_at, final_value
                            ) VALUES (?, NULL, ?, ?)
                            """,
                            (ach_id, now, value),
                        )
                        unlocked.append(ach_id)
                    except Exception as exc:
                        log.warning("achievement_unlock_insert_fail", id=ach_id, error=str(exc))
        if unlocked:
            self.storage.commit()
            log.info("achievements_unlocked", ids=unlocked)
        return unlocked

    # ── Read API ─────────────────────────────────────────
    _TIER_ORDER_SQL = (
        "CASE a.tier "
        "WHEN 'pro' THEN 0 "
        "WHEN 'ultra' THEN 1 "
        "WHEN 'hyper' THEN 2 "
        "WHEN 'apex' THEN 3 "
        "ELSE 4 END"
    )

    def get_state(self, include_hidden_locked: bool = False) -> list[AchievementState]:
        """Snapshot for the API. Hidden achievements that are still locked
        are dropped unless ``include_hidden_locked`` is True (the UI uses
        the default; tests use the override)."""
        rows = self.storage.execute(
            f"""
            SELECT
                a.id, a.tier, a.icon, a.title, a.description, a.metric,
                a.target_value, a.reward, a.hidden, a.sort_order,
                COALESCE(s.value, 0) AS current_value,
                u.unlocked_at
            FROM achievements a
            LEFT JOIN achievement_stats s ON s.metric = a.metric
            LEFT JOIN achievement_unlocks u
                ON u.achievement_id = a.id AND u.user_id IS NULL
            ORDER BY {self._TIER_ORDER_SQL}, a.sort_order, a.id
            """
        ).fetchall()

        out: list[AchievementState] = []
        for r in rows:
            (
                ach_id, tier, icon, title, descr, metric,
                target, reward, hidden, sort, current, unlocked_at,
            ) = r
            is_locked_secret = bool(hidden) and unlocked_at is None
            if is_locked_secret and not include_hidden_locked:
                continue
            out.append(
                AchievementState(
                    id=ach_id,
                    tier=tier,
                    icon=icon,
                    title=title,
                    description=descr,
                    metric=metric,
                    target=float(target),
                    current=float(current),
                    reward=reward,
                    hidden=bool(hidden),
                    unlocked=unlocked_at is not None,
                    unlocked_at=int(unlocked_at) if unlocked_at is not None else None,
                    sort_order=int(sort),
                )
            )
        return out

    def summary(self) -> dict[str, Any]:
        """Aggregate counts for the UI header."""
        rows = self.storage.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN u.unlocked_at IS NOT NULL THEN 1 ELSE 0 END) AS unlocked
            FROM achievements a
            LEFT JOIN achievement_unlocks u
                ON u.achievement_id = a.id AND u.user_id IS NULL
            """
        ).fetchone()
        total = int(rows[0] or 0)
        unlocked = int(rows[1] or 0)
        by_tier_rows = self.storage.execute(
            """
            SELECT a.tier,
                   COUNT(*) AS of,
                   SUM(CASE WHEN u.unlocked_at IS NOT NULL THEN 1 ELSE 0 END) AS done
            FROM achievements a
            LEFT JOIN achievement_unlocks u
                ON u.achievement_id = a.id AND u.user_id IS NULL
            GROUP BY a.tier
            """
        ).fetchall()
        by_tier = {
            tier: {"done": int(done or 0), "of": int(of)}
            for (tier, of, done) in by_tier_rows
        }
        return {"total": total, "unlocked": unlocked, "by_tier": by_tier}

    # ── One-shot refresh (used by background tick) ───────
    def refresh(self) -> dict[str, Any]:
        """Run recompute + unlock check. Returns summary dict for logging."""
        stats = self.recompute_stats()
        unlocked = self.check_unlocks(stats)
        return {"stats_computed": len(stats), "newly_unlocked": unlocked}
