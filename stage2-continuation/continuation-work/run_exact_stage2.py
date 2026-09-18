#!/usr/bin/env python3
"""Run a sealed, eight-component Stage 2 source tree without fetching sibling HEADs.

Execution evidence is NOT semantic acceptance. No flag in an existing matrix is
rewritten. Missing tools, failed/empty/skipped pytest suites, interpreter mismatch,
source drift and timed-out children are explicit failed/blocked results.
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

COMPONENTS=("openpine-contracts","pine2ast","ast2python","pinelib",
            "backtest_engine","marketdata-provider","optimizer","openpine")
OMIT={".git","__pycache__",".pytest_cache",".mypy_cache",".ruff_cache",
      ".hypothesis",".venv","venv","node_modules","build","dist","htmlcov"}

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode()

def digest(value):
    return "sha256:"+hashlib.sha256(canonical(value)).hexdigest()

def snapshot(root):
    records={}
    for component in COMPONENTS:
        base=root/"sources"/component
        if not base.is_dir():
            raise ValueError(f"missing executable component: {component}")
        component_files={}
        for path in sorted(base.rglob("*")):
            relative=path.relative_to(base)
            if component == "openpine" and str(relative) in {
                "verification/stage2-source-lock.json", "verification/stage2-current-acceptance.json"
            }:
                # Receipt and its own source lock are outside the acyclic executable snapshot.
                # Their final bytes are still covered by DELIVERY_MANIFEST.json.
                continue
            if any(p in OMIT or p.endswith(".egg-info") for p in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"source symlink is not admitted: {path}")
            if not path.is_file() or path.suffix in {".pyc",".pyo"} or path.name==".coverage":
                continue
            component_files[str(relative)]="sha256:"+hashlib.sha256(path.read_bytes()).hexdigest()
        if not component_files:
            raise ValueError(f"empty component: {component}")
        records[component]=component_files
    return {"schema_id":"openpine.exact_candidate_execution_tree.v1",
            "components":records,"source_hash":digest(records)}

def clean_junit(path):
    try:
        tree=ET.parse(path).getroot()
    except (OSError,ET.ParseError) as exc:
        return {"ok":False,"reason":f"unreadable JUnit: {exc}"}
    cases=list(tree.iter("testcase"))
    totals={"tests":len(cases),"failures":0,"errors":0,"skipped":0}
    for case in cases:
        totals["failures"]+=len(case.findall("failure"))
        totals["errors"]+=len(case.findall("error"))
        totals["skipped"]+=len(case.findall("skipped"))
    # A collection/setup error may be represented on a testsuite rather than testcase.
    suites=[tree] if tree.tag=="testsuite" else list(tree.iter("testsuite"))
    for key in ("failures","errors","skipped"):
        totals[key]=max(totals[key],sum(int(s.get(key,"0")) for s in suites))
    totals["ok"]=bool(cases) and not any(totals[k] for k in ("failures","errors","skipped"))
    return totals

def run_process(command,cwd,env,log,timeout):
    started=time.monotonic()
    log.parent.mkdir(parents=True,exist_ok=True)
    with log.open("wb") as stream:
        try:
            proc=subprocess.Popen(command,cwd=cwd,env=env,stdout=stream,
                                  stderr=subprocess.STDOUT,start_new_session=True)
        except OSError as exc:
            stream.write(str(exc).encode())
            return {"status":"blocked","error":str(exc),"returncode":None}
        try:
            rc=proc.wait(timeout=timeout)
            return {"status":"passed" if rc==0 else "failed","returncode":rc,
                    "elapsed_seconds":round(time.monotonic()-started,3)}
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid,signal.SIGTERM)
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGKILL);proc.wait()
            return {"status":"failed","reason":"timeout","returncode":proc.returncode,
                    "elapsed_seconds":round(time.monotonic()-started,3)}

def interpreter_identity(executable,required):
    try:
        response=subprocess.run([executable,"-I","-c",
            "import sys,json;print(json.dumps({'version':list(sys.version_info[:3]),'executable':sys.executable}))"],
            capture_output=True,text=True,timeout=20,check=True)
        value=json.loads(response.stdout)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        return {"status":"blocked","error":str(exc),"required":required}
    if '.'.join(str(x) for x in value['version'][:2])!=required:
        return {"status":"blocked","error":"interpreter substitution refused","required":required,
                "observed":value}
    return {"status":"passed",**value}

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root",type=Path,default=Path(__file__).resolve().parent)
    ap.add_argument("--python311",default="python3.11")
    ap.add_argument("--python313",default="python3.13")
    ap.add_argument("--python",choices=("3.11","3.13","all"),default="all")
    ap.add_argument("--group",choices=("preflight","libraries","stack","packages","frontend","all"),default="all")
    ap.add_argument("--output",type=Path)
    ap.add_argument("--timeout",type=int,default=1800)
    args=ap.parse_args(argv)
    root=args.root.resolve()
    output=(args.output or root/"local-check-results"/"exact-stage2").resolve()
    if output==root or (root/"sources") in output.parents:
        ap.error("evidence must not overwrite executable sources")
    output.mkdir(parents=True,exist_ok=True)
    source=snapshot(root)
    report={"schema_id":"openpine.exact_candidate_execution.v1",
        "started_at":dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_hash":source["source_hash"],"group":args.group,"results":[],
        "runner_hash":"sha256:"+hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "package_driver_hash":"sha256:"+hashlib.sha256((root/"build_stage2_package.py").read_bytes()).hexdigest(),
        "semantic_acceptance":False,"full_stage2_accepted":False,
        "scope_note":"execution evidence only; catalogue, import and independent oracle criteria remain separate"}
    (output/"source-snapshot.json").write_bytes(canonical(source))
    env=dict(os.environ)
    env.update(PYTHONPATH=os.pathsep.join(str(root/"sources"/c) for c in COMPONENTS),
               PYTHONDONTWRITEBYTECODE="1",PYTHONNOUSERSITE="1",PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")
    def save():
        value=dict(report);value["content_hash"]=digest(value)
        (output/"report.json").write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n")
    def task(name,command,cwd,junit=None):
        before=snapshot(root)["source_hash"]
        row={"name":name,"command":command,"cwd":str(cwd.relative_to(root)),
             "source_hash_before":before,"log":name+".log"}
        if before!=source["source_hash"]:
            row.update(status="blocked",reason="source tree changed before task")
        else:
            row.update(run_process(command,cwd,env,output/row["log"],args.timeout))
            row['source_hash_after']=snapshot(root)['source_hash']
            if row['source_hash_after']!=before:
                row.update(status="failed",reason="source tree changed during task")
            if junit is not None:
                row["junit"]=clean_junit(junit)
                if row["status"]=="passed" and not row["junit"]["ok"]:
                    row.update(status="failed",reason="empty, skipped or failing test result")
        report["results"].append(row);save()
        print(name+": "+row["status"],flush=True)
    versions=[("3.11",args.python311),("3.13",args.python313)]
    for required,executable in ([] if args.group=="frontend" else versions):
        if args.python!="all" and required!=args.python:continue
        identity=interpreter_identity(executable,required)
        report["results"].append({"name":"interpreter-"+required,**identity});save()
        if identity["status"]!="passed":continue
        if args.group=="preflight":
            task("dependencies-"+required,[executable,"-I","-c",
                "import pytest,build,hatchling,structlog;print('declared test/build preflight OK')"],root)
            continue
        if args.group in ("libraries","stack","all"):
            selected=("pine2ast","ast2python","pinelib") if args.group=="libraries" else COMPONENTS
            for component in selected:
                cwd=root/"sources"/component
                paths=[p for p in ("tests","rc6_tests") if (cwd/p).is_dir()]
                if not paths:
                    report["results"].append({"name":f"{component}-{required}","status":"blocked","reason":"no declared suite"});save();continue
                name=f"pytest-{component}-{required}";junit=output/(name+".xml")
                command=[executable,"-m","pytest","-q","--disable-warnings",*paths,"--junitxml="+str(junit)]
                if component=="marketdata-provider":command.extend(["-m","not live_network"])
                task(name,command,cwd,junit)
        if args.group in ("packages","all"):
            for component in COMPONENTS:
                dest=output/"packages"/required/component;dest.mkdir(parents=True,exist_ok=True)
                task(f"build-{component}-{required}",[executable,str(root/"build_stage2_package.py"),"--outdir",str(dest)],root/"sources"/component)
    if args.group in ("stack","all","preflight"):
        worker={"name":"protected-worker-execution","status":"blocked"}
        if not shutil.which("bwrap"):
            worker["reason"]="bubblewrap unavailable; no in-process replacement"
        elif args.group=="preflight":
            worker["reason"]="bubblewrap present; preflight is not an execution receipt"
        else:
            # Native suite names come from the delivered authoritative workflow, not
            # an arbitrary user-supplied command or a synthetic PASS JSON document.
            workflow=root/"sources"/"openpine"/".github"/"workflows"/"rc6-native.yml"
            text=workflow.read_text() if workflow.exists() else ""
            names=sorted(set(re.findall(r"(?:rc6_tests|tests)/([A-Za-z_0-9/]*(?:native|protected)[A-Za-z_0-9]*worker[A-Za-z_0-9]*\.py|[A-Za-z_0-9/]*worker[A-Za-z_0-9]*(?:native|protected)[A-Za-z_0-9]*\.py)",text)))
            observations=[]
            for row in report["results"]:
                if row["name"].startswith("pytest-openpine-") and row.get("status")=="passed":
                    jp=output/(row["name"]+".xml")
                    cases=list(ET.parse(jp).getroot().iter("testcase"))
                    matched=[x for x in cases if any(Path(n).stem in x.get("classname","") for n in names)]
                    if matched and all(not any(x.find(tag) is not None for tag in ("failure","error","skipped")) for x in matched):
                        observations.append({"suite":row["name"],"cases":[x.get("classname","")+"::"+x.get("name","") for x in matched]})
            required_count=2 if args.python=="all" else 1
            if names and len(observations)==required_count:
                worker.update(status="passed",native_workflow_suites=names,observations=observations,
                    source_hash=source["source_hash"])
            else:
                worker.update(reason="native protected-worker execution not established by clean exact-tree host suites",
                    native_workflow_suites=names,observations=observations)
        report["results"].append(worker);save()
    if args.group in ("frontend","all"):
        frontend=root/"sources"/"openpine"/"frontend"
        if not (frontend/"package.json").is_file():
            candidates=[p.parent for p in (root/"sources"/"openpine").rglob("package.json") if "node_modules" not in p.parts]
            frontend=next((p for p in candidates if "web" in p.parts),candidates[0] if candidates else frontend)
        if not (frontend/"package.json").is_file():
            report["results"].append({"name":"frontend","status":"blocked","reason":"package.json not found"});save()
        else:
            package=json.loads((frontend/"package.json").read_text());scripts=package.get("scripts",{})
            for name in ("typecheck","test:unit","test","build"):
                if name not in scripts:continue
                command=["npm","run",name]
                if "vitest" in scripts[name] and "run" not in scripts[name].split():command.extend(["--","--run"])
                task("frontend-"+name.replace(":","-"),command,frontend)
    report['final_source_hash']=snapshot(root)['source_hash']
    report['source_unchanged']=report['final_source_hash']==source['source_hash']
    report['execution_group_clean']=bool(report['results']) and report['source_unchanged'] and all(r['status']=='passed' for r in report['results'])
    report['finished_at']=dt.datetime.now(dt.timezone.utc).isoformat();save()
    return 0 if report['execution_group_clean'] else 1

if __name__=="__main__":raise SystemExit(main())
