import unittest

from packages.contracts import OpenPineError
from packages.contracts.marketdata import DataQuery
from packages.marketdata.binance import BinanceProvider
from packages.marketdata.providers import snapshot_from_payload, snapshot_payload


def kline(t, close_time=None):
    return [t, "9", "12", "8", "10", "100", t + 59999 if close_time is None else close_time, "0", 1, "0", "0", "0"]


class BinanceTests(unittest.TestCase):
    def test_pagination_half_open_gap_and_offline_snapshot(self):
        calls = []
        def transport(path, params):
            calls.append((path, params))
            if path.endswith("time"):
                return {"serverTime": 999999}
            return [kline(0), kline(120000)] if params["startTime"] == 0 else [kline(180000)]
        snapshot = BinanceProvider(transport=transport, page_limit=2).fetch(DataQuery("BINANCE:BTCUSDT", "1", 0, 240000))
        self.assertEqual([b.time for b in snapshot.data.bars], [0, 120000, 180000])
        self.assertEqual(calls[2][1]["startTime"], 120001)
        self.assertEqual(calls[1][1]["endTime"], 239999)
        self.assertEqual(snapshot_from_payload(snapshot_payload(snapshot)), snapshot)

    def test_actual_close_time_excludes_unconfirmed_monthly_bar(self):
        def transport(path, params):
            return {"serverTime": 150000} if path.endswith("time") else [kline(0, 100000), kline(120000, 999999)]
        data = BinanceProvider(transport=transport).fetch(DataQuery("BINANCE:BTCUSDT", "M", 0, 140000)).data
        self.assertEqual([b.time for b in data.bars], [0])

    def test_no_partial_success_on_later_page_failure(self):
        def transport(path, params):
            if path.endswith("time"):
                return {"serverTime": 999999}
            if params["startTime"] == 0:
                return [kline(0)]
            raise OSError("network lost")
        with self.assertRaises(OSError):
            BinanceProvider(transport=transport, page_limit=1).fetch(DataQuery("BINANCE:BTCUSDT", "1", 0, 180000))

    def test_bad_rows_order_prices_and_future_range_rejected(self):
        for page in ([kline(60000), kline(0)], [kline(0), kline(0)], [[False, *kline(0)[1:]]],
                     [[0, "NaN", *kline(0)[2:]]], [[0, "99", *kline(0)[2:]]]):
            with self.subTest(page=page), self.assertRaises(OpenPineError):
                BinanceProvider(transport=lambda path, p: {"serverTime": 999999} if path.endswith("time") else page).fetch(DataQuery("BINANCE:BTCUSDT", "1", 0, 120000))
        with self.assertRaises(OpenPineError):
            BinanceProvider(transport=lambda *args: {"serverTime": 1}).fetch(DataQuery("BINANCE:BTCUSDT", "1", 0, 2))
        with self.assertRaises(OpenPineError):
            BinanceProvider().fetch(DataQuery("other:BTCUSDT", "1", 0, 2))
