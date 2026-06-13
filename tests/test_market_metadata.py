from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import accounts_data


def test_data_metadata_exposes_exchange_catalog_with_openpine_support_flags():
    payload = asyncio.run(accounts_data.data_metadata())

    exchanges = {item['id']: item for item in payload['exchanges']}
    assert len(exchanges) == 10
    assert {'binance', 'bybit', 'okx', 'coinbase', 'kraken', 'kucoin', 'bitget', 'gateio', 'htx', 'mexc'} <= set(exchanges)

    binance = exchanges['binance']
    assert binance['name'] == 'Binance'
    assert binance['status'] == 'native'
    assert binance['native_adapter'] is True
    assert binance['openpine_enabled'] is True
    assert binance['symbol_search_supported'] is True
    assert [mt['id'] for mt in binance['market_types']] == ['spot', 'margin', 'futures', 'delivery']

    bybit = exchanges['bybit']
    assert bybit['native_adapter'] is True
    assert bybit['openpine_enabled'] is True
    assert bybit['symbol_search_supported'] is True
    assert [mt['id'] for mt in bybit['market_types']] == ['spot', 'futures', 'delivery']
    assert bybit['disabled_reason'] is None

    expected_market_types = {
        'okx': ['spot', 'margin', 'futures', 'delivery'],
        'coinbase': ['spot'],
        'kraken': ['spot', 'margin', 'futures', 'delivery'],
        'kucoin': ['spot', 'margin', 'futures', 'delivery'],
        'bitget': ['spot', 'margin', 'futures', 'delivery'],
        'gateio': ['spot', 'margin', 'futures', 'delivery'],
        'htx': ['spot', 'margin', 'futures', 'delivery'],
        'mexc': ['spot', 'futures', 'delivery'],
    }
    for exchange_id, market_type_ids in expected_market_types.items():
        exchange = exchanges[exchange_id]
        assert exchange['status'] == 'native'
        assert exchange['native_adapter'] is True
        assert exchange['openpine_enabled'] is True
        assert exchange['symbol_search_supported'] is True
        assert exchange['disabled_reason'] is None
        assert [mt['id'] for mt in exchange['market_types']] == market_type_ids


def test_data_metadata_market_type_mapping_is_deduped_and_labeled():
    payload = accounts_data._market_metadata_payload()

    market_types = {item['id']: item for item in payload['market_types']}
    assert market_types['futures']['aliases'] == ['futures', 'usdm', 'linear', 'usdt_futures']
    assert market_types['delivery']['aliases'] == ['delivery', 'coinm', 'inverse', 'coin_futures', 'delivery_futures']
    assert market_types['options']['enabled_for_strategy_create'] is False

    gateio = next(item for item in payload['exchanges'] if item['id'] == 'gateio')
    assert [mt['id'] for mt in gateio['market_types']] == ['spot', 'margin', 'futures', 'delivery']


def test_data_symbols_runs_discovery_in_thread_with_bounded_timeout(monkeypatch):
    calls: dict[str, object] = {}

    async def fake_to_thread(fn, /, *args, **kwargs):
        calls['fn'] = fn
        calls['args'] = args
        calls['kwargs'] = kwargs
        return [SimpleNamespace(to_dict=lambda: {'symbol': 'BTCUSDT'})]

    sentinel_search = object()
    monkeypatch.setattr(accounts_data.asyncio, 'to_thread', fake_to_thread)
    monkeypatch.setattr(accounts_data, 'search_symbols', sentinel_search)
    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_stable_quote_assets=('USDT', 'USDC'),
            marketdata_stable_quotes_only=True,
            marketdata_symbol_search_limit=7,
        )
    )

    payload = asyncio.run(accounts_data.data_symbols('binance', 'spot', 'btc', state))

    assert payload['symbols'] == [{'symbol': 'BTCUSDT'}]
    assert calls['fn'] is sentinel_search
    assert calls['args'] == ()
    assert calls['kwargs'] == {
        'exchange': 'binance',
        'market': 'spot',
        'query': 'btc',
        'stable_quotes_only': True,
        'stable_quote_assets': ('USDT', 'USDC'),
        'limit': 7,
        'timeout': 8.0,
    }


def test_data_symbols_returns_gateway_timeout_for_stuck_provider_thread(monkeypatch):
    async def slow_to_thread(fn, /, *args, **kwargs):
        del fn, args, kwargs
        await asyncio.sleep(0.05)
        return []

    monkeypatch.setattr(accounts_data.asyncio, 'to_thread', slow_to_thread)
    monkeypatch.setattr(accounts_data, '_SYMBOL_SEARCH_RESPONSE_TIMEOUT_SECONDS', 0.001)
    state = SimpleNamespace(
        config=SimpleNamespace(
            marketdata_stable_quote_assets=('USDT',),
            marketdata_stable_quotes_only=True,
            marketdata_symbol_search_limit=7,
        )
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(accounts_data.data_symbols('binance', 'spot', 'btc', state))

    assert exc.value.status_code == 504
    assert exc.value.detail == 'Symbol discovery timed out'


def test_data_klines_returns_gateway_timeout_for_stuck_provider_thread(monkeypatch):
    async def slow_to_thread(fn, /, *args, **kwargs):
        del fn, args, kwargs
        await asyncio.sleep(0.05)
        return ('binance', 'BTCUSDT', None, [])

    monkeypatch.setattr(accounts_data.asyncio, 'to_thread', slow_to_thread)
    monkeypatch.setattr(accounts_data, '_DATA_LOAD_RESPONSE_TIMEOUT_SECONDS', 0.001)
    state = SimpleNamespace()

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            accounts_data.data_klines(
                symbol='BTCUSDT',
                start_time=0,
                end_time=60_000,
                exchange='binance',
                market_type='spot',
                interval='1m',
                limit=5,
                state=state,
            )
        )

    assert exc.value.status_code == 504
    assert exc.value.detail == 'Market data load timed out'


def test_data_metadata_keeps_planned_reason_for_non_native_catalog_entries():
    assert accounts_data._exchange_disabled_reason({'native_adapter': False}) == 'planned_provider'
