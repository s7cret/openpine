from __future__ import annotations

import inspect
from types import SimpleNamespace

from marketdata_provider import SymbolInfo
from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe
from marketdata_provider.errors import MDSymbolUnsupported
from fastapi import HTTPException
import pytest

from openpine.config import OpenPineConfig
from openpine.gateway.routes import accounts_data
import openpine.gateway.deps as deps


def test_openpine_config_defaults_to_stable_quote_symbol_filter():
    cfg = OpenPineConfig()

    assert cfg.marketdata_stable_quotes_only is True
    assert cfg.marketdata_stable_quote_assets == ('USDT', 'USDC', 'FDUSD', 'BUSD', 'TUSD', 'USDP', 'DAI', 'USD')
    assert cfg.marketdata_symbol_search_limit == 50


def test_openpine_config_env_overrides_and_validation(monkeypatch):
    from openpine.config.loader import _apply_env_overrides

    monkeypatch.setenv('OPENPINE_MARKETDATA_STABLE_QUOTES_ONLY', 'off')
    monkeypatch.setenv('OPENPINE_MARKETDATA_STABLE_QUOTE_ASSETS', 'usdt, usdc, usdt')
    monkeypatch.setenv('OPENPINE_MARKETDATA_SYMBOL_SEARCH_LIMIT', '7')

    merged = _apply_env_overrides({})
    assert merged['marketdata_stable_quotes_only'] is False
    assert merged['marketdata_stable_quote_assets'] == ('USDT', 'USDC', 'USDT')
    assert merged['marketdata_symbol_search_limit'] == 7
    cfg = OpenPineConfig(**merged)
    assert cfg.marketdata_stable_quote_assets == ('USDT', 'USDC')
    with pytest.raises(ValueError, match='marketdata_symbol_search_limit'):
        OpenPineConfig(marketdata_symbol_search_limit=0)


def test_gateway_state_uses_canonical_marketdata_provider_not_direct_binance():
    source = inspect.getsource(deps.GatewayState.__init__)

    assert 'DirectBinanceProvider' not in source
    assert 'create_local_marketdata_provider_adapter' in source


async def _fake_data_symbols_call(monkeypatch):
    calls = []

    def fake_search_symbols(**kwargs):
        calls.append(kwargs)
        return [
            SymbolInfo(
                exchange=kwargs['exchange'],
                market=kwargs['market'],
                symbol='BTCUSDC',
                base_asset='BTC',
                quote_asset='USDC',
            )
        ]

    monkeypatch.setattr(accounts_data, 'search_symbols', fake_search_symbols)
    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_stable_quotes_only=True,
            marketdata_stable_quote_assets=('USDT', 'USDC'),
            marketdata_symbol_search_limit=25,
            data_cache_root=None,
            data_dir=None,
        )
    )

    payload = await accounts_data.data_symbols(
        exchange='bybit',
        market_type='delivery',
        query='btc',
        state=state,
    )

    assert payload['exchange'] == 'bybit'
    assert payload['market_type'] == 'delivery'
    assert payload['stable_quotes_only'] is True
    assert payload['stable_quote_assets'] == ['USDT', 'USDC']
    assert payload['symbols'][0]['symbol'] == 'BTCUSDC'
    assert calls == [
        {
            'exchange': 'bybit',
            'market': 'delivery',
            'query': 'btc',
            'stable_quotes_only': True,
            'stable_quote_assets': ('USDT', 'USDC'),
            'limit': 25,
            'timeout': 8.0,
        }
    ]


def test_data_symbols_uses_provider_search_and_stable_quote_settings(monkeypatch):
    import asyncio

    asyncio.run(_fake_data_symbols_call(monkeypatch))


def test_data_symbols_rejects_unknown_disabled_and_provider_errors(monkeypatch):
    import asyncio

    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_stable_quotes_only=True,
            marketdata_stable_quote_assets=('USDT',),
            marketdata_symbol_search_limit=5,
        )
    )
    with pytest.raises(HTTPException) as unknown:
        asyncio.run(accounts_data.data_symbols(exchange='unknown', market_type='spot', query='', state=state))
    assert unknown.value.status_code == 404

    def okx_search_symbols(**kwargs):
        return [
            SymbolInfo(
                exchange=kwargs['exchange'],
                market=kwargs['market'],
                symbol='BTC-USDT',
                base_asset='BTC',
                quote_asset='USDT',
            )
        ]

    monkeypatch.setattr(accounts_data, 'search_symbols', okx_search_symbols)
    okx_payload = asyncio.run(accounts_data.data_symbols(exchange='okx', market_type='spot', query='', state=state))
    assert okx_payload['exchange'] == 'okx'
    assert okx_payload['market_type'] == 'spot'

    with pytest.raises(HTTPException) as unsupported_market:
        asyncio.run(accounts_data.data_symbols(exchange='binance', market_type='bad', query='', state=state))
    assert unsupported_market.value.status_code == 400
    assert 'unsupported market_type' in str(unsupported_market.value.detail)

    def fail_search_symbols(**_kwargs):
        raise MDSymbolUnsupported('bad market')

    monkeypatch.setattr(accounts_data, 'search_symbols', fail_search_symbols)
    with pytest.raises(HTTPException) as provider_error:
        asyncio.run(accounts_data.data_symbols(exchange='binance', market_type='spot', query='', state=state))
    assert provider_error.value.status_code == 400
    assert provider_error.value.detail == 'bad market'


