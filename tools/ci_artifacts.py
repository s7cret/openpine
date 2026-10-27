"""Content-bound qualification receipts and one shared portable wheel."""
import argparse,base64,csv,hashlib,io,json,math,platform,sys,unittest,zipfile
from pathlib import Path,PurePosixPath
from packages.contracts.wire import atomic_json,read_json,digest
from packages.execution.identity import implementation_hash,build_identity
from tools.specification import ROOT
from tools.installed_suites import SUITES
from tools.test_shards import partition,weights,identity

PLATFORMS=('ubuntu-latest','windows-latest')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def platform_id():return 'windows-latest' if sys.platform=='win32' else 'ubuntu-latest' if sys.platform.startswith('linux') else sys.platform
def conformance_inventory():
    corpora=[]
    for path in sorted((ROOT/'conformance').glob('*/cases.json')):
        values=read_json(path)['cases'];executed=sum(c['expected']['status']=='executed' for c in values)
        corpora.append({'path':path.relative_to(ROOT).as_posix(),'sha256':sha(path),'cases':len(values),'executed':executed,'rejected':len(values)-executed})
    return {'cases':sum(c['cases'] for c in corpora),'executed':sum(c['executed'] for c in corpora),'rejected':sum(c['rejected'] for c in corpora),'corpora':corpora}
def modules():
    from tools.release_gate import PRIORITY
    return [*PRIORITY,*sorted(p.name for p in (ROOT/'tests').glob('test_*.py') if p.name not in PRIORITY)]
