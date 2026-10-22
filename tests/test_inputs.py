"""Versioned input binding, immutable IR, typed overrides and checkpoint isolation."""
from dataclasses import replace
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData,OpenPineError,InputIR
from packages.contracts.inputs import input_schema,settings_for
from packages.execution.session import Session
from packages.execution.artifacts import export_program,import_program
from packages.runtime.engine import program_hash
from packages.execution.driver import HistoricalDriver,MemorySource


DATA=HistoricalData('primary','TEST:INPUTS','1',tuple(HistoricalBar(i*60000,float(10+i),float(9+i),float(12+i),float(8+i),100.) for i in range(6)),schema_version='openpine.historical-bars.2')


def code(version):
    declaration='indicator' if version>=5 else 'study';ema='ta.ema' if version>=5 else 'ema'
    make=lambda kind,default:f'input.{kind}({default})' if version>=5 else f'input({default})'
    return f'//@version={version}\n{declaration}("inputs")\nlength={make("int","2")}\nscale={make("float","1.0")}\nenabled={make("bool","true")}\nlabel={make("string",chr(34)+"A"+chr(34))}\nsrc={make("source","close")}\nplot({ema}(src,length)*scale)\nplot(enabled and label=="A" ? src : 0)'


class InputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_one_ir_all_versions_defaults_overrides_source_and_roundtrip(self):
        for version in range(1,7):
            with self.subTest(version=version):
                program=self.session.compile(code(version));identity=program_hash(program)
                self.assertEqual(program.schema_version,'openpine.typed-ir.6')
                self.assertEqual(import_program(export_program(program)),program)
                self.assertEqual(len(input_schema(program)['definitions']),5)
                base=self.session.execute(program,data=DATA)
                changed=self.session.execute(program,data=DATA,inputs={'length':3,'scale':2,'enabled':False,'src':'open'})
                self.assertEqual(changed.outputs[1].values,(0.,)*6)
                self.assertEqual(base.outputs[1].values,tuple(b.close for b in DATA.bars))
                self.assertEqual(changed.settings['values']['input:scale'],2.)
                self.assertNotEqual(changed.settings['sha256'],base.settings['sha256'])
                self.assertEqual(program_hash(program),identity)
                self.assertEqual(self.session.execute(program,data=DATA),base)

    def test_exact_int64_transport_and_input_values_do_not_mutate_settings(self):
        program=self.session.compile('//@version=6\nindicator("int64")\nn=input.int(9007199254740993)\nplot(n)')
        self.assertEqual(input_schema(program)['definitions'][0]['default_text'],'9007199254740993')
        values={'n':{'$int':'9007199254740995'}};settings=settings_for(program,values);values['n']['$int']='1'
        self.assertEqual(settings['values']['input:n'],9007199254740995)
        for value in ({'$int':'01'},{'$int':'9223372036854775808'},{'$int':'-0'}):
            with self.assertRaises(OpenPineError):settings_for(program,{'n':value})

    def test_constraints_options_legacy_type_constants_and_bad_versions(self):
        for version in range(1,7):
            declaration='indicator' if version>=5 else 'study'
            body='n=input.int(2,minval=1,maxval=4,step=1)' if version>=5 else 'n=input(2,type='+('input.integer' if version==4 else 'integer')+',minval=1,maxval=4,step=1)'
            program=self.session.compile(f'//@version={version}\n{declaration}("bounds")\n{body}\nplot(n)')
            for values in ({'n':True},{'n':0},{'n':5},{'n':2.5},{'unknown':2},{'n':2,'input:n':3}):
                with self.assertRaises(OpenPineError):settings_for(program,values)
            if version>=3:
                expression='input.string("A",options=["A","B"])' if version>=5 else 'input("A",options=["A","B"])'
                selected=self.session.compile(f'//@version={version}\n{declaration}("choice")\nx={expression}\nplot(x=="B" ? 1 : 0)')
                self.assertEqual(self.session.execute(selected,data=DATA,inputs={'x':'B'}).outputs[0].values,(1,)*6)
                with self.assertRaises(OpenPineError):settings_for(selected,{'x':'C'})
        for version in (1,2,3,4):
            with self.assertRaises(OpenPineError):self.session.compile(f'//@version={version}\nstudy("old")\nx=input.int(2)\nplot(x)')

    def test_settings_are_pinned_across_checkpoint_and_wrong_settings_restore_rejected(self):
        program=self.session.compile(code(6));runtime=self.session.validate_program(program)
        first=settings_for(program,{'length':3});saved=[]
        driver=HistoricalDriver(runtime.prepare(program,settings=first),MemorySource(DATA))
        driver.run(pause_after=3,checkpoint=lambda value:saved.append(value.checkpoint()))
        wrong=HistoricalDriver(runtime.prepare(program,settings=settings_for(program,{'length':4})),MemorySource(DATA))
        with self.assertRaisesRegex(ValueError,'settings'):wrong.restore(saved[0])
        self.assertEqual(wrong.count,0)
        correct=HistoricalDriver(runtime.prepare(program,settings=first),MemorySource(DATA));correct.restore(saved[0]);correct.run()
        self.assertEqual(correct.finish(self.session.execute(program,data=DATA).data),self.session.execute(program,data=DATA,inputs={'length':3}))

    def test_no_inputs_keep_legacy_ir_and_forbidden_input_positions_rejected(self):
        plain=self.session.compile('//@version=6\nindicator("plain")\nplot(close)')
        self.assertEqual(plain.schema_version,'openpine.typed-ir.5');self.assertIsNone(settings_for(plain))
        for body in ('plot(input.int(2))','if close>0\n    n=input.int(2)','f() => input.int(2)\nplot(f())','n=input.int(2,options=[])\nplot(n)'):
            with self.assertRaises(OpenPineError):self.session.compile('//@version=6\nindicator("bad")\n'+body)
        program=self.session.compile(code(6));first=program.statements[1]
        bad=replace(program,statements=(program.statements[0],replace(first,expression=replace(first.expression,definition=replace(first.expression.definition,default=True))),*program.statements[2:]))
        with self.assertRaises(OpenPineError):self.session.validate_program(bad)


if __name__=='__main__':unittest.main()
