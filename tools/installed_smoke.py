"""Offline acceptance of the installed CLI, saved IR, worker and HTTP path."""

import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path

from apps.ui.server import Server
from packages.compiler.semantic.specification import ROOT, load_snapshot
from packages.contracts.application import TERMINAL_STATUSES


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-root", type=Path)
    args = parser.parse_args(argv)
    check(sys.version_info[:3] == (3, 14, 8), "Acceptance requires Python 3.14.8")
    if args.expected_root:
        check(ROOT == args.expected_root.resolve(), "Imported a different checkout instead of the installation")
    load_snapshot()
    expected = json.loads((ROOT / "examples/pipeline/expected.json").read_text())["outputs"]
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    with tempfile.TemporaryDirectory(prefix="openpine-accept-") as directory:
        work = Path(directory)
        def cli(*arguments):
            process = subprocess.run([sys.executable, "-X", "utf8", "-c", bootstrap, str(ROOT), *map(str, arguments)],
                cwd=work, capture_output=True, text=True, encoding="utf-8", timeout=90)
            check(process.returncode == 0, f"CLI failed: {process.stdout[-2000:]} {process.stderr[-2000:]}")
            return json.loads(process.stdout) if process.stdout.strip() else None
        bars = ROOT / "examples/pipeline/bars.json"
        print("Installed acceptance: CLI v1-v6",file=sys.stderr,flush=True)
        cli_responses = {}
        for version in range(1, 7):
            artifact = work / f"v{version}.json"
            source = ROOT / f"examples/pipeline/v{version}.pine"
            cli("compile", "--source", source, "--profile", "historical", "--output", artifact)
            result = cli("run", "--artifact", artifact, "--bars", bars)
            check([o["values"] for o in result["result"]["outputs"]] == expected, f"Wrong installed v{version} result")
            cli_responses[version] = result
        isolated = cli("job", "--source", ROOT / "examples/pipeline/v6.pine", "--bars", bars, "--state-dir", work / "cli-jobs")
        check(isolated["job"]["status"] == "succeeded", "Installed worker failed")
        check([o["values"] for o in isolated["response"]["result"]["outputs"]] == expected, "Worker values differ")
        held=cli("job","--source",ROOT/"examples/pipeline/v6.pine","--bars",bars,
            "--state-dir",work/"paused-jobs","--pause-after","2","--checkpoint-every","1")
        check(held["job"]["status"]=="paused" and held["job"]["completed"]==2,"Installed job did not pause at its cursor")
        continued=cli("job","--resume",held["job"]["job_id"],"--state-dir",work/"paused-jobs")
        check(continued["job"]["resumed_from_bar"]==2,"Installed job replayed its prefix")
        check(continued["response"]["result"]==isolated["response"]["result"],"Installed checkpoint resume differs")
        check(cli("storage", "inspect", "--state-dir", work / "cli-jobs")["integrity"] == "ok", "Installed SQLite catalog failed")
        preview=cli("storage","gc","--state-dir",work/"cli-jobs","--grace-seconds","0")
        check(preview["applied"] is False,"GC must default to preview")
        cli("storage","quota","--state-dir",work/"cli-jobs","--max-mb","64")
        check(cli("storage","inspect","--state-dir",work/"cli-jobs")["usage"]["quota_bytes"] == 64*1024**2,"Quota was not persisted")
        cli("storage", "backup", "--state-dir", work / "cli-jobs", "--destination", work / "backup")
        cli("storage", "restore", "--source", work / "backup", "--state-dir", work / "restored-jobs")
        replay = cli("job", "--source", ROOT / "examples/pipeline/v6.pine", "--snapshot-id", isolated["response"]["snapshot_sha256"],
            "--state-dir", work / "restored-jobs")
        check(replay["response"]["result"] == isolated["response"]["result"], "Installed backup replay differs")
        print("Installed acceptance: context CLI and joint resume",file=sys.stderr,flush=True)
        context_cli={};example=ROOT/"examples/contexts"
        context_expected=json.loads((example/"expected.json").read_text(encoding="utf-8"))
        for version in (1,6):
            options=["--pause-after","17"] if version==6 else []
            current=cli("job","--source",example/f"v{version}.pine","--bars",example/"bars.json","--contexts",example/"inputs.json",
                "--state-dir",work/f"context-cli-{version}",*options)
            if version==6:
                check(current["job"]["status"]=="paused","Installed context job did not pause")
                current=cli("job","--resume",current["job"]["job_id"],"--state-dir",work/f"context-cli-{version}")
                check(current["job"]["resumed_from_bar"]==17,"Installed context graph replayed its prefix")
            check(current["job"]["status"]=="succeeded","Installed context worker failed")
            result=current["response"]["result"]
            check([o["values"] for o in result["outputs"]]==context_expected[str(version)],"Installed requested series differ")
            context_cli[version]=result
        print("Installed acceptance: HTTP and CLI equality",file=sys.stderr,flush=True)
        strategy_example=ROOT/'examples/strategies';strategy_cli={}
        print('Installed acceptance: trading CLI and pending-order resume',file=sys.stderr,flush=True)
        for version in (1,6):
            name=f'v{version}-filtered.pine'
            current=cli('job','--source',strategy_example/name,'--bars',strategy_example/'wave.json','--contexts',strategy_example/'inputs.json',
                '--state-dir',work/f'strategy-cli-{version}','--pause-after','2')
            check(current['job']['status']=='paused','Installed trading job did not pause')
            current=cli('job','--resume',current['job']['job_id'],'--state-dir',work/f'strategy-cli-{version}')
            check(current['job']['status']=='succeeded','Installed trading worker failed')
            result=current['response']['result'];check(result['schema_version']=='openpine.run-result.4','Installed trading result schema differs')
            check(result['simulation']['statistics']['equity']==('1000' if version==1 else '997.88'),'Installed trading arithmetic differs')
            strategy_cli[version]=result
        server = Server(("127.0.0.1", 0), work / "http")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        token = ""
        def http(path, payload=None):
            request = urllib.request.Request(base + path, data=json.dumps(payload).encode() if payload is not None else None,
                headers={"Content-Type": "application/json", "X-OpenPine-Token": token})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:return json.loads(response.read())
            except urllib.error.HTTPError as error:
                raise AssertionError(f"HTTP {path}: {error.code} {error.read().decode('utf-8')}") from error
        try:
            token = http("/api/config")["token"]
            with urllib.request.urlopen(base + "/", timeout=10) as response:
                check(b"OpenPine" in response.read(), "Installed web assets missing")
            snapshot_id = http("/api/snapshots", {"provider": "upload", "raw": bars.read_text()})["sha256"]
            for version in (1, 6):
                artifact = json.loads((work / f"v{version}.json").read_text())["artifact"]
                job = http("/api/jobs", {"schema_version": "openpine.job-submission.1", "input": {"artifact": artifact},
                    "snapshot_sha256": snapshot_id, "timeout_seconds": 30})["job_id"]
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    state = http("/api/jobs/" + job)
                    if state["status"] in TERMINAL_STATUSES:
                        break
                    time.sleep(.04)
                check(state["status"] == "succeeded", f"Installed HTTP worker failed: {state}")
                result = http(f"/api/jobs/{job}/result")
                check(result["result"] == cli_responses[version]["result"], "HTTP and CLI computational results differ")
                summary = http(f"/api/jobs/{job}/summary")
                plan = http(f"/api/jobs/{job}/plan")
                check(plan["snapshot_id"] == snapshot_id and plan["schema_version"] == "openpine.run-plan.4","Saved HTTP plan differs")
                window = http(f"/api/jobs/{job}/chart?start=1&limit=2")
                check("values" not in summary["result"]["outputs"][0], "Summary contains a large output array")
                check(window["outputs"][0]["values"] == expected[0][1:3], "Installed chart range differs")
            saved=http("/api/compile",{"source":(ROOT/"examples/pipeline/v6.pine").read_text(),"source_name":"v6.pine"})
            job=http("/api/jobs",{"schema_version":"openpine.job-request.3","input_ref":saved["input_ref"],
                "snapshot_id":snapshot_id,"timeout_seconds":30,"checkpoint_interval_bars":1,"pause_after_bars":2})["job_id"]
            deadline=time.monotonic()+45
            while time.monotonic()<deadline:
                state=http("/api/jobs/"+job)
                if state["status"]=="paused" or state["status"] in TERMINAL_STATUSES:break
                time.sleep(.04)
            check(state["status"]=="paused","Installed HTTP checkpoint did not pause")
            http(f"/api/jobs/{job}/resume",{})
            while time.monotonic()<deadline:
                state=http("/api/jobs/"+job)
                if state["status"] in TERMINAL_STATUSES:break
                time.sleep(.04)
            check(state["status"]=="succeeded" and state["resumed_from_bar"]==2,"Installed HTTP checkpoint resume failed")
            inputs=json.loads((example/"inputs.json").read_text(encoding="utf-8"))
            primary=http("/api/snapshots",{"provider":"upload","raw":(example/"bars.json").read_text(encoding="utf-8")})["sha256"]
            secondary=http("/api/snapshots",{"provider":"upload","raw":(example/"hourly.json").read_text(encoding="utf-8")})["sha256"]
            for version in (1,6):
                compiled=http("/api/compile",{"source":(example/f"v{version}.pine").read_text(encoding="utf-8"),"source_name":f"v{version}.pine"})
                job=http("/api/jobs",{"schema_version":"openpine.job-request.4","input_ref":compiled["input_ref"],"snapshot_id":primary,
                    "timeout_seconds":60,"primary_context":inputs["primary_context"],
                    "contexts":[{"context":inputs["contexts"][0]["context"],"snapshot_id":secondary}],
                    "checkpoint_interval_bars":11,"pause_after_bars":17})["job_id"]
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    state=http("/api/jobs/"+job)
                    if state["status"]=="paused" or state["status"] in TERMINAL_STATUSES:break
                    time.sleep(.04)
                check(state["status"]=="paused",f"Installed requested graph did not pause: {state}")
                http(f"/api/jobs/{job}/resume",{});deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    state=http("/api/jobs/"+job)
                    if state["status"] in TERMINAL_STATUSES:break
                    time.sleep(.04)
                check(state["status"]=="succeeded",f"Installed requested graph failed: {state}")
                check(http(f"/api/jobs/{job}/result")["result"]==context_cli[version],"Installed context CLI/HTTP differ")
            print('Installed acceptance: trading HTTP and CLI equality',file=sys.stderr,flush=True)
            inputs=json.loads((strategy_example/'inputs.json').read_text(encoding='utf-8'))
            primary=http('/api/snapshots',{'provider':'upload','raw':(strategy_example/'wave.json').read_text(encoding='utf-8')})['sha256']
            secondary=http('/api/snapshots',{'provider':'upload','raw':(strategy_example/'higher.json').read_text(encoding='utf-8')})['sha256']
            for version in (1,6):
                name=f'v{version}-filtered.pine'
                compiled=http('/api/compile',{'source':(strategy_example/name).read_text(encoding='utf-8'),'source_name':name})
                job=http('/api/jobs',{'schema_version':'openpine.job-request.4','input_ref':compiled['input_ref'],'snapshot_id':primary,
                    'timeout_seconds':60,'primary_context':inputs['primary_context'],
                    'contexts':[{'context':inputs['contexts'][0]['context'],'snapshot_id':secondary}],
                    'checkpoint_interval_bars':1,'pause_after_bars':2})['job_id']
                deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    state=http('/api/jobs/'+job)
                    if state['status']=='paused' or state['status'] in TERMINAL_STATUSES:break
                    time.sleep(.04)
                check(state['status']=='paused',f'Installed trading HTTP job did not pause: {state}')
                http(f'/api/jobs/{job}/resume',{});deadline=time.monotonic()+60
                while time.monotonic()<deadline:
                    state=http('/api/jobs/'+job)
                    if state['status'] in TERMINAL_STATUSES:break
                    time.sleep(.04)
                check(state['status']=='succeeded',f'Installed trading HTTP job failed: {state}')
                result=http(f'/api/jobs/{job}/result')['result'];check(result==strategy_cli[version],'Installed trading CLI/HTTP differ')
                page=http(f'/api/jobs/{job}/ledger?start=1&limit=2')
                check(page['events']==result['simulation']['events'][1:3],'Installed bounded ledger differs')
        finally:
            server.shutdown(); thread.join(timeout=10); server.server_close()
    print(json.dumps({"status": "passed", "python": sys.version.split()[0], "root": str(ROOT),
        "cli_versions": list(range(1, 7)), "saved_ir": True, "worker": True,
        "http_worker_versions": [1, 6], "cli_http_result_equal": True, "network_required": False,
        "sqlite_catalog": True, "parquet": True, "bounded_chart": True, "backup_replay": True,
        "run_plan":True,"gc_preview":True,"persisted_quota":True,
        "checkpoint_resume":True,"http_checkpoint_resume":True,"context_cli_http_versions":[1,6],
        "joint_context_resume":True,'strategy_cli_http_versions':[1,6],'joint_strategy_resume':True,'bounded_ledger':True}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
