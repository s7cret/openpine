from __future__ import annotations

from openpine import exchange_metadata


def test_binance_spot_default_qty_step_uses_lot_size(monkeypatch) -> None:
    payload = {
        "symbols": [
            {
                "symbol": "XLMUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.00001000"},
                    {"filterType": "LOT_SIZE", "stepSize": "1.00000000"},
                ],
            }
        ]
    }
    monkeypatch.setattr(exchange_metadata, "_load_binance_spot_exchange_info", lambda: payload)

    assert exchange_metadata.default_qty_step("binance", "spot", "xlmusdt") == 1.0
    assert exchange_metadata.default_qty_rounding_mode("binance", "spot", "XLMUSDT") == "truncate"


def test_default_qty_step_ignores_non_binance_spot(monkeypatch) -> None:
    monkeypatch.setattr(exchange_metadata, "_load_binance_spot_exchange_info", lambda: None)

    assert exchange_metadata.default_qty_step("coinbase", "spot", "BTCUSD") is None
    assert exchange_metadata.default_qty_step("binance", "futures", "BTCUSDT") is None