def source_identity():
    files={}
    for name in ('packages','apps','tools','spec','examples','conformance','tests','.github'):
        for path in sorted((ROOT/name).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc' and (path.name!='.gitkeep' or name in ('packages','apps')) and not path.is_relative_to(ROOT/'conformance/results'):
                files[path.relative_to(ROOT).as_posix()]=sha(path)
    for name in ('pyproject.toml','requirements-dev.txt','.python-version','.gitattributes','LICENSE'):files[name]=sha(ROOT/name)
    value={'python':platform.python_version(),'implementation_sha256':implementation_hash(),'build':build_identity(),'files':files,'modules':modules(),'weights_sha256':identity(),'conformance':conformance_inventory()}
    return {**value,'sha256':digest(value)}
def validate_source(value):
    assert value['sha256']==digest({k:v for k,v in value.items() if k!='sha256'}),'Source receipt checksum differs.'
    assert value==source_identity(),'Qualification belongs to different source or dependencies.'
def verify_record(wheel):
    with zipfile.ZipFile(wheel) as archive:
        names=archive.namelist();assert len(names)==len(set(names)),'Duplicate wheel members.'
        assert all(not n.startswith('/') and '\\' not in n and '..' not in PurePosixPath(n).parts for n in names),'Unsafe wheel member path.'
        records=[n for n in names if n.endswith('.dist-info/RECORD')];assert len(records)==1
        rows=list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
        assert {r[0] for r in rows}==set(names) and len(rows)==len(names)
        for name,expected,size in rows:
            if name==records[0]:assert expected==size=='';continue
            raw=archive.read(name);actual=base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
            assert expected=='sha256='+actual and len(raw)==int(size),'Wheel RECORD differs.'
        return names
def verify_wheel_sources(wheel,source):
    from tools.build_backend import metadata_files
    dist,metadata=metadata_files()
    with zipfile.ZipFile(wheel) as archive:
        names=set(archive.namelist());expected={n for n in source['files'] if not n.startswith('.github/')}
        assert expected<=names,'Missing wheel source members.'
        for name in expected:assert hashlib.sha256(archive.read(name)).hexdigest()==source['files'][name],('Wheel source differs',name)
        for name,raw in metadata.items():assert archive.read(name)==raw,('Wheel metadata differs',name)
        allowed=expected|set(metadata)|{dist+'/RECORD','README.md'}
        assert all(n in allowed or n.startswith('docs/') and PurePosixPath(n).suffix in ('.md','.json','.txt','.html','.css','.js') for n in names),'Foreign wheel source member.'
def build(directory):
    from tools.build_backend import build_wheel
    directory=Path(directory);before=source_identity();wheel=directory/build_wheel(directory);names=verify_record(wheel)
    verify_wheel_sources(wheel,before)
    assert before==source_identity(),'Source changed during wheel construction.'
    value={'schema_version':'openpine.ci-wheel.1','status':'passed','source':before,'wheel':wheel.name,'wheel_sha256':sha(wheel),'suites':list(SUITES),'record_members':len(names)}
    atomic_json(directory/'manifest.json',value);return value
def validate_wheel(wheel,manifest):
    wheel=Path(wheel);value=read_json(manifest)
    assert value['schema_version']=='openpine.ci-wheel.1' and value['status']=='passed' and value['suites']==list(SUITES)
    validate_source(value['source']);assert value['wheel']==wheel.name and value['wheel_sha256']==sha(wheel)
    assert len(verify_record(wheel))==value['record_members'];verify_wheel_sources(wheel,value['source']);return value
def expected_cases():
    suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
    def walk(value):
        if isinstance(value,unittest.TestSuite):
            for child in value:yield from walk(child)
        else:yield value.id()
    cases=list(walk(suite));assert not any('FailedTest' in c for c in cases)
    return set(cases)
def audit_receipts(specs,shards,installed,wheel,source,cases):
    assert wheel['source']==source and wheel['suites']==list(SUITES)
    assert len(specs)==2 and {r['platform'] for r in specs}==set(PLATFORMS)
    from tools.release_gate import specification_checks
    from tools.installed_suites import GROUPS
    expected_checks=['architecture',*[name for name,_ in specification_checks()]]
    for r in specs:
        assert r['status']=='passed' and r['section']=='specification' and not r['fast'] and r['assigned_modules']==[] and r['source']==source
        assert [c['check'] for c in r['checks']]==expected_checks and all(c['exit_code']==0 for c in r['checks'])
        assert all(r['conformance'][k]==source['conformance'][k] for k in ('cases','executed','rejected','corpora'))
    assert len(shards)==12 and {(r['platform'],r['shard_index']) for r in shards}=={(p,i) for p in PLATFORMS for i in range(6)}
    for os in PLATFORMS:
        found=[];seen=[];parts=partition(source['modules'],6,*weights(os))
        for r in (r for r in shards if r['platform']==os):
            parent=next(s for s in specs if s['platform']==os)
            assert r['status']=='passed' and r['section']=='tests' and not r['fast'] and r['source']==source and r['qualification_sha256']==digest(parent)
            assert r['shard_count']==6 and r['assigned_modules']==parts[r['shard_index']]
            assert [c['check'] for c in r['checks']]==['architecture',*r['assigned_modules']] and all(c['exit_code']==0 for c in r['checks'])
            assert [m['module'] for m in r['module_results']]==r['assigned_modules']
            for m in r['module_results']:
                assert m['successful'] and m['tests']==len(m['cases']) and all(c['test'].startswith(m['module'][:-3]+'.') for c in m['cases']);found.append(m['module']);seen.extend(c['test'] for c in m['cases'])
        assert sorted(found)==sorted(source['modules']) and len(seen)==len(set(seen)) and set(seen)==cases,('Missing, duplicated or foreign tests',os)
    assert len(installed)==2*len(SUITES) and {(r['platform'],r['suite']) for r in installed}=={(p,s) for p in PLATFORMS for s in SUITES}
    for r in installed:
        assert r['schema_version']=='openpine.installed-receipt.1' and r['status']=='passed' and r['source']==source and r['wheel_sha256']==wheel['wheel_sha256'] and r['result']['installed_suite']==r['suite'] and r['result']['status']=='passed'
        assert r['result']['python']==source['python'] and r['result']['network_required'] is False and r['result']['root']!=str(ROOT)
        if r['suite']=='core':assert r['result']['completed_core'] is True
        else:assert r['result']['completed_helpers']==list(GROUPS[r['suite']])
    return {'schema_version':'openpine.ci-acceptance.1','status':'passed','source_sha256':source['sha256'],'python':source['python'],'tests_per_os':len(cases),'modules_per_os':len(source['modules']),'conformance_per_os':source['conformance']['cases'],'specification_runs':len(specs),'shared_wheel_sha256':wheel['wheel_sha256'],'wheel_builds':1,'installed_suites_per_os':list(SUITES),'platforms':list(PLATFORMS)}
def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('build','verify-wheel','accept'));p.add_argument('--directory',type=Path);p.add_argument('--wheel',type=Path);p.add_argument('--manifest',type=Path);p.add_argument('--receipts',type=Path);p.add_argument('--output',type=Path);args=p.parse_args(argv)
    if args.action=='build':result=build(args.directory)
    elif args.action=='verify-wheel':
        if args.directory:
            args.manifest=args.directory/'manifest.json';name=read_json(args.manifest)['wheel'];assert Path(name).name==name;args.wheel=args.directory/name
        result=validate_wheel(args.wheel,args.manifest)
    else:
        source=source_identity();specs=[];shards=[];installed=[];wheels=[]
        for path in args.receipts.rglob('*.json'):
            value=read_json(path)
            if value.get('schema_version')=='openpine.ci-wheel.1':wheels.append(value)
            elif value.get('schema_version')=='openpine.gate-receipt.2':
                (specs if value['section']=='specification' else shards).append(value)
                if value['section']=='specification':
                    for item in value['conformance']['reports']:
                        report_path=path.parent/'conformance'/item['file'];assert report_path.resolve().is_relative_to((path.parent/'conformance').resolve())
                        assert sha(report_path)==item['sha256'];report=read_json(report_path)
                        assert report['python']==source['python'] and report['implementation_sha256']==source['implementation_sha256'] and all(c['passed'] for c in report['results'])
            elif value.get('schema_version')=='openpine.installed-receipt.1':installed.append(value)
        assert len(wheels)==1;result=audit_receipts(specs,shards,installed,wheels[0],source,expected_cases())
        result['receipt_sha256']={str(p.relative_to(args.receipts)):sha(p) for p in sorted(args.receipts.rglob('*.json'))}
    if args.output:atomic_json(args.output,result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('source','receipt_sha256')},sort_keys=True));return 0
if __name__=='__main__':raise SystemExit(main())