class _FakeChartOrchestrator:
    def __init__(self) -> None:
        self.queries: list[BarQuery] = []

    def load_bars(self, query: BarQuery) -> BarSeries:
        self.queries.append(query)
        bars = (
            Bar(
                instrument=InstrumentKey(exchange='bybit', market='delivery', symbol='BTCUSD'),
                timeframe=parse_timeframe('1h'),
                time=1_700_000_000_000,
                time_close=1_700_003_600_000,
                open=100.0,
                high=110.0,
                low=90.0,
                close=105.0,
                volume=2.0,
                closed=True,
            ),
            Bar(
                instrument=InstrumentKey(exchange='bybit', market='delivery', symbol='BTCUSD'),
                timeframe=parse_timeframe('1h'),
                time=1_700_003_600_000,
                time_close=1_700_007_200_000,
                open=105.0,
                high=120.0,
                low=101.0,
                close=115.0,
                volume=3.0,
                closed=True,
            ),
        )
        return BarSeries(
            query=query,
            bars=bars,
            coverage=CoverageReport(
                requested_start_ms=query.start_ms,
                requested_end_ms=query.end_ms,
                delivered_start_ms=bars[0].time,
                delivered_end_ms=bars[-1].time_close,
            ),
        )


def test_data_klines_uses_gateway_provider_not_browser_binance():
    import asyncio

    orchestrator = _FakeChartOrchestrator()
    state = SimpleNamespace(orchestrator=orchestrator)

    payload = asyncio.run(
        accounts_data.data_klines(
            exchange='bybit',
            market_type='delivery',
            symbol='btcusd',
            interval='1h',
            start_time=1_700_000_000_000,
            end_time=1_700_007_200_000,
            limit=200000,
            state=state,
        )
    )

    assert payload['exchange'] == 'bybit'
    assert payload['market_type'] == 'delivery'
    assert payload['symbol'] == 'BTCUSD'
    assert payload['interval'] == '1h'
    assert payload['bars'][0] == {
        'time': 1_700_000_000_000,
        'time_close': 1_700_003_600_000,
        'open': 100.0,
        'high': 110.0,
        'low': 90.0,
        'close': 105.0,
        'volume': 2.0,
    }
    query = orchestrator.queries[0]
    assert query.instrument.exchange == 'bybit'
    assert query.instrument.market == 'delivery'
    assert query.instrument.symbol == 'BTCUSD'
    assert query.source == 'auto'


def test_data_ticker24h_is_computed_from_provider_bars(monkeypatch):
    import asyncio

    orchestrator = _FakeChartOrchestrator()
    state = SimpleNamespace(orchestrator=orchestrator)
    monkeypatch.setattr(accounts_data.time, 'time', lambda: 1_700_007_200)

    payload = asyncio.run(
        accounts_data.data_ticker24h(
            exchange='bybit',
            market_type='delivery',
            symbol='btcusd',
            state=state,
        )
    )

    assert payload['exchange'] == 'bybit'
    assert payload['market_type'] == 'delivery'
    assert payload['symbol'] == 'BTCUSD'
    assert payload['lastPrice'] == 115.0
    assert payload['priceChangePercent'] == 15.0
    assert payload['volume'] == 5.0
    assert payload['quoteVolume'] == 555.0
    query = orchestrator.queries[0]
    assert query.instrument.exchange == 'bybit'
    assert query.timeframe.canonical == '1h'


