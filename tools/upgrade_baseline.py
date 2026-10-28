"""Pin an offline previous production tree from a verified qualification wheel."""
import argparse
import ast
import hashlib
import json
import re
import zipfile
from pathlib import Path

from packages.contracts.wire import atomic_json, read_json
from tools.ci_artifacts import verify_record, verify_wheel_sources


def pin(wheel, manifest, destination, *, expected_sha256, source_commit):
    wheel,destination=Path(wheel),Path(destination)
    metadata=read_json(manifest)
    if not re.fullmatch('[0-9a-f]{40}',source_commit):raise ValueError('Use the qualified commit SHA.')
    actual=hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual!=expected_sha256 or metadata['wheel_sha256']!=actual:raise ValueError('Baseline wheel differs from qualification.')
    verify_record(wheel);verify_wheel_sources(wheel,metadata['source'])
    if destination.exists():raise ValueError('Use a new fixture destination, then review its replacement in Git.')
    extras={'pyproject.toml','requirements-dev.txt','.python-version','.gitattributes','LICENSE','tools/__init__.py','tools/specification.py',
            *[f'tests/{name}.py' for name in ('test_jobs','test_realtime','test_experiments','test_inputs')]}
    destination.mkdir(parents=True);target=destination/'baseline.zip';files={}
    with zipfile.ZipFile(wheel) as source,zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as output:
        tree=ast.parse(source.read('packages/storage/database.py').decode('utf-8'))
        schema=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SCHEMA_VERSION' for t in n.targets))
        for name in sorted(source.namelist()):
            if name.startswith(('packages/','apps/','spec/','examples/pipeline/')) or name in extras:
                raw=source.read(name);files[name]=hashlib.sha256(raw).hexdigest()
                info=zipfile.ZipInfo(name,date_time=(2026,10,21,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o644<<16
                output.writestr(info,raw)
    value={'schema_version':'openpine.upgrade-baseline.1','source_commit':source_commit,'catalog_schema':schema,
           'archive':'baseline.zip','archive_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'wheel_sha256':actual,
           'build':metadata['source']['build'],'implementation_sha256':metadata['source']['implementation_sha256'],'files':files}
    atomic_json(destination/'baseline.json',value);return value


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('wheel','manifest','destination'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--expected-sha256',required=True);p.add_argument('--source-commit',required=True)
    args=p.parse_args(argv);value=pin(args.wheel,args.manifest,args.destination,expected_sha256=args.expected_sha256,source_commit=args.source_commit)
    print(json.dumps({'status':'pinned','source_commit':value['source_commit'],'archive_sha256':value['archive_sha256'],'members':len(value['files'])}));return 0


if __name__=='__main__':raise SystemExit(main())
