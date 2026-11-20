"""Compare multicurrency plans, saved programs and fills through CLI and HTTP."""
from uuid import uuid4
from packages.contracts.wire import canonical,read_json
from tools.specification import ROOT
from tools.installed_input_smoke import wait_job


def verify(http,cli,work):
    folder=ROOT/"conformance/security-currency"
    snapshots=[http("/api/snapshots",dict(provider="upload",raw=(folder/name).read_text()))["sha256"]
               for name in ("primary.json","second.json")]
    for sample in ("indicator","strategy"):
        source=ROOT/"examples/security-currency"/(sample+".pine")
        compiled=http("/api/compile",dict(source=source.read_text(),source_name=source.name))
        artifact=work/(sample+"-security-currency.ir.json")
        artifact.write_bytes(canonical(http("/api/artifacts/"+compiled["artifact_ref"])))
        for revision in ("rates","revised"):
            fx_path=folder/(revision+".json")
            fx=http("/api/fx-rates",read_json(fx_path))["sha256"]
            config=dict(schema_version="openpine.launch-request.1",request_id=uuid4().hex,
                input_ref=compiled["input_ref"],primary=dict(kind="snapshot",snapshot_id=snapshots[0]),
                contexts=[dict(kind="snapshot",snapshot_id=snapshots[1])],inputs={},timeout_seconds=120,
                gap_policy="allow_with_metadata",fx_snapshot_id=fx)
            launch=http("/api/launches",config)
            assert launch["status"]=="ready",launch
            args=("job","--artifact",artifact,"--bars",folder/"primary.json","--contexts",folder/"contexts.json",
                  "--state-dir",work/"security-currency-cli","--fx-rates",fx_path)
            preview=cli(*args,"--plan-only")
            sites=preview["plan"]["symbol_resolutions"]
            assert sites==launch["prepared"]["plan"]["symbol_resolutions"]
            assert [(s["source_currency"],s["target_currency"],s["fx_snapshot_id"]) for s in sites[:2]]==[("USD","EUR",fx)]*2
            assert all(s["snapshot_id"]==snapshots[1] for s in sites)
            assert "fx_snapshot_id" not in sites[2]
            assert preview["fx_rates"]==launch["prepared"]["requirements"]["fx_rates"]
            route="/api/jobs/"+http("/api/launches/"+launch["launch_id"]+"/start",{})["job_id"]
            wait_job(http,route,"succeeded")
            result=http(route+"/result")["result"]
            local=cli(*args)["response"]["result"]
            assert result["outputs"]==local["outputs"]
            assert [r["values"] for r in result["outputs"]]==[
                [50,60,112,120 if revision=="revised" else 128],
                [50,55,100,106.875 if revision=="revised" else 114],[100,120,140,160]]
            if sample=="strategy":
                assert result["simulation"]==local["simulation"]
                ledger=http(route+"/ledger?limit=100")["events"]
                assert [(r["bar_index"],r["value"]["price"]) for r in ledger if r["kind"]=="fill"]==[(3,"13")]
