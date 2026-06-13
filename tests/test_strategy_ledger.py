from __future__ import annotations

from openpine.storage import MigrationRunner, SQLiteStorage
from openpine.storage.strategy_ledger import (
    LedgerSource,
    PositionSide,
    StrategyLedger,
    StrategyPosition,
    StrategyTrade,
    TradeStatus,
)


def _storage(tmp_path):
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    MigrationRunner().run_migrations(storage)
    return storage


def test_strategy_ledger_separates_history_and_paper_trades(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        ledger = StrategyLedger(storage)

        ledger.record_trade(
            StrategyTrade(
                trade_id="trade-history",
                strategy_id="strategy-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
                source=LedgerSource.HISTORY,
                status=TradeStatus.CLOSED,
                direction="long",
                entry_time=1,
                exit_time=2,
                entry_price=100.0,
                exit_price=110.0,
                qty=0.1,
                net_pnl=1.0,
            )
        )
        ledger.record_trade(
            StrategyTrade(
                trade_id="trade-paper",
                strategy_id="strategy-1",
                order_id="order-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
                source=LedgerSource.PAPER,
                status=TradeStatus.CLOSED,
                direction="short",
                entry_time=3,
                exit_time=4,
                entry_price=110.0,
                exit_price=100.0,
                qty=0.2,
                net_pnl=2.0,
            )
        )

        assert [
            trade.trade_id for trade in ledger.list_trades(source=LedgerSource.HISTORY)
        ] == ["trade-history"]
        assert [
            trade.trade_id for trade in ledger.list_trades(source=LedgerSource.PAPER)
        ] == ["trade-paper"]
        assert (
            storage.execute("SELECT count(*) FROM backtest_trades").fetchone()[0] == 0
        )
        assert storage.execute("SELECT count(*) FROM orders").fetchone()[0] == 0
    finally:
        storage.close()


def test_strategy_ledger_upserts_current_position(tmp_path) -> None:
    storage = _storage(tmp_path)
    try:
        ledger = StrategyLedger(storage)

        first = ledger.upsert_position(
            StrategyPosition(
                strategy_id="strategy-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
                source=LedgerSource.HISTORY,
                side=PositionSide.LONG,
                qty=0.1,
                avg_price=100.0,
                last_bar_time=10,
            )
        )
        second = ledger.upsert_position(
            StrategyPosition(
                strategy_id="strategy-1",
                exchange="binance",
                market_type="spot",
                symbol="BTCUSDT",
                timeframe="15m",
                source=LedgerSource.PAPER,
                side=PositionSide.FLAT,
                qty=0.0,
                avg_price=None,
                realized_pnl=1.5,
                last_bar_time=20,
            )
        )

        loaded = ledger.get_position(
            strategy_id="strategy-1",
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="15m",
        )

        assert loaded is not None
        assert second.position_id == first.position_id
        assert loaded.side == PositionSide.FLAT
        assert loaded.source == LedgerSource.PAPER
        assert loaded.realized_pnl == 1.5
        assert loaded.last_bar_time == 20
        assert (
            storage.execute("SELECT count(*) FROM strategy_positions").fetchone()[0]
            == 1
        )
    finally:
        storage.close()