def test_data_provider_chart_error_branches(monkeypatch):
    import asyncio

    enabled_metadata = {
        'exchanges': [
            {
                'id': 'binance',
                'openpine_enabled': True,
                'disabled_reason': None,
                'market_types': [{'id': 'spot', 'enabled_for_strategy_create': True}],
            }
        ]
    }
    monkeypatch.setattr(accounts_data, '_market_metadata_payload', lambda: enabled_metadata)
    state = SimpleNamespace(orchestrator=_FakeChartOrchestrator())

    with pytest.raises(HTTPException) as invalid_range:
        accounts_data._load_market_bars(
            state,
            exchange='binance',
            market_type='spot',
            symbol='BTCUSDT',
            interval='1m',
            start_time=2,
            end_time=1,
        )
    assert invalid_range.value.status_code == 400

    with pytest.raises(HTTPException) as empty_symbol:
        accounts_data._load_market_bars(
            state,
            exchange='binance',
            market_type='spot',
            symbol=' ',
            interval='1m',
            start_time=1,
            end_time=2,
        )
    assert empty_symbol.value.status_code == 400

    assert accounts_data._require_enabled_exchange('binance') == 'binance'
    assert accounts_data._require_enabled_market_type(
        {'market_types': [{'id': 'spot', 'enabled_for_strategy_create': True}]},
        'spot',
    ) == 'spot'
    with pytest.raises(HTTPException) as disabled_market:
        accounts_data._require_enabled_market_type(
            {'market_types': [{'id': 'options', 'enabled_for_strategy_create': False}]},
            'options',
        )
    assert disabled_market.value.status_code == 400
    assert 'market_type disabled' in str(disabled_market.value.detail)

    with pytest.raises(HTTPException) as unsupported_market:
        accounts_data._load_market_bars(
            state,
            exchange='binance',
            market_type='delivery',
            symbol='BTCUSDT',
            interval='1m',
            start_time=1,
            end_time=2,
        )
    assert unsupported_market.value.status_code == 400
    assert 'unsupported market_type' in str(unsupported_market.value.detail)

    with pytest.raises(HTTPException) as excessive_window:
        accounts_data._load_market_bars(
            state,
            exchange='binance',
            market_type='spot',
            symbol='BTCUSDT',
            interval='1m',
            start_time=0,
            end_time=120_000,
            max_bars=1,
        )
    assert excessive_window.value.status_code == 400
    assert 'request window exceeds max bars' in str(excessive_window.value.detail)

    with pytest.raises(HTTPException) as variable_window:
        accounts_data._load_market_bars(
            state,
            exchange='binance',
            market_type='spot',
            symbol='BTCUSDT',
            interval='1M',
            start_time=0,
            end_time=accounts_data._DATA_KLINES_MAX_VARIABLE_WINDOW_MS + 1,
        )
    assert variable_window.value.status_code == 400
    assert variable_window.value.detail == 'request window too large'

    month_exchange, _month_symbol, month_query, _month_bars = accounts_data._load_market_bars(
        state,
        exchange='binance',
        market_type='spot',
        symbol='BTCUSDT',
        interval='1M',
        start_time=0,
        end_time=accounts_data._DATA_KLINES_MAX_VARIABLE_WINDOW_MS,
    )
    assert month_exchange == 'binance'
    assert month_query.timeframe.duration_ms is None

    with pytest.raises(HTTPException) as unknown_exchange:
        accounts_data._require_enabled_exchange('missing')
    assert unknown_exchange.value.status_code == 404

    monkeypatch.setattr(
        accounts_data,
        '_market_metadata_payload',
        lambda: {
            'exchanges': [
                {
                    'id': 'okx',
                    'openpine_enabled': False,
                    'disabled_reason': 'planned_provider',
                }
            ]
        },
    )
    with pytest.raises(HTTPException) as disabled_exchange:
        accounts_data._require_enabled_exchange('okx')
    assert disabled_exchange.value.status_code == 400
    assert disabled_exchange.value.detail == 'planned_provider'

    monkeypatch.setattr(accounts_data, '_market_metadata_payload', lambda: enabled_metadata)

    class FailingOrchestrator:
        def __init__(self, exc: Exception) -> None:
            self.exc = exc

        def load_bars(self, query: BarQuery) -> BarSeries:
            raise self.exc

    with pytest.raises(HTTPException) as provider_error:
        accounts_data._load_market_bars(
            SimpleNamespace(orchestrator=FailingOrchestrator(MDSymbolUnsupported('bad'))),
            exchange='binance',
            market_type='spot',
            symbol='BTCUSDT',
            interval='1m',
            start_time=1,
            end_time=2,
        )
    assert provider_error.value.status_code == 400
    assert provider_error.value.detail == 'bad'

    with pytest.raises(HTTPException) as parse_error:
        accounts_data._load_market_bars(
            state,
            exchange='binance',
            market_type='spot',
            symbol='BTCUSDT',
            interval='bad',
            start_time=1,
            end_time=2,
        )
    assert parse_error.value.status_code == 400

    class EmptyOrchestrator:
        def load_bars(self, query: BarQuery) -> BarSeries:
            return BarSeries(
                query=query,
                bars=(),
                coverage=CoverageReport(
                    requested_start_ms=query.start_ms,
                    requested_end_ms=query.end_ms,
                    delivered_start_ms=None,
                    delivered_end_ms=None,
                ),
            )

    with pytest.raises(HTTPException) as no_ticker:
        asyncio.run(
            accounts_data.data_ticker24h(
                exchange='binance',
                market_type='spot',
                symbol='BTCUSDT',
                state=SimpleNamespace(orchestrator=EmptyOrchestrator()),
            )
        )
    assert no_ticker.value.status_code == 404
