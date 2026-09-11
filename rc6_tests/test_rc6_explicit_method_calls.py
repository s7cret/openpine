"""Explicit receiver calls must produce real orders through existing transports.

compare_modes replaces IPC in-process only. Separate tests retain actual protected
worker admission. Manual field/price expectations are not TradingView exports.
"""
import json

import pytest
from pine2ast.libraries import LibraryStore

from openpine.compile.native_rc6 import NativeRC6CompilerAdapter
from openpine.runtime.rc6_worker_runtime import _engine_bar
from rc6_tests.test_rc6_deferred_exits import compare_modes
from rc6_tests.test_rc6_generated_checkpoint import advance
from rc6_tests.test_rc6_lifecycle import make_session
from rc6_tests.test_rc6_library_imports import prepare
from rc6_tests.test_rc6_worker_admission import _manifest

DECL = """export type Counter
    int n=0
    varip int ticks=0
method hidden(Counter self,int step)=>
    self.n+=step
    self.ticks+=step
    self
export method add(Counter self,int step)=>hidden(self,step)
export method value(Counter self)=>self.n
"""


def setup(version=6,on_close=False,recalc=False,imported=True,broker_guard=True,decl=DECL):
    ns="signal." if imported else ""
    libs={"qa/Signal/1":f'//@version={version}\nlibrary("Signal")\n'+decl}
    declarations="import qa/Signal/1 as signal" if imported else decl.replace("export ","")
    guard=" and strategy.position_size==0" if broker_guard else ""
    source=f'''//@version={version}
strategy("explicit methods")
{declarations}
var {ns}Counter a={ns}Counter.new()
var {ns}Counter b={ns}Counter.new()
{ns}add(step=1,self=a)
{ns}add(b,10)
c={ns}add(a.copy(),100)
if bar_index==2{guard} and {ns}value(a)==3 and {ns}value(b)==30 and {ns}value(c)==103
    strategy.entry("explicit",strategy.long,qty={ns}value(a))
'''
    return prepare(version,on_close,recalc,source_override=source,libs=libs)


@pytest.mark.parametrize("version",[5,6])
@pytest.mark.parametrize("on_close",[False,True])
@pytest.mark.parametrize("recalc",[False,True])
@pytest.mark.parametrize("imported",[False,True])
def test_explicit_receiver_preserves_actual_broker_entry(monkeypatch,tmp_path,version,on_close,recalc,imported):
    case,rows=setup(version,on_close,recalc,imported)
    result,tape=compare_modes(monkeypatch,tmp_path,case,rows)
    assert [(e["command_id"],e["bar_index"],float(e["qty"])) for e in tape]==[("explicit",2,3)]
    assert [(t.entry_price,t.qty) for t in result.open_trades]==[(102 if on_close else 103,3)]
    assert "user_method_function_calls_v1" in case[0].consumer_bundle["consumer_contract"]["required_capabilities"]


@pytest.mark.parametrize("cut",[1,3,5])
def test_explicit_methods_checkpoint_matches_uninterrupted_and_binds_sources(cut):
    case,rows=setup(broker_guard=False)
    whole=make_session(case)
    expected=advance(whole,rows)
    first=make_session(case)
    prefix=advance(first,rows,stop=cut)
    snapshot=json.loads(json.dumps(first.export_state()))
    restored=make_session(case)
    restored.restore_state(snapshot)
    assert prefix+advance(restored,rows,start=cut)==expected
    assert restored.export_state()==whole.export_state()
    other,_=setup(broker_guard=False,decl=DECL+'\n// updated locked source\n')
    with pytest.raises(ValueError,match="identity"):
        make_session(other).restore_state(snapshot)


@pytest.mark.parametrize("bad",["signal.hidden(a,1)",'signal.add("not a Counter",1)',"signal.add(self=a,step=1,self=a)"])
def test_host_rejects_invalid_or_private_explicit_calls_before_staging(bad):
    result=NativeRC6CompilerAdapter().compile(
        '//@version=6\nstrategy("bad")\nimport qa/Signal/1 as signal\nvar signal.Counter a=signal.Counter.new()\n'+bad+'\n',
        library_store=LibraryStore.create({"qa/Signal/1":'//@version=6\nlibrary("Signal")\n'+DECL}),
        producer_commits={"pine2ast":"a"*40,"ast2python":"b"*40},
    )
    assert not result.success and not result.python_code


@pytest.mark.parametrize("mode",["interactive","bulk_backtest"])
@pytest.mark.parametrize("on_close",[False,True])
def test_real_protected_workers_execute_explicit_method_artifacts(tmp_path,mode,on_close):
    from openpine.runtime.isolated_run import run_isolated_artifact
    case,rows=setup(on_close=on_close,recalc=True)
    compiled,context,cfg=case
    for name,value in dict(execution_context=context,admitted_manifest=_manifest(),
                           instrument_id=context["instrument_id"],generated_artifact=compiled.generated_artifact,
                           bar_envelopes=rows,run_hash="sha256:"+"1"*64,
                           protocol_artifact_dir=str(tmp_path/"protocol"),isolated_protocol=mode).items():
        setattr(cfg,name,value)
    out=run_isolated_artifact(compiled.python_code.encode(),bars=[_engine_bar(b) for b in rows],config=cfg,params={})
    assert out["ok"] and out["bars_processed"]==6
    assert [(e["command_id"],float(e["qty"])) for e in out["intent_tape"]]==[("explicit",3)]
    assert [(t.entry_price,t.qty) for t in out["raw_result"].open_trades]==[(102 if on_close else 103,3)]
