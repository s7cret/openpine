"""Exercise explicit symbol resolution through CLI, HTTP and resumed workers."""
from uuid import uuid4
from packages.contracts.wire import canonical
from tools.specification import ROOT
from tools.installed_input_smoke import wait_job


def verify(http,cli,work,*,lower=False):
    suite = 'optional-lower-symbols' if lower else 'optional-symbols'
    folder = ROOT/'conformance'/suite
    def upload(name):
        return http('/api/snapshots',{'provider':'upload','raw':(folder/name).read_text()})['sha256']
    main, second = upload('market.json'), upload('secondary.json')
    for sample in ('indicator','strategy'):
        source = ROOT/'examples'/suite/(sample+'.pine')
        compiled = http('/api/compile',dict(source=source.read_text(),source_name=source.name))
        artifact = work/f'{suite}-{sample}.ir.json'
        artifact.write_bytes(canonical(http('/api/artifacts/'+compiled['artifact_ref'])))
        args = ('--bars',folder/'market.json','--contexts',folder/'contexts.json','--state-dir',work/'symbol-cli')
        preview = cli('job','--artifact',artifact,*args,'--plan-only')['plan']
        launch = http('/api/launches',dict(schema_version='openpine.launch-request.1',request_id=uuid4().hex,
            input_ref=compiled['input_ref'],primary=dict(kind='snapshot',snapshot_id=main),
            contexts=[dict(kind='snapshot',snapshot_id=second)],inputs={},timeout_seconds=120,
            gap_policy='allow_with_metadata',checkpoint_interval_bars=1,pause_after_bars=1))
        assert launch['status']=='ready',launch
        sites = preview['symbol_resolutions']
        assert sites == launch['prepared']['plan']['symbol_resolutions']
        assert [r['resolution'] for r in sites] == ['bound','unknown_symbol']
        assert [r['snapshot_id'] for r in sites] == [second,None]
        path = '/api/jobs/'+http('/api/launches/'+launch['launch_id']+'/start',{})['job_id']
        wait_job(http,path,'paused')
        http(path+'/resume',{})
        assert wait_job(http,path,'succeeded')['resumed_from_bar']==1
        result = http(path+'/result')['result']
        native = cli('job','--artifact',artifact,*args)['response']['result']
        assert result['outputs'] == native['outputs']
        expected = ([[0,1,2,0,0,1],[-1]*6,[1]*6] if lower else [[None,10.,10.,10.,10.,30.],[None]*6])
        assert [o['values'] for o in result['outputs']] == expected
        if sample=='strategy':
            assert result['simulation']==native['simulation']
            ledger = http(path+'/ledger?limit=100')['events']
            assert [r['bar_index'] for r in ledger if r['kind']=='order']==[1]
            assert [(r['bar_index'],r['value']['price']) for r in ledger if r['kind']=='fill']==[(2,'3')]

