"""Install a verified shared wheel into an empty target; execute outside checkout."""
import argparse,json,subprocess,sys,tempfile,threading
from pathlib import Path
from tools.build_backend import ROOT
from tools.ci_artifacts import build,validate_wheel,source_identity,platform_id
from packages.contracts.wire import read_json,atomic_json

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pip-runner',type=Path)
    from tools.installed_suites import SUITES
    p.add_argument('--suite',choices=('all',*SUITES),default='all');p.add_argument('--wheel',type=Path);p.add_argument('--manifest',type=Path);p.add_argument('--wheel-dir',type=Path);p.add_argument('--receipt',type=Path);args=p.parse_args(argv)
    if args.wheel_dir and (args.wheel or args.manifest):p.error('Use --wheel-dir OR --wheel with --manifest.')
    if args.wheel_dir:
        args.manifest=args.wheel_dir/'manifest.json';metadata=read_json(args.manifest)
        if Path(metadata['wheel']).name!=metadata['wheel']:p.error('Wheel manifest contains an unsafe filename.')
        args.wheel=args.wheel_dir/metadata['wheel']
    if bool(args.wheel)!=bool(args.manifest):p.error('An external wheel requires its source-bound manifest.')
    with tempfile.TemporaryDirectory(prefix='openpine-install-') as directory:
        work=Path(directory)
        if args.wheel:wheel=args.wheel.resolve();manifest=validate_wheel(wheel,args.manifest)
        else:manifest=build(work/'artifact');wheel=work/'artifact'/manifest['wheel']
        site=work/'site';pip=[str(args.pip_runner.resolve())] if args.pip_runner else ['-m','pip']
        subprocess.run([sys.executable,'-X','utf8',*pip,'--isolated','--disable-pip-version-check','install','--no-index','--no-deps','--no-compile','--no-warn-script-location','--target',str(site),str(wheel)],cwd=work,check=True)
        expected={'build':manifest['source']['build'],'implementation_sha256':manifest['source']['implementation_sha256']}
        bootstrap="import runpy,sys,json;site=sys.argv.pop(1);old=sys.argv.pop(1);expected=json.loads(sys.argv.pop(1));sys.path[:]=[site,*[p for p in sys.path if p and p!=old]];from packages.execution.identity import build_identity,implementation_hash;assert build_identity()==expected['build'] and implementation_hash()==expected['implementation_sha256'];runpy.run_module('tools.installed_smoke',run_name='__main__')"
        command=[sys.executable,'-X','utf8','-c',bootstrap,str(site),str(ROOT),json.dumps(expected),'--expected-root',str(site),'--suite',args.suite]
        process=subprocess.Popen(command,cwd=work,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8');expired=threading.Event()
        def expire():expired.set();process.kill()
        timer=threading.Timer(5400,expire);timer.daemon=True;timer.start();results=[];size=0
        try:
            for line in process.stdout:
                size+=len(line)
                if size>32*1024*1024:process.kill();raise RuntimeError('Installed acceptance output exceeds 32 MiB.')
                print(line,end='',flush=True)
                if line.startswith('{') and '"installed_suite"' in line:results.append(json.loads(line))
            code=process.wait()
        finally:
            timer.cancel();process.stdout.close()
            if process.poll() is None:process.kill();process.wait()
        if expired.is_set():raise subprocess.TimeoutExpired(command,5400)
        if code:raise subprocess.CalledProcessError(code,command)
        assert len(results)==1 and results[0]['installed_suite']==args.suite and results[0]['status']=='passed'
        assert source_identity()==manifest['source'],'Checkout changed while the installed wheel was under test.'
        receipt={'schema_version':'openpine.installed-receipt.1','status':'passed','source':manifest['source'],'platform':platform_id(),'suite':args.suite,'wheel_sha256':manifest['wheel_sha256'],'result':results[0]}
        if args.receipt:atomic_json(args.receipt,receipt)
    return 0
if __name__=='__main__':raise SystemExit(main())
