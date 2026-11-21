"""Different markets at different intervals must each advance on their own candles."""
from pathlib import Path
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, HistoricalData
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.execution.driver import MemorySource
from packages.execution.session import Session


class NestedMarketClockTests(unittest.TestCase):
    def test_rotating_outer_resolves_inherited_inner_market_per_route(self):
        self.check_rotating_outer(False)

    def test_rotating_outer_in_user_function(self):
        self.check_rotating_outer(True)

    def check_rotating_outer(self, udf):
        from packages.contracts.prepared_requests import graph
        from packages.contracts.realtime import MarketEvent
        from packages.execution.realtime import ReplayEngine
        from test_realtime import Sink

        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        source = ('//@version=6\nindicator("Two clocks per market")\n'
                  'selected=close==1 ? "TEST:B" : "TEST:C"\n'
                  'plot(request.security(selected,"1",request.security("","2",close[1])))')
        if udf:
            source = source.replace('selected=','readMarket(s) => request.security(s,"1",request.security("","2",close[1]))\nselected=')
            source = source.replace('plot(request.security(selected,"1",request.security("","2",close[1])))',
                                    'plot(readMarket(selected))')
        program = session.compile(source)
        chart = HistoricalData('chart','TEST:CHART','1',tuple(
            HistoricalBar(i*60000,float(1+i%2)) for i in range(8)))
        primary = MarketContext('chart','TEST:CHART','1','fixture')
        rows = graph(program,primary=primary)['requests']
        self.assertEqual([(r['symbol'],r['timeframe']) for r in rows],
                         [('TEST:B','2'),('TEST:B','1'),('TEST:C','2'),('TEST:C','1')])
        for inner,outer in zip(rows[::2],rows[1::2],strict=True):
            child, = outer['nested_requests']
            self.assertEqual((child['execution_site_id'],child['inherited'],child['symbol'],child['timeframe']),
                             (inner['call_site_id'],['symbol'],outer['symbol'],'2'))
        sources, contexts = [], [primary]
        tape = [(b.time+60000,2,primary,b) for b in chart.bars]
        for symbol,multiplier in [('B',10),('C',40)]:
            for interval in (1,2):
                cid = symbol+str(interval)
                ctx = MarketContext(cid,'TEST:'+symbol,str(interval),'fixture')
                bars = tuple(HistoricalBar(i*interval*60000,float(multiplier*(i+1))) for i in range(8//interval))
                reader = MemorySource(HistoricalData(cid,ctx.symbol,ctx.timeframe,bars))
                reader.bind_identity()
                sources.append((ContextBinding(ctx,reader.identity.sha256),reader))
                contexts.append(ctx)
                tape.extend((b.time+interval*60000,2-interval,ctx,b) for b in bars)
        expected = (None,None,None,40.,10.,80.,20.,120.)
        result = session.execute(program,data=chart,primary_context=primary,context_sources=sources)
        self.assertEqual(result.outputs[0].values,expected)
        sink = Sink()
        engine = ReplayEngine(session,program,tuple(contexts),'chart',sink,
            environments={c.context_id:dict(instrument=None,history_last_ms=480000) for c in contexts})
        for i,(end,_,ctx,b) in enumerate(sorted(tape,key=lambda r:r[:2])):
            engine.apply(MarketEvent(i,ctx.context_id,'close',b.time,end,0,*([b.close]*4),1.))
        self.assertEqual(tuple(r[-1] for r in sink.rows),expected)

    def test_outer_missing_bar_delays_visibility_of_inner_close(self):
        self.check_clocks((None,10.,10.,10.,20.,30.,30.,40.),
                          (None,None,None,None,10.,20.,20.,30.))

    def test_shared_usd_child_converts_to_each_selected_parent_currency(self):
        from packages.contracts.fx import DAY
        from packages.marketdata.providers import make_snapshot

        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        program = session.compile('//@version=6\nindicator("Caller currencies")\n'
            's=close==1 ? "TEST:B" : "TEST:C"\n'
            'plot(request.security(s,"1",request.security("TEST:A","2",close,currency=syminfo.currency)))')
        def market(name, interval, prices):
            return HistoricalData(name,'TEST:'+name,str(interval),tuple(
                HistoricalBar(DAY+i*interval*60000,float(price)) for i,price in enumerate(prices)))
        chart = market('CHART',1,[1,2,1,2,1,2])
        bindings = []
        for data,currency in [(market('A',2,[10,20,30]),'USD'),
                              (market('B',1,[1]*6),'GBP'),(market('C',1,[1]*6),'EUR')]:
            reader = MemorySource(data)
            reader.bind_identity()
            provenance = dict(provider='fixture',environment=dict(schema_version='openpine.instrument-environment.1',
                instrument=dict(basecurrency='TEST',currency=currency,mintick='0.01',pointvalue='1',type='custom')))
            snapshot = make_snapshot(data,provenance)
            reader.manifest = dict(snapshot_id=snapshot.sha256,provenance=provenance)
            ctx = MarketContext(data.context_id,data.symbol,data.timeframe,'fixture')
            bindings.append((ContextBinding(ctx,snapshot.sha256),reader))
        rates = dict(schema_version='openpine.fx.1',source='manual',pairs=[
            dict(base='USD',quote=quote,start_ms=DAY,end_ms=2*DAY,
                 rates=[dict(day_ms=0,available_ms=DAY,rate=rate)])
            for quote,rate in [('GBP',.5),('EUR',.8)]])
        result = session.execute(program,data=chart,context_sources=bindings,fx_rates=rates,
            primary_context=MarketContext('CHART',chart.symbol,chart.timeframe,'fixture'))
        self.assertEqual(result.outputs[0].values,(None,8.,5.,16.,10.,24.))

    def test_inner_two_candles_and_outer_three_candles(self):
        self.check_clocks((None,None,None,None,None,30.,30.,40.),
                          (None,None,None,None,None,None,None,30.), counted=True)

    def test_inner_and_outer_conversions_observe_their_own_closes(self):
        self.check_clocks((None,10.,10.,10.,30.,72.,72.,96.),
                          (None,None,None,None,15.,48.,48.,72.), converted=True)

    def test_lower_window_counts_only_outer_bars_and_preserves_empty_window(self):
        self.check_clocks((20.,120.,None), (3,4,0), lower=True)

    def test_lower_window_keeps_inner_ema_warmup_before_first_visible_intrabar(self):
        self.check_clocks((None,210.859375,None), (0,2,0), lower=True, ema=True)

    def test_inner_gaps_compare_against_previous_outer_candle(self):
        self.check_clocks((None,10.,None,None,20.,30.,None,40.),
                          (None,None,None,None,10.,20.,None,30.), gaps=True)

    def test_inner_lookahead_history_uses_caller_open_and_replay_uses_observations(self):
        self.check_clocks((10.,10.,20.,20.,30.,30.,40.,40.),(None,None,10.,10.,20.,20.,30.,30.),
            lookahead=True, replay=((None,10.,10.,10.,20.,30.,30.,40.),(None,None,None,None,10.,20.,20.,30.)))

    def test_inner_lookahead_gaps_on_sparse_outer(self):
        self.check_clocks((10.,None,20.,20.,30.,None,40.,None),(None,None,10.,10.,20.,None,30.,None),
            gaps=True, lookahead=True,
            replay=((None,10.,None,None,20.,30.,None,40.),(None,None,None,None,10.,20.,None,30.)))

    def test_lower_window_reads_lookahead_at_each_intrabar_open(self):
        self.check_clocks((40.,140.,None),(3,4,0),lower=True,lookahead=True,
                          replay=((20.,120.,None),(3,4,0)))

    def check_clocks(self, expected, previous, *, counted=False, converted=False, lower=False, gaps=False, ema=False,
                     lookahead=False, replay=None):
        from packages.contracts.fx import DAY
        from packages.marketdata.providers import make_snapshot
        source = (Path(__file__).resolve().parents[1] /
                  'examples/dynamic-requests/nested-market-clocks.pine').read_text()
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        if counted:
            source = source.replace('close[1]]))','close[1]],calc_bars_count=2),calc_bars_count=3)')
        if converted:
            source = source.replace('close[1]]))','close[1]],currency=syminfo.currency),currency=currency.EUR)')
        if gaps or lookahead:
            flags = (',gaps=barmerge.gaps_on' if gaps else '') + (',lookahead=barmerge.lookahead_on' if lookahead else '')
            source = source.replace('close[1]]))','close[1]]'+flags+'))')
        if lower:
            source = ('//@version=6\nindicator("Nested intrabars")\n'
                      'a=request.security_lower_tf("TEST:B","1",request.security("TEST:A","2",close))\n'
                      'plot(array.sum(a))\nplot(array.size(a))')
            if ema:
                source = source.replace('request.security("TEST:A","2",close))',
                    'request.security("","",ta.ema(close,3)),calc_bars_count=2)')
            if lookahead:
                source = source.replace('"2",close))','"2",close,lookahead=barmerge.lookahead_on))')
        program = session.compile(source, 'nested-market-clocks.pine')

        def market(name, interval, prices):
            return HistoricalData(name, 'TEST:'+name, interval, tuple(
                HistoricalBar(DAY+minute*60_000, float(price)) for minute,price in prices))

        chart = market('CHART','4',[(0,1),(4,1),(8,1)]) if lower else market('CHART','1',enumerate([1]*8))
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
            primary_context=MarketContext(chart.context_id,chart.symbol,chart.timeframe,'fixture'))
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
        if lower:
            from packages.contracts.lower_events import PROFILE
            options['lower_tf'] = dict(schema_version=PROFILE,late='reject',
                                       warmup={'B':dict(count=7,end_ms=DAY+480_000)})
        engine = ReplayEngine(session,program,contexts,'CHART',sink,**options)
        for event in events[:10]:
            engine.apply(event)
        state = loads(canonical(engine.checkpoint()))
        resumed = ReplayEngine(session,program,contexts,'CHART',sink,**options)
        resumed.restore(state)
        resumed.apply(events[9])
        for event in events[10:]:
            resumed.apply(event)
        self.assertEqual([r[-1] for r in sink.rows], [v for pair in zip(*(replay or (expected,previous))) for v in pair])
        if counted or converted or lower and lookahead:
            self.check_saved_job(program, market_data, contexts, currencies, rates if converted else None,
                                 expected, previous)

    def check_saved_job(self, program, datasets, contexts, currencies, rates, expected, previous):
        from dataclasses import asdict
        import json
        import subprocess
        import sys
        from tempfile import TemporaryDirectory
        from apps.openpine.bootstrap import local_manager
        from packages.execution.artifacts import export_program

        with TemporaryDirectory() as directory:
            folder = Path(directory)
            bindings = []
            with local_manager(folder/'state',autostart=False) as manager:
                for data,context in zip(datasets,contexts,strict=True):
                    instrument = dict(basecurrency='TEST',currency=currencies[data.context_id],
                                      mintick='0.01',pointvalue='1',type='custom')
                    metadata = dict(context_id=data.context_id,symbol=data.symbol,timeframe=data.timeframe,
                                    bar_schema=data.schema_version)
                    snapshot = manager.market.ingest(metadata,data.bars,dict(provider='fixture',
                        environment=dict(schema_version='openpine.instrument-environment.1',instrument=instrument)))
                    bindings.append(dict(context=asdict(context), snapshot_id=snapshot))
            primary = bindings.pop()
            (folder/'contexts.json').write_text(json.dumps(dict(schema_version='openpine.context-inputs.1',
                primary_context=primary['context'],contexts=bindings)))
            (folder/'program.json').write_text(json.dumps(export_program(program)))
            options = ['--artifact',str(folder/'program.json'),'--snapshot-id',primary['snapshot_id'],
                       '--contexts',str(folder/'contexts.json')]
            if rates:
                (folder/'fx.json').write_text(json.dumps(rates))
                options.extend(['--fx-rates',str(folder/'fx.json')])
            def job(*args):
                command = [sys.executable,'-m','apps.openpine','job','--state-dir',str(folder/'state'),*args]
                response = subprocess.run(command,capture_output=True,text=True)
                self.assertEqual(response.returncode,0,response.stdout+response.stderr)
                return json.loads(response.stdout)
            preview = job(*options,'--plan-only')
            self.assertEqual([(r['symbol'],r['timeframe']) for r in preview['context_graph']['requests']],
                             [('TEST:A','2'),('TEST:B','1')])
            if rates:
                self.assertEqual([(r['base'],r['quote']) for r in preview['fx_rates']],
                                 [('USD','GBP'),('GBP','EUR')])
            paused = job(*options,'--pause-after',str(min(5,len(expected)-1)))['job']
            self.assertEqual(paused['status'],'paused',paused)
            result = job('--resume',paused['job_id'])
            self.assertEqual(result['job']['status'],'succeeded',result)
            self.assertEqual([r['values'] for r in result['response']['result']['outputs']],
                             [list(expected),list(previous)])