def verify_prepared(http, cli, work):
    for sample, suite in (("indicator","optional-symbols"),("strategy","optional-lower-symbols")):
        folder = ROOT / "conformance" / suite
        source = ROOT / "examples/prepared-optional-symbols" / (sample + ".pine")
        compiled = http("/api/compile", {"source_name":source.name,"source":source.read_text()})
        artifact = work / ("prepared-" + sample + ".json")
        artifact.write_bytes(canonical(http("/api/artifacts/" + compiled["artifact_ref"])))
        def snapshot(name):
            return http("/api/snapshots", {"provider":"upload","raw":(folder/name).read_text()})["sha256"]
        main, second = snapshot("market.json"), snapshot("secondary.json")
        for chosen in ("TEST:SECOND","TEST:ABSENT","TEST:UNLISTED"):
            overrides = {"chosen":chosen}
            settings = work / "prepared-overrides.json"
            settings.write_bytes(canonical(overrides))
            args = ("job","--artifact",artifact,"--bars",folder/"market.json","--contexts",folder/"contexts.json",
                    "--inputs",settings,"--state-dir",work/"prepared-cli")
            request = dict(schema_version="openpine.launch-request.1",request_id=uuid4().hex,input_ref=compiled["input_ref"],
                primary=dict(kind="snapshot",snapshot_id=main),contexts=[dict(kind="snapshot",snapshot_id=second)],
                inputs=overrides,timeout_seconds=120,gap_policy="allow_with_metadata",pause_after_bars=1,checkpoint_interval_bars=1)
            if chosen == "TEST:UNLISTED":
                try:
                    preview = cli(*args,"--plan-only")
                except AssertionError as error:
                    assert "TEST:UNLISTED" in str(error) and "context" in str(error),str(error)
                else:
                    assert "TEST:UNLISTED" in str(preview.get("error",preview.get("diagnostics",[]))),preview
                try:
                    rejected = http("/api/launches",request)
                except AssertionError as error:
                    assert "TEST:UNLISTED" in str(error) and "pinned context" in str(error),str(error)
                else:
                    assert rejected["status"] == "failed" and "TEST:UNLISTED" in str(rejected["error"]), rejected
                continue
            launch = http("/api/launches",request)
            plan = cli(*args,"--plan-only")["plan"]
            sites = plan["symbol_resolutions"]
            assert sites == launch["prepared"]["plan"]["symbol_resolutions"]
            assert sites[-1]["symbol"] == chosen and sites[-1]["snapshot_id"] == (second if chosen == "TEST:SECOND" else None)
            path = "/api/jobs/" + http("/api/launches/" + launch["launch_id"] + "/start",{})["job_id"]
            wait_job(http,path,"paused")
            http(path+"/resume",{})
            assert wait_job(http,path,"succeeded")["resumed_from_bar"] == 1
            result = http(path+"/result")["result"]
            native = cli(*args)["response"]["result"]
            assert result["outputs"] == native["outputs"]
            known = chosen == "TEST:SECOND"
            closes = [None,10.,10.,10.,10.,30.]
            counts = [0,1,2,0,0,1]
            expected = [closes,closes if known else [None]*6] if sample == "indicator" else [
                counts,counts if known else [-1]*6,[0 if known else 1]*6]
            assert [row["values"] for row in result["outputs"]] == expected
            if sample == "strategy":
                assert result["simulation"] == native["simulation"]
                ledger = http(path+"/ledger?limit=100")["events"]
                assert [r["bar_index"] for r in ledger if r["kind"]=="order"] == ([] if known else [1])
                assert [(r["bar_index"],r["value"]["price"]) for r in ledger if r["kind"]=="fill"] == ([] if known else [(2,"3")])

def verify_security_count(http,cli,work):
    folder=ROOT/"conformance/security-count"
    def upload(name):
        return http("/api/snapshots",{"provider":"upload","raw":(folder/name).read_text()})["sha256"]
    main,second=upload("primary.json"),upload("secondary.json")
    expected=[[None]*9+[30.,30.,35.],[None]*3+[10.,10.,15.,15.,15.,15.,22.5,22.5,31.25]]
    for sample in ("indicator","strategy"):
        source=ROOT/"examples/security-count"/(sample+".pine")
        compiled=http("/api/compile",{"source_name":source.name,"source":source.read_text()})
        artifact=work/("counted-"+sample+".ir.json")
        artifact.write_bytes(canonical(http("/api/artifacts/"+compiled["artifact_ref"])))
        args=("job","--artifact",artifact,"--bars",folder/"primary.json","--contexts",folder/"contexts.json",
              "--state-dir",work/"counted-cli")
        plan=cli(*args,"--plan-only")["plan"]
        launch=http("/api/launches",dict(schema_version="openpine.launch-request.1",request_id=uuid4().hex,input_ref=compiled["input_ref"],
            primary=dict(kind="snapshot",snapshot_id=main),contexts=[dict(kind="snapshot",snapshot_id=second)],inputs={},
            timeout_seconds=120,gap_policy="allow_with_metadata",pause_after_bars=5,checkpoint_interval_bars=1))
        sites=plan["symbol_resolutions"]
        assert [(s["symbol"],s["timeframe"],s["calc_bars_count"],s["snapshot_id"]) for s in sites]==[
            ("TEST:SECOND","2",limit,second) for limit in (2,4)]
        assert sites==launch["prepared"]["plan"]["symbol_resolutions"]
        path="/api/jobs/"+http("/api/launches/"+launch["launch_id"]+"/start",{})["job_id"]
        wait_job(http,path,"paused")
        http(path+"/resume",{})
        assert wait_job(http,path,"succeeded")["resumed_from_bar"]==5
        result=http(path+"/result")["result"]
        native=cli(*args)["response"]["result"]
        assert result["outputs"]==native["outputs"]
        assert [o["values"] for o in result["outputs"]]==expected
        if sample=="strategy":
            assert result["simulation"]==native["simulation"]
            ledger=http(path+"/ledger?limit=100")["events"]
            assert [r["bar_index"] for r in ledger if r["kind"]=="order"]==[9]
            assert [(r["bar_index"],r["value"]["price"]) for r in ledger if r["kind"]=="fill"]==[(10,"11")]
