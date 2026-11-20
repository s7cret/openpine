"""Independent requested-currency expectations across changes in published rates."""
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, HistoricalBar, HistoricalData
from packages.contracts.contexts import ContextBinding, MarketContext
from packages.contracts.fx import DAY
from packages.execution.artifacts import export_program, import_program
from packages.execution.driver import MemorySource
from packages.execution.session import Session
from packages.marketdata.providers import make_snapshot


class SecurityCurrencyTests(unittest.TestCase):
    def test_completed_secondary_ema_uses_visible_rate(self):
        minute = 60_000
        closes = [8*DAY+16*60*minute, 11*DAY+10*60*minute,
                  11*DAY+602*minute, 12*DAY+10*60*minute]
        bars = tuple(HistoricalBar(end-minute, price) for end,price in zip(closes, (100.,120.,140.,160.), strict=True))
        primary = HistoricalData("chart", "TEST:B", "1", bars)
        secondary = HistoricalData("secondary", "TEST:A", "1", bars)
        context = MarketContext("secondary","TEST:A","1","fixture")
        provenance = {"provider":"fixture", "environment":{"schema_version":"openpine.instrument-environment.1",
                      "instrument":{"basecurrency":"TEST","currency":"USD","mintick":"0.01","pointvalue":"1","type":"custom"}}}
        snapshot = make_snapshot(secondary,provenance)
        source = MemorySource(secondary)
        source.manifest = {"snapshot_id":snapshot.sha256,"provenance":provenance}
        rates = {"schema_version":"openpine.fx.1","source":"manual","pairs":[{
            "base":"USD","quote":"EUR","start_ms":8*DAY,"end_ms":13*DAY,"rates":[
                {"day_ms":7*DAY,"available_ms":8*DAY,"rate":0.5},
                {"day_ms":8*DAY,"available_ms":closes[1]+minute,"rate":0.8},
                {"day_ms":8*DAY,"available_ms":closes[3]-60*minute,"rate":0.75}]}]}
        session = Session(profile_id=HISTORICAL_PROFILE_ID)
        for version in (5,6):
            with self.subTest(version=version):
                program = session.compile(f'//@version={version}\nindicator("FX")\n'
                    'plot(request.security("TEST:A","1",ta.ema(close,3),currency=currency.EUR))\n'
                    'plot(request.security("TEST:A","1",close,barmerge.gaps_off,barmerge.lookahead_off,false,"EUR",4))')
                result = session.execute(import_program(export_program(program)), data=primary,
                    primary_context=MarketContext("chart","TEST:B","1","fixture"),
                    context_sources=[(ContextBinding(context,snapshot.sha256),source)],fx_rates=rates)
                self.assertEqual(result.outputs[0].values,(50.,55.,100.,106.875))
                self.assertEqual(result.outputs[1].values,(50.,60.,112.,120.))

    def test_lower_children_use_individual_publication_times(self):
        minute=60_000
        start=11*DAY+600*minute
        parent=HistoricalData("chart","TEST:B","4",tuple(
            HistoricalBar(start+i*4*minute,99.) for i in range(2)))
        child=HistoricalData("secondary","TEST:A","1",tuple(
            HistoricalBar(start+i*minute,p,p,p,p,10.) for i,p in ((0,120.),(2,140.))),
            schema_version="openpine.historical-bars.2")
        provenance={"provider":"fixture","environment":{"schema_version":"openpine.instrument-environment.1",
            "instrument":{"basecurrency":"TEST","currency":"USD","mintick":"0.01","pointvalue":"1","type":"custom"}}}
        snapshot=make_snapshot(child,provenance)
        source=MemorySource(child)
        source.manifest={"snapshot_id":snapshot.sha256,"provenance":provenance}
        binding=ContextBinding(MarketContext("secondary","TEST:A","1","fixture"),snapshot.sha256)
        fx={"schema_version":"openpine.fx.1","source":"manual","pairs":[{
            "base":"USD","quote":"EUR","start_ms":8*DAY,"end_ms":13*DAY,"rates":[
                {"day_ms":7*DAY,"available_ms":8*DAY,"rate":0.5},
                {"day_ms":8*DAY,"available_ms":start+2*minute,"rate":0.8}]}]}
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        for v,arg in ((5,'false,"EUR"'),(6,'currency=currency.EUR')):
            with self.subTest(version=v):
                program=session.compile(f'//@version={v}\nindicator("Children")\n'
                    f'[p,q,r,n]=request.security_lower_tf("TEST:A","1",[close,volume,close/open,200],{arg})\n'
                    'plot(array.size(p)>0 ? array.get(p,0) : 0)\n'
                    'plot(array.size(p)>0 ? array.get(p,1) : 0)\nplot(array.sum(q))\n'
                    'plot(array.sum(r))\nplot(array.sum(n))\nplot(array.size(p))')
                result=session.execute(import_program(export_program(program)),data=parent,
                    primary_context=MarketContext("chart","TEST:B","4","fixture"),
                    context_sources=[(binding,source)],fx_rates=fx)
                self.assertEqual([o.values for o in result.outputs],
                    [(60.,0),(112.,0),(20.,None),(2.,None),(400,None),(2,0)])
