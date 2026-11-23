import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.openpine.bootstrap import local_services
from packages.contracts.wire import read_json
from packages.marketdata.aggregation import aggregate_page, intrabar_page
from packages.marketdata.providers import input_snapshot


FIXTURE = Path(__file__).resolve().parents[1] / 'examples/market-schedules/short-night-ohlcv.json'
FIELDS = ('start_ms', 'end_ms', 'observed', 'expected', 'open', 'high', 'low', 'close', 'volume')


class ScheduledAggregation(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(TemporaryDirectory())
        self.market = self.enterContext(local_services(directory)).market
        self.payload = read_json(FIXTURE)

    def pin(self):
        return self.market.source(self.market.save(input_snapshot(self.payload)))

    def test_overnight_short_close_and_hole(self):
        source = self.pin()
        report = aggregate_page(source, '3')
        self.assertEqual([tuple(row[field] for field in FIELDS) for row in report['candles']], [
            (86280000, 86460000, 3, 3, 10, 18, 8, 14, 6),
            (86460000, 86640000, 2, 3, None, None, None, None, None),
            (86640000, 86700000, 1, 1, 12, 13, 8, 9, 6),
            (120000000, 120120000, 2, 2, 30, 34, 28, 33, 9),
        ])
        self.assertEqual(aggregate_page(source, '3', 2, 2)['candles'], report['candles'][2:])
        self.assertEqual(aggregate_page(source, '3', 4)['candles'], [])

    def test_missing_field_and_empty_open_bucket(self):
        bars = self.payload['data']['bars']
        bars[0]['volume'] = None
        del bars[3:5]
        first, empty, *_ = aggregate_page(self.pin(), '3')['candles']
        self.assertEqual((first['close'], first['volume']), (14, None))
        self.assertEqual((empty['observed'], empty['expected'], empty['open']), (0, 3, None))

    def test_reject_invalid_target_and_page(self):
        source = self.pin()
        for target, start, limit in [('1', 0, 1), ('D', 0, 1), ('3', -1, 1), ('3', 0, 1001)]:
            with self.subTest(target=target, start=start, limit=limit), self.assertRaises(ValueError):
                aggregate_page(source, target, start, limit)

    def test_order_and_paging_of_intrabar_observations(self):
        source = self.pin()
        for parent, times in enumerate(((86280000, 86340000, 86400000), (86460000, 86580000),
                                        (86640000,), (120000000, 120060000))):
            with self.subTest(parent=parent):
                whole = intrabar_page(source, '3', parent)
                self.assertEqual([bar['time'] for bar in whole['bars']], list(times))
                singles = [intrabar_page(source, '3', parent, i, 1) for i in range(len(times))]
                self.assertEqual([page['bars'][0] for page in singles], whole['bars'])
                self.assertEqual([page['next'] for page in singles], list(range(1, len(times)+1)))
                self.assertEqual(intrabar_page(source, '3', parent, len(times))['bars'], [])

    def test_empty_parent_and_incomplete_final_parent(self):
        del self.payload['data']['bars'][3:5]
        self.payload['data']['bars'].pop()
        source = self.pin()
        empty = intrabar_page(source, '3', 1)
        self.assertEqual((empty['bars'], empty['expected'], empty['next']), ([], 3, 0))
        last = intrabar_page(source, '3', 3)
        self.assertEqual((last['observed'], last['expected'], last['bars'][0]['time']), (1, 2, 120000000))
        self.assertEqual(intrabar_page(source, '3', 3, 0, 1)['bars'], last['bars'])
        with self.assertRaises(ValueError):
            intrabar_page(source, '3', 4)
