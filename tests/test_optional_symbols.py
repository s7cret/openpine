"""Offline symbol evidence, split replay and type-preserving missing results."""
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.context_ir import request_nodes
from packages.contracts.optional_symbols import resolutions
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from test_realtime import PRIMARY, Sink, tape

CATALOG = {"schema_version":"openpine.symbol-catalog.1","source":"fixture","unknown_symbols":["TEST:ABSENT"]}


class OptionalSymbolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_prepared_options_follow_selected_target_and_strict_sites(self):
        from packages.contracts.inputs import settings_for
        from packages.contracts.prepared_requests import resolved_requests
        from packages.contracts.optional_symbols import resolve_nodes
        from packages.execution.artifacts import export_program, import_program
        for version in (4,5,6):
            name = "security" if version==4 else "request.security"
            decl = "study" if version==4 else "indicator"
            inp = "input" if version==4 else "input.string"
            for flag in ("",",ignore_invalid_symbol=false",",ignore_invalid_symbol=true"):
                source = (f'//@version={version}\n{decl}("Options")\ns={inp}("TEST:SECOND")\n'
                          f'alias=s\na={name}(alias,"2",close{flag})\nplot(a)')
                program = import_program(export_program(self.session.compile(source)))
                for chosen in ("TEST:SECOND","TEST:ABSENT","TEST:UNLISTED"):
                    settings = settings_for(program,{"s":chosen})
                    nodes = resolved_requests(program,settings)
                    pairs = [("TEST:SECOND","2")]
                    if chosen=="TEST:SECOND" or chosen=="TEST:ABSENT" and flag.endswith("true"):
                        rows=resolve_nodes(nodes,pairs,CATALOG)
                        self.assertEqual((rows[0]["symbol"],rows[0]["resolution"]),
                                         (chosen,"bound" if chosen=="TEST:SECOND" else "unknown_symbol"))
                    else:
                        with self.assertRaisesRegex(ValueError,chosen):
                            resolve_nodes(nodes,pairs,CATALOG)
                if flag.endswith("true"):
                    mixed=self.session.compile(source+f'\nb={name}(s,"2",close)\nplot(b)')
                    with self.assertRaisesRegex(ValueError,"TEST:ABSENT"):
                        resolve_nodes(resolved_requests(mixed,settings_for(mixed,{"s":"TEST:ABSENT"})),[],CATALOG)

    def test_missing_source_is_distinct_from_negative_evidence(self):
        row = {"symbol":"TEST:ABSENT","timeframe":"2","ignore_invalid_symbol":True}
        with self.assertRaises(ValueError):resolutions([row],[],None)
        with self.assertRaises(ValueError):resolutions([row],[("TEST:ABSENT","1")],CATALOG)
        for strict in ({**row,"ignore_invalid_symbol":False},{k:v for k,v in row.items() if k!="ignore_invalid_symbol"}):
            with self.assertRaises(ValueError):resolutions([row,strict],[],CATALOG)
        self.assertEqual(resolutions([row],[],CATALOG)[0]["resolution"],"unknown_symbol")

    def test_prepared_missing_tuple_preserves_versioned_bool(self):
        from packages.contracts.inputs import settings_for
        for version in (4,5,6):
            name = "security" if version==4 else "request.security"
            decl = "study" if version==4 else "indicator"
            inp = "input" if version==4 else "input.string"
            source=(f'//@version={version}\n{decl}("Tuple")\ns={inp}("TEST:UNSELECTED")\n'
                    f'[n,b]={name}(s,"2",[close,close>0],ignore_invalid_symbol=true)\nplot(n)')
            program=self.session.compile(source)
            engine=ReplayEngine(self.session,program,(PRIMARY,),PRIMARY.context_id,Sink(),
                                settings=settings_for(program,{"s":"TEST:ABSENT"}),symbol_catalog=CATALOG)
            self.assertEqual(list(engine.skipped.values()),[(None,False if version==6 else None)])

    def test_replay_restores_numeric_and_boolean_tuple_without_a_secondary_stream(self):
        for version in (4,5,6):
            fn = "security" if version == 4 else "request.security"
            decl = "study" if version == 4 else "indicator"
            source = (f'//@version={version}\n{decl}("Optional tuple")\n'
                      f'[n,b] = {fn}("TEST:ABSENT","2",[close,close>0],ignore_invalid_symbol=true)\n'
                      'plot(n)\nplot(b ? 1 : 0)')
            program = self.session.compile(source)
            def engine():
                return ReplayEngine(self.session,program,(PRIMARY,),PRIMARY.context_id,Sink(),symbol_catalog=CATALOG)
            events = list(tape(3))
            expected = engine()
            for event in events:expected.apply(event)
            before = engine()
            before.apply(events[0])
            after = engine()
            after.restore(before.checkpoint())
            after.apply(events[0])
            for event in events[1:]:after.apply(event)
            self.assertEqual(expected.checkpoint(),after.checkpoint())
            self.assertEqual(list(after.skipped.values()),[(None,False if version==6 else None)])
            changed = ReplayEngine(self.session,program,(PRIMARY,),PRIMARY.context_id,Sink(),
                                   symbol_catalog={**CATALOG,"source":"revision"})
            with self.assertRaises(ValueError):changed.restore(before.checkpoint())

    def test_replay_strategy_duplicate_closes_and_all_restore_boundaries(self):
        from packages.compiler.semantic.specification import ROOT
        from packages.contracts.contexts import MarketContext
        from packages.contracts.realtime import MarketEvent
        from packages.contracts.wire import canonical,loads
        program = self.session.compile((ROOT/'examples/optional-symbols/strategy.pine').read_text())
        main = MarketContext("primary","TEST:MAIN","1","fixture")
        secondary = MarketContext("second","TEST:SECOND","2","fixture")
        def engine():
            sink = Sink()
            return ReplayEngine(self.session,program,(main,secondary),"primary",sink,symbol_catalog=CATALOG),sink
        events = []
        for i in range(6):
            close_ms = (i+1)*60000
            if i in (1,5):
                price = 10. if i==1 else 30.
                events.append(MarketEvent(len(events),"second","close",close_ms-120000,close_ms,0,price,price,price,price,1.))
            price = float(i+1)
            events.append(MarketEvent(len(events),"primary","close",i*60000,close_ms,0,price,price,price,price,1.))
        baseline,expected = engine()
        for event in events:baseline.apply(event)
        self.assertEqual([r["bar_index"] for r in expected.events if r["kind"]=="order"],[1])
        self.assertEqual([(r["bar_index"],r["value"]["price"]) for r in expected.events if r["kind"]=="fill"],[(2,"3")])
        for cut in range(1,len(events)):
            first,prefix = engine()
            for event in events[:cut]:first.apply(event)
            resumed,suffix = engine()
            resumed.restore(loads(canonical(first.checkpoint())))
            for event in events[cut-1:]:
                resumed.apply(event)
                resumed.apply(event)
            self.assertEqual(prefix.rows+suffix.rows,expected.rows)
            self.assertEqual(prefix.events+suffix.events,expected.events)
            self.assertEqual(resumed.checkpoint(),baseline.checkpoint())

    def test_omitted_option_preserves_the_existing_overload(self):
        for version in (4,5,6):
            fn = "security" if version==4 else "request.security"
            decl = "study" if version==4 else "indicator"
            src = f'//@version={version}\n{decl}("Compare")\nplot({fn}("TEST:MAIN","2",close{{option}}))'
            nodes = [request_nodes(self.session.compile(src.format(option=arg)))[0]
                     for arg in ("",", ignore_invalid_symbol=false")]
            self.assertEqual(nodes[1].overload_id,nodes[0].overload_id+".optional_symbol")
            self.assertEqual([len(n.arguments) for n in nodes],[5,6])
