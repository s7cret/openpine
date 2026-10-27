"""Frozen target dependencies and one saved IR selecting distinct market graphs."""
import copy,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar,OpenPineError,ProgramIR
from packages.contracts.codec import encode,decode
from packages.contracts.inputs import settings_for
from packages.contracts.prepared_requests import graph,resolved_requests
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.execution.session import Session
from packages.execution.driver import MemorySource
from test_observed_broker import Sink

def source(v=6):
    declaration='indicator' if v>=5 else 'study';inp='input.string' if v>=5 else 'input';security='request.security' if v>=5 else 'security'
    return f'//@version={v}\n{declaration}("Prepared")\nsym={inp}("TEST:A")\ntf={inp}("2")\nplot({security}(sym,tf,close))'

class PreparedRequestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def test_saved_typed_targets_and_default_vs_override_all_versions(self):
        for v in range(1,7):
            p=self.session.compile(source(v));self.assertEqual(p.schema_version,'openpine.typed-ir.11')
            p=decode(encode(p),ProgramIR);self.session.validate_program(p)
            a=graph(p);settings=settings_for(p,{'sym':'TEST:B','tf':'1'});b=graph(p,settings)
            self.assertEqual((a['requests'][0]['symbol'],a['requests'][0]['timeframe']),('TEST:A','2'))
            self.assertEqual((b['requests'][0]['symbol'],b['requests'][0]['timeframe']),('TEST:B','1'))
            self.assertNotEqual(a['sha256'],b['sha256'])
            primary=MarketContext('primary','TEST:CHART','1','recorded.1')
            bars=HistoricalData('primary','TEST:CHART','1',tuple(HistoricalBar(i*60000,1.,1.,1.,1.,1.) for i in range(4)),schema_version='openpine.historical-bars.2')
            for overrides,symbol,tf,prices,expected in (({},'TEST:A','2',[10.,20.],[10.,10.,20.,20.] if v<3 else [None,10.,10.,20.]),
                    ({'sym':'TEST:B','tf':'1'},'TEST:B','1',[100.,101.,102.,103.],[100.,101.,102.,103.])):
                context=MarketContext('requested',symbol,tf,'recorded.1')
                data=HistoricalData('requested',symbol,tf,tuple(HistoricalBar(i*int(tf)*60000,x,x,x,x,1.) for i,x in enumerate(prices)),schema_version='openpine.historical-bars.2')
                out=Sink();d=self.session.stream_driver(p,MemorySource(bars),out,primary_context=primary,
                    context_sources=((ContextBinding(context,'a'*64),MemorySource(data)),),settings=settings_for(p,overrides))
                d.run();self.assertEqual([r[-1] for r in out.rows],expected,v)
    def test_lower_arrays_select_frozen_input_all_attested_versions(self):
        import test_lower_tf as historical
        for v in (5,6):
            code=historical.source('sym=input.string("TEST:LOWER")\ntf=input.string("1")\na=request.security_lower_tf(sym,tf,close)\nplot(array.sum(a))',v)
            p=self.session.compile(code);self.session.validate_program(p)
            a,b=historical.data();primary=MarketContext(a.context_id,a.symbol,a.timeframe,'recorded.1');other=MarketContext(b.context_id,b.symbol,b.timeframe,'recorded.1')
            out=Sink();d=self.session.stream_driver(p,MemorySource(a),out,primary_context=primary,
                context_sources=((ContextBinding(other,'a'*64),MemorySource(b)),),settings=settings_for(p))
            d.run();self.assertEqual([r[-1] for r in out.rows],[10.,26.,42.,58.])
    def test_mutable_series_computed_and_local_target_rejected(self):
        for declaration in ('var sym="TEST:A"','sym="TEST:A"\nsym:="TEST:B"','sym=close>0 ? "TEST:A" : "TEST:B"'):
            text='//@version=6\nindicator("No dynamic target")\n'+declaration+'\nplot(request.security(sym,"2",close))'
            with self.assertRaises(OpenPineError):self.session.compile(text)
    def test_target_validation_uses_selected_settings_and_no_program_rewrite(self):
        p=self.session.compile(source());original=encode(p)
        for values in ({'tf':'0'},{'tf':'01'},{'sym':'unqualified'}):
            with self.assertRaises(ValueError):graph(p,settings_for(p,values))
        self.assertEqual(encode(p),original)
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.6'))
    def test_named_literal_calls_keep_old_contract(self):
        p=self.session.compile('//@version=6\nindicator("Literal")\nplot(request.security(expression=close,symbol="TEST:A",timeframe="2"))')
        self.assertEqual(p.schema_version,'openpine.typed-ir.5');self.session.validate_program(p)
        self.assertFalse(resolved_requests(p)[0].overload_id.endswith('.prepared'))

    def test_replay_resolves_settings_and_every_event_restore_all_versions(self):
        from packages.execution.realtime import ReplayEngine
        from packages.contracts.realtime import MarketEvent
        from packages.contracts.wire import canonical,loads
        contexts=(MarketContext('primary','TEST:CHART','1','recorded.1'),MarketContext('a','TEST:A','2','recorded.1'),MarketContext('b','TEST:B','1','recorded.1'))
        rows=[]
        for cid,tf,prices in (('primary',1,[1.]*4),('a',2,[10.,20.]),('b',1,[100.,101.,102.,103.])):
            rows.extend((i*tf*60000+tf*60000,cid=='primary',cid,i*tf*60000,p) for i,p in enumerate(prices))
        events=[MarketEvent(seq,cid,'close',t,at,0,p,p,p,p,1.) for seq,(at,_,cid,t,p) in enumerate(sorted(rows))]
        for v in range(1,7):
            program=self.session.compile(source(v));settings=settings_for(program,{'sym':'TEST:B','tf':'1'})
            def engine():
                out=Sink();return ReplayEngine(self.session,program,contexts,'primary',out,settings=settings),out
            full,out=engine()
            for e in events:full.apply(e)
            self.assertEqual([r[-1] for r in out.rows],[100.,101.,102.,103.])
            for cut in range(len(events)+1):
                first,prefix=engine()
                for e in events[:cut]:first.apply(e)
                other,tail=engine();other.restore(loads(canonical(first.checkpoint())))
                for e in events[cut:]:other.apply(e)
                self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(other.status(),full.status())

    def test_const_alias_and_input_alias_are_bound_without_executing_expressions(self):
        code=source().replace('tf=input.string("2")','tf=input.string("2")\nselected=tf').replace('security(sym,tf,close)','security(sym,selected,close)')
        p=self.session.compile(code);self.session.validate_program(p)
        self.assertEqual(graph(p,settings_for(p,{'tf':'4'}))['requests'][0]['timeframe'],'4')
        bad=replace(p,statements=tuple(replace(n,expression=replace(n.expression,binding_id=n.binding.binding_id))
            if getattr(n,'binding',None) and n.binding.name=='selected' else n for n in p.statements))
        with self.assertRaisesRegex(OpenPineError,'cycle'):self.session.validate_program(bad)

    def test_versioned_profile_examples_and_independent_corpus_are_reproducible(self):
        from types import SimpleNamespace
        from packages.compiler.semantic.requests import register_prepared
        from packages.compiler.semantic.specification import SpecError
        from tools.prepared_requests_spec import examples,corpus,profile
        from tools.specification import ROOT
        from packages.contracts.wire import read_json
        from tools.conformance import run_cases
        self.assertEqual(read_json(ROOT/'spec/prepared-requests-profile.json'),profile())
        snapshot=self.session.snapshot
        foreign=replace(snapshot,references={**snapshot.references,6:{**snapshot.references[6],'language_version':1}})
        catalog=SimpleNamespace(snapshot=foreign,language_version=6,contracts={})
        base=next(c for c in self.session.catalog_for(6).contracts.values() if c.operation=='context.request' and not c.overload_id.endswith('.prepared'))
        with self.assertRaises(SpecError) as caught:register_prepared(catalog,base)
        self.assertEqual(caught.exception.code,'SPEC_EVIDENCE')
        for folder,files in (('examples/prepared-requests',examples()),('conformance/prepared-requests',corpus())):
            for name,raw in files.items():self.assertEqual((ROOT/folder/name).read_bytes(),raw)
        report=run_cases(cases_path='conformance/prepared-requests/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(14,6))

if __name__=='__main__':unittest.main()
