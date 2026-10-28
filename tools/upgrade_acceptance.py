"""Actual two-wheel upgrade and incompatible-build proof, outside both trees."""
import argparse
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

from tools.specification import ROOT, require_python
from packages.contracts.wire import atomic_json
from packages.execution.identity import build_identity, implementation_hash


def run(directory, previous_wheel=None, expected_sha256=None, *, fixture_manifest=None):
    require_python()
    assert sys.version_info[:3]==(3,14,8)
    directory=Path(directory).resolve()
    baseline=None
    if fixture_manifest is not None:
        from packages.contracts.wire import read_json
        fixture_manifest=Path(fixture_manifest).resolve();baseline=read_json(fixture_manifest)
        if baseline['schema_version']!='openpine.upgrade-baseline.1' or baseline['archive']!='baseline.zip':raise ValueError('Invalid baseline fixture.')
        previous_wheel=fixture_manifest.parent/baseline['archive'];expected_sha256=baseline['archive_sha256']
    previous_wheel=Path(previous_wheel).resolve()
    if directory.exists():raise ValueError('Use a new acceptance directory.')
    actual=hashlib.sha256(previous_wheel.read_bytes()).hexdigest()
    if actual!=expected_sha256:raise ValueError('Previous wheel hash differs from the qualified artifact.')
    directory.mkdir(parents=True);old=directory/'previous';old.mkdir()
    with zipfile.ZipFile(previous_wheel) as archive:
        if sum(i.file_size for i in archive.infolist())>128*1024**2:raise ValueError('Previous wheel exceeds fixture limit.')
        for member in archive.infolist():
            if not (old/member.filename).resolve().is_relative_to(old):raise ValueError('Unsafe wheel member.')
        if baseline:
            assert set(archive.namelist())==set(baseline['files'])
            assert all(hashlib.sha256(archive.read(name)).hexdigest()==sha for name,sha in baseline['files'].items())
        archive.extractall(old)
    probe=ROOT/'tools/upgrade_probe.py';store=directory/'store';receipt=directory/'seed.json'
    bootstrap="import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
    def command(args,name):
        process=subprocess.run([sys.executable,'-X','utf8',*map(str,args)],cwd=directory,capture_output=True,text=True,encoding='utf-8',
                               timeout=240,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        (directory/(name+'.log')).write_text(process.stdout+process.stderr,encoding='utf-8')
        if process.returncode:raise AssertionError(name+': '+process.stdout[-2000:]+process.stderr[-2000:])
        return json.loads(process.stdout.strip().splitlines()[-1])
    command([probe,'seed','--root',old,'--store',store,'--receipt',receipt],'seed')
    if baseline:
        seed=json.loads(receipt.read_text(encoding='utf-8'))
        assert seed['identity']=={k:baseline[k] for k in ('build','implementation_sha256')}
        assert seed['schema']==baseline['catalog_schema']
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store.glob('catalog.sqlite3*')}
    # Attention exit code is expected before migration; inspect through the API
    # in a distinct process so the old/new package imports cannot be mixed.
    inspection="import sys,json;sys.path.insert(0,sys.argv[1]);from apps.openpine.diagnostics import doctor;print(json.dumps(doctor(sys.argv[2])))"
    report=command(['-c',inspection,ROOT,store],'doctor-before')
    assert report['catalog']['stored']==15 and report['catalog']['action']=='upgrade_required',report
    assert before=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in store.glob('catalog.sqlite3*')}
    migrated=command(['-c',bootstrap,ROOT,'storage','upgrade','--state-dir',store,'--destination',directory/'pre-upgrade'],'upgrade')
    assert migrated['phase']=='complete' and migrated['from_schema']==15
    compatible=command([probe,'continue','--root',ROOT,'--store',store,'--receipt',receipt],'compatible')
    # Copy the current wheel layout, then make a computation-file change only
    # in the isolated fixture. The production checkout never changes.
    from tools.build_backend import build_wheel
    wheel=directory/build_wheel(directory);changed=directory/'changed';changed.mkdir()
    with zipfile.ZipFile(wheel) as archive:archive.extractall(changed)
    with (changed/'packages/compiler/__init__.py').open('a',encoding='utf-8') as stream:stream.write('\n# Deliberately incompatible acceptance fixture.\n')
    incompatible=command([probe,'incompatible','--root',changed,'--store',store,'--receipt',receipt],'incompatible')
    value={'schema_version':'openpine.upgrade-proof.1','status':'passed','python':sys.version.split()[0],
           'previous_kind':'production_fixture' if baseline else 'complete_wheel',
           'previous_archive_sha256':actual,'previous_wheel_sha256':baseline['wheel_sha256'] if baseline else actual,
           'previous_source_commit':baseline['source_commit'] if baseline else None,
           'build':build_identity(),'implementation_sha256':implementation_hash(),
           'from_schema':15,'to_schema':migrated['to_schema'],'doctor_left_source_unchanged':True,
           'compatible':compatible,'incompatible':incompatible}
    atomic_json(directory/'report.json',value);return value


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True)
    previous=p.add_mutually_exclusive_group(required=True)
    previous.add_argument('--previous-wheel',type=Path);previous.add_argument('--previous-fixture',type=Path)
    p.add_argument('--expected-sha256')
    args=p.parse_args(argv)
    if args.previous_wheel and not args.expected_sha256:p.error('A previous wheel requires its qualified SHA256.')
    print(json.dumps(run(args.directory,args.previous_wheel,args.expected_sha256,fixture_manifest=args.previous_fixture)));return 0


if __name__=='__main__':raise SystemExit(main())
