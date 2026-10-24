"""Independent UTC arithmetic, version boundaries and closed environment IR."""

import copy
from dataclasses import FrozenInstanceError,replace
from datetime import UTC,datetime
import unittest

from packages.contracts import HistoricalData,HistoricalBar,HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.environment import Environment,InstrumentValues
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.session import Session

INSTRUMENT={'mintick':'0.25','pointvalue':'1','currency':'USD','basecurrency':'ENV','type':'custom'}


def source(version,body):return f'//@version={version}\n'+('study' if version<5 else 'indicator')+'("Environment")\n'+body


def data(times=(0,60000,120000),tf='1',name='TEST:ENV'):
    return HistoricalData('environment',name,tf,tuple(HistoricalBar(t,10.+i) for i,t in enumerate(times)))


def run_with_metadata(session,program,dataset,instrument=INSTRUMENT):
    selected=MemorySource(dataset);selected.manifest={'provenance':{'environment':{'schema_version':'openpine.instrument-environment.1','instrument':instrument}}}
    driver=HistoricalDriver(session.validate_program(program).prepare(program),selected);driver.run()
    from packages.marketdata import data_identity
    return driver.finish(data_identity(dataset))


class EnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def run_code(self,v,body,dataset=None):return self.session.run(source(v,body),data=dataset or data())[1]

    def test_time_and_calendar_variables_and_functions_all_versions(self):
        times=(1704067200000,1709164800000,1709251200000) # Jan1, leapFeb29, Mar1 2024
        for v in range(1,7):
            r=self.run_code(v,'plot(time)\nplot(year)\nplot(month)\nplot(dayofmonth)\nplot(dayofweek)\nplot(hour(time))\nplot(minute)\nplot(second)',data(times))
            self.assertEqual([o.values for o in r.outputs],[times,(2024,)*3,(1,2,3),(1,29,1),(2,5,6),(0,)*3,(0,)*3,(0,)*3])

    def test_calendar_close_uses_month_lengths_and_not_next_observed_bar(self):
        for tf,times,ends in (
            ('M',(1704067200000,1706745600000,1711929600000),(1706745600000,1709251200000,1714521600000)),
            ('D',(1709078400000,1709251200000),(1709164800000,1709337600000)),
            ('1',(0,180000),(60000,240000))):
            for v in (4,5,6):self.assertEqual(self.run_code(v,'plot(time_close)',data(times,tf)).outputs[0].values,ends)

    def test_timestamp_numeric_forms_and_strict_invalid_dates(self):
        for v in range(2,7):
            r=self.run_code(v,'plot(timestamp(2024,2,29,12,34))\nplot(timestamp(2024,2,29,12,34,56))')
            self.assertEqual(r.outputs[0].values,(1709210040000,)*3);self.assertEqual(r.outputs[1].values,(1709210096000,)*3)
            for expression in ('timestamp(2023,2,29,0,0)','timestamp(2024,13,1,0,0)','timestamp(1969,12,31,0,0)'):
                with self.assertRaises(OpenPineError) as caught:self.run_code(v,'plot('+expression+')')
                self.assertEqual(caught.exception.diagnostic.code,'ENVIRONMENT_DATE')

    def test_full_calendar_range_does_not_depend_on_platform_gmtime(self):
        from packages.contracts.calendar import floor_open,next_open,shift_open,MAX_TIME
        # 9998-01-01 00:00 UTC and 9999-12-30 23:59:59.999 UTC.
        stamp=253339228800000
        p=self.session.compile(source(6,'plot(year)\nplot(month)\nplot(time_close)'))
        r=self.session.execute(p,data=data((stamp,),'M'))
        self.assertEqual([o.values for o in r.outputs],[(9998,),(1,),(253341907200000,)])
        self.assertEqual(floor_open(MAX_TIME-1,'D'),MAX_TIME-86400000)
        self.assertEqual(next_open(stamp,'M'),253341907200000)
        self.assertEqual(shift_open(stamp,'M',1),253341907200000)
        self.assertEqual(self.run_code(6,'plot(timestamp(9998,1,1,0,0))').outputs[0].values,(stamp,)*3)

    def test_explicit_unavailable_version_names(self):
        for v,name in ((1,'timestamp(2024,1,1,0,0)'),(1,'barstate.islast'),(2,'barstate.isfirst'),(3,'time_close'),(3,'timeframe.period'),(3,'syminfo.ticker'),(3,'barstate.islastconfirmedhistory'),(6,'period'),(6,'ticker')):
            with self.subTest(v=v,name=name),self.assertRaises(OpenPineError) as caught:self.session.compile(source(v,'x = '+name))
            self.assertEqual(caught.exception.diagnostic.code,'ENVIRONMENT_VERSION')

    def test_historical_flags_use_full_frozen_range(self):
        names=('isfirst','islast','ishistory','isrealtime','isnew','isconfirmed')
        expected=((1,0,0),(0,0,1),(1,1,1),(0,0,0),(1,1,1),(1,1,1))
        for v in (3,4,5,6):
            body='\n'.join(f'plot(barstate.{n} ? 1 : 0)' for n in names)
            self.assertEqual([o.values for o in self.run_code(v,body).outputs],list(expected))
        for v in (4,5,6):self.assertEqual(self.run_code(v,'plot(barstate.islastconfirmedhistory ? 1 : 0)').outputs[0].values,(0,0,1))
        self.assertEqual(self.run_code(2,'plot(barstate.islast ? 1 : 0)\nplot(barstate.isrealtime ? 1 : 0)').outputs[0].values,(0,0,1))

    def test_legacy_and_modern_timeframe_and_symbol_names(self):
        for v in range(1,7):
            tf='timeframe.period' if v>=4 else 'period';ticker='syminfo.ticker' if v>=4 else 'ticker'
            body=f'plot({tf} == "1" ? 1 : 0)\nplot({ticker} == "ENV" ? 1 : 0)\nplot(syminfo.prefix == "TEST" ? 1 : 0)'
            self.assertEqual([o.values for o in self.run_code(v,body).outputs],[(1,)*3]*3)
        for v in (4,5,6):
            for tf in ('D','W','M','5'):
                times=(1704067200000,) if tf!='5' else (0,)
                body='plot(timeframe.multiplier)\nplot(timeframe.isdwm ? 1 : 0)\nplot(timeframe.isseconds ? 1 : 0)'
                self.assertEqual([o.values for o in self.run_code(v,body,data(times,tf)).outputs],[(5 if tf=='5' else 1,),(0 if tf=='5' else 1,),(0,)])

    def test_metadata_is_required_and_numeric_properties_are_consistent(self):
        for v in range(1,7):
            p=self.session.compile(source(v,'plot(syminfo.mintick)\nplot(syminfo.pointvalue)'))
            with self.assertRaises(OpenPineError) as caught:self.session.execute(p,data=data())
            self.assertEqual(caught.exception.diagnostic.code,'ENVIRONMENT_METADATA')
            self.assertEqual([o.values for o in run_with_metadata(self.session,p,data()).outputs],[(.25,)*3,(1.,)*3])

    def test_metadata_is_immutable_closed_and_hash_bound(self):
        raw=copy.deepcopy(INSTRUMENT);env=Environment('test','TEST:ENV','1',3,raw);original=env.sha256
        raw['mintick']='999';self.assertEqual(env.instrument['mintick'],'0.25')
        with self.assertRaises(FrozenInstanceError):env.instrument.mintick='2'
        self.assertEqual(env.sha256,original);self.assertEqual(Environment.from_payload(env.payload()),env)
        for change in ({'mintick':'0'},{'pointvalue':'NaN'},{'mintick':'1e1000000000'},{'currency':''},{'unknown':'x'}):
            with self.assertRaises(ValueError):Environment('test','TEST:ENV','1',3,{**INSTRUMENT,**change})

    def test_broker_uses_frozen_tick_and_rejects_conflicting_units(self):
        from packages.contracts.trading import BrokerPolicy,BrokerSettings
        from packages.execution.environment import broker_environment
        env=Environment('test','TEST:ENV','1',3,INSTRUMENT)
        policy=BrokerPolicy('1000','1','0',0)
        self.assertEqual(broker_environment(env,policy).settings.tick_size,'0.25')
        for other in (replace(policy,settings=BrokerSettings('0.5')),):
            with self.assertRaises(OpenPineError) as caught:broker_environment(env,other)
            self.assertEqual(caught.exception.diagnostic.code,'ENVIRONMENT_BROKER')
        with self.assertRaises(OpenPineError):broker_environment(Environment('test','TEST:ENV','1',3,{**INSTRUMENT,'pointvalue':'10'}),policy)

    def test_environment_IR_cannot_forge_slots_types_or_schema(self):
        p=self.session.compile(source(6,'plot(time)'));node=p.statements[-1];ref=node.arguments[0].expression
        runtime=self.session.validate_program(p)
        for changed in (replace(ref,contract=replace(ref.contract,slot='environment.__class__')),replace(ref,value_type=replace(ref.value_type,base='float'))):
            forged=replace(p,statements=(*p.statements[:-1],replace(node,arguments=(replace(node.arguments[0],expression=changed),*node.arguments[1:]))))
            with self.assertRaises(OpenPineError):runtime.admit(forged)
        with self.assertRaises(OpenPineError):runtime.admit(replace(p,schema_version='openpine.typed-ir.5'))


if __name__=='__main__':unittest.main()
