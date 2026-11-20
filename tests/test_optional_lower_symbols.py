"""Negative symbol evidence yields absent array references in Pine v5/v6."""
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.context_ir import request_nodes
from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical,loads,read_json
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.execution.artifacts import export_program,import_program
from packages.marketdata.providers import input_snapshot
from test_realtime import Sink
from tools.conformance import market_source,context_inputs,ROOT

FOLDER = ROOT/'conformance/optional-lower-symbols'


class OptionalLowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.market = read_json(FOLDER/'market.json')

    def program(self,body,version=6):
        return self.session.compile(f'//@version={version}\nindicator("Optional lower")\n'+body)

    def driver(self,program,catalog=True):
        market = self.market if catalog else self.market['data']
        return self.session.stream_driver(program,market_source(input_snapshot(market)),None,
                                          **context_inputs(FOLDER/'contexts.json'))

    def test_missing_reference_is_observable_and_size_has_its_normal_error(self):
        for v in (5,6):
            p = self.program('a=request.security_lower_tf("TEST:ABSENT","1",close,true)\nplot(array.size(a))',v)
            driver = self.driver(p)
            with self.assertRaises(OpenPineError) as raised:driver.run()
            self.assertEqual(raised.exception.diagnostic.code,'ARRAY_NA')

    def test_negative_evidence_required_and_strict_site_still_fails(self):
        for body,catalog in (('a=request.security_lower_tf("TEST:ABSENT","1",close,true)',False),
                ('a=request.security_lower_tf("TEST:SECOND","2",close,true)',True),
                ('a=request.security_lower_tf("TEST:ABSENT","1",close,true)\nb=request.security_lower_tf("TEST:ABSENT","1",close)',True)):
            with self.assertRaisesRegex(ValueError,'No pinned context'):
                self.driver(self.program(body+'\nplot(array.size(a))'),catalog=catalog)

    def test_timeframe_flag_and_symbol_flag_are_independent(self):
        call='a=request.security_lower_tf("TEST:ABSENT","10",close,true,ignore_invalid_timeframe={flag})\nplot(na(a) ? -1 : array.size(a))'
        with self.assertRaisesRegex(ValueError,'smaller divisible'):
            self.driver(self.program(call.format(flag="false")))
        driver=self.driver(self.program(call.format(flag="true")))
        driver.run()
        self.assertFalse(driver.contexts.groups)
        self.assertEqual(list(driver.evaluation.request_values.values()),[None])

    def test_explicit_flag_and_legacy_ir(self):
        src='a=request.security_lower_tf("TEST:SECOND","1",close{flag})\nplot(array.size(a))'
        for v in (5,6):
            old=self.program(src.format(flag=""),v)
            previous=import_program(export_program(old))
            self.session.validate_program(previous)
            oid=request_nodes(previous)[0].overload_id
            self.assertFalse(oid.endswith('.optional_symbol'))
            for flag in (',false',',ignore_invalid_symbol=true'):
                self.assertEqual(request_nodes(self.program(src.format(flag=flag),v))[0].overload_id,oid+'.optional_symbol')
            for flag in (',ignore_invalid_symbol=close>0',',ignore_invalid_symbol=1'):
                with self.assertRaises(OpenPineError):self.program(src.format(flag=flag),v)

    def test_history_ema_and_window(self):
        source = ('[a,b]=request.security_lower_tf("TEST:SECOND","1",[ta.ema(close,3),close],true,calc_bars_count=3)\n'
                  'q=request.security_lower_tf("TEST:ABSENT","1",close,true)\n'
                  'plot(array.size(b))\nplot(array.size(a)>0 ? array.get(a,0) : -1)\nplot(na(q[1]) ? -1 : 1)')
        for v in (5,6):
            d=self.driver(self.program(source,v));d.run()
            outputs=list(d.evaluation.outputs.values())
            self.assertEqual([list(o[-1]) for o in outputs],[[0,0,2,0,0,1],[-1,-1,20.,-1,-1,32.5],[-1]*6])

    def test_replay_strategy_all_cuts_and_duplicate_events(self):
        from packages.contracts.realtime import MarketEvent
        src=(ROOT/'examples/optional-lower-symbols/strategy.pine').read_text()
        p=import_program(export_program(self.session.compile(src)))
        contexts=tuple(MarketContext(i,s,t,'fixture') for i,s,t in [('primary','TEST:MAIN','5'),('second','TEST:SECOND','1')])
        def engine():
            sink=Sink()
            return ReplayEngine(self.session,p,contexts,'primary',sink,symbol_catalog=self.market['symbol_catalog']),sink
        events=[]
        for end in range(1,31):
            if end in (6,11,15,26):
                price=float((6,11,15,26).index(end)+1)*10
                events.append(MarketEvent(len(events),'second','close',(end-1)*60000,end*60000,0,price,price,price,price,1.))
            if end%5==0:
                price=float(end//5)
                events.append(MarketEvent(len(events),'primary','close',(end-5)*60000,end*60000,0,price,price,price,price,1.))
        base,out=engine()
        for e in events:base.apply(e)
        self.assertEqual([(r['bar_index'],r['value']['price']) for r in out.events if r['kind']=='fill'],[(2,'3')])
        for cut in range(1,len(events)):
            first,a=engine()
            for e in events[:cut]:first.apply(e)
            other,b=engine();other.restore(loads(canonical(first.checkpoint())))
            for e in events[cut-1:]:
                other.apply(e);other.apply(e)
            self.assertEqual(a.rows+b.rows,out.rows)
            self.assertEqual(a.events+b.events,out.events)
            self.assertEqual(other.checkpoint(),base.checkpoint())
