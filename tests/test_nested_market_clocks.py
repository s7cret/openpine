"""Different markets at different intervals must each advance on their own candles."""
from pathlib import Path
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, HistoricalData
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.execution.driver import MemorySource
from packages.execution.session import Session


class NestedMarketClockTests(unittest.TestCase):
    def test_outer_missing_bar_delays_visibility_of_inner_close(self):
        self.check_clocks((None,10.,10.,10.,20.,30.,30.,40.),
                          (None,None,None,None,10.,20.,20.,30.))

    def test_inner_two_candles_and_outer_three_candles(self):
        self.check_clocks((None,None,None,None,None,30.,30.,40.),
                          (None,None,None,None,None,None,None,30.), counted=True)

    def test_inner_and_outer_conversions_observe_their_own_closes(self):
        self.check_clocks((None,10.,10.,10.,30.,72.,72.,96.),
                          (None,None,None,None,15.,48.,48.,72.), converted=True)

    def check_clocks(self, expected, previous, *, counted=False, converted=False):
        from packages.contracts.fx import DAY
        from packages.marketdata.providers import make_snapshot
        source = (Path(__file__).resolve().parents[1] /
                  'examples/dynamic-requests/nested-market-clocks.pine').read_text()
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        if counted:
            source = source.replace('close[1]]))','close[1]],calc_bars_count=2),calc_bars_count=3)')
        if converted:
            source = source.replace('close[1]]))','close[1]],currency=syminfo.currency),currency=currency.EUR)')
        program = session.compile(source, 'nested-market-clocks.pine')

        def market(name, interval, prices):
            return HistoricalData(name, 'TEST:'+name, interval, tuple(
                HistoricalBar(DAY+minute*60_000, float(price)) for minute,price in prices))

        chart = market('CHART', '1', enumerate([1]*8))
        outer = market('B', '1', [(i,100+i) for i in (0,1,2,4,5,6,7)])
        inner = market('A', '2', [(0,10),(2,20),(4,30),(6,40)])
        bindings = []
        def instrument(currency):
            return dict(basecurrency='TEST',currency=currency,mintick='0.01',pointvalue='1',type='custom')
        currencies = {'A':'USD','B':'GBP','CHART':'EUR'}
        for data in (outer,inner):
            reader = MemorySource(data)
            reader.bind_identity()
            context = MarketContext(data.context_id,data.symbol,data.timeframe,'fixture')
            provenance = dict(provider='fixture',environment=dict(schema_version='openpine.instrument-environment.1',
                              instrument=instrument(currencies[data.context_id])))
            snapshot = make_snapshot(data,provenance)
            reader.manifest = dict(snapshot_id=snapshot.sha256,provenance=provenance)
            bindings.append((ContextBinding(context,snapshot.sha256),reader))
        rates = dict(schema_version='openpine.fx.1',source='manual',pairs=[
            dict(base=base,quote=quote,start_ms=DAY,end_ms=2*DAY,rates=[
                dict(day_ms=0,available_ms=DAY,rate=early),
                dict(day_ms=0,available_ms=DAY+4*60_000,rate=late)])
            for base,quote,early,late in [('USD','GBP',.5,.8),('GBP','EUR',2.,3.)]])
        result = session.execute(program, data=chart, context_sources=bindings,
            fx_rates=rates if converted else None,
            primary_context=MarketContext(chart.context_id,chart.symbol,'1','fixture'))
        self.assertEqual(result.outputs[0].values, expected)
        self.assertEqual(result.outputs[1].values, previous)
        from packages.contracts.realtime import MarketEvent
        from packages.contracts.wire import canonical,loads
        from packages.execution.realtime import ReplayEngine
        from test_realtime import Sink

        market_data = (inner,outer,chart)
        contexts = tuple(MarketContext(d.context_id,d.symbol,d.timeframe,'fixture') for d in market_data)
        tape = sorted((bar.time+int(d.timeframe)*60_000, order, d, bar) for order,d in enumerate(market_data)
                      for bar in d.bars)
        events = [MarketEvent(i,d.context_id,'close',bar.time,end,0,*([bar.close]*4),1.)
                  for i,(end,_,d,bar) in enumerate(tape)]
        sink = Sink()
        options = {'security_warmup':{d.context_id:{'count':len(d.bars),'end_ms':DAY+480_000}
                   for d in (inner,outer)}} if counted else {}
        if converted:
            options.update(environments={k:dict(instrument=instrument(v),history_last_ms=None)
                                         for k,v in currencies.items()},fx_rates=rates)
        engine = ReplayEngine(session,program,contexts,'CHART',sink,**options)
        for event in events[:10]:
            engine.apply(event)
        state = loads(canonical(engine.checkpoint()))
        resumed = ReplayEngine(session,program,contexts,'CHART',sink,**options)
        resumed.restore(state)
        resumed.apply(events[9])
        for event in events[10:]:
            resumed.apply(event)
        self.assertEqual([r[-1] for r in sink.rows], [v for pair in zip(expected,previous) for v in pair])
