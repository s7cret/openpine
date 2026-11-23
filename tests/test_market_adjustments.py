import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.openpine.bootstrap import local_services
from packages.contracts.wire import read_json
from packages.marketdata.adjustments import price_page
from packages.marketdata.providers import input_snapshot


class PriceProfiles(unittest.TestCase):
    def setUp(self):
        folder = Path(__file__).resolve().parents[1] / 'examples/corporate-actions'
        market = self.enterContext(local_services(self.enterContext(TemporaryDirectory()))).market
        self.source = market.source(market.save(input_snapshot(read_json(folder/'prices.json'))))
        self.actions = read_json(folder/'price-events.json')

    def prices(self, cutoff, **options):
        return price_page(self.source, 'split-cash', self.actions, cutoff, **options)

    def test_raw_split_cash_and_visibility(self):
        def values(result):
            return [tuple(row[key] for key in ('open','high','low','close','volume')) for row in result['bars']]
        raw = [(100,106,98,104,10), (52,54,49,50,18), (48,51,47,49,21)]
        self.assertEqual(values(price_page(self.source, 'raw')), raw)
        self.assertEqual(values(self.prices(59999)), raw[:1])
        self.assertEqual(values(self.prices(60000)), [(50,53,49,52,20), raw[1]])
        self.assertEqual(values(self.prices(149999)), [(50,53,49,52,20), *raw[1:]])
        adjusted = [(48,51,47,50,20), (50,52,47,48,18), raw[2]]
        self.assertEqual(values(self.prices(150000)), adjusted)
        self.assertEqual(values(self.prices(150000, start=1, limit=1)), adjusted[1:2])
        self.assertEqual(values(price_page(self.source, 'raw')), raw)

    def test_simultaneous_and_later_split(self):
        dividend = self.actions['streams'][1]['events'][0]
        dividend['effective_ms'] = 60000
        self.assertEqual(self.prices(150000)['bars'][0]['close'], 50)
        dividend['effective_ms'] = 30000
        self.assertEqual(self.prices(150000)['bars'][0]['close'], 51)

    def test_coverage_and_currency(self):
        with self.assertRaises(ValueError):
            self.prices(240000)
        self.actions['streams'][1]['currency'] = 'EUR'
        with self.assertRaises(ValueError):
            self.prices(150000)
