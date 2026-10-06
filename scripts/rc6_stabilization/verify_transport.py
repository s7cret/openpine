#!/usr/bin/env python3
"""Re-read FIX06 transport and obligation inventory, not a new stage verdict owner."""
import argparse
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

NAMES = {'openpine','openpine-contracts','pine2ast','ast2python','pinelib','backtest_engine','marketdata-provider','optimizer'}

def check(root):
    root = Path(root).resolve()
    report = json.loads((root/'receipt.json').read_text())
    assert report['schema_id'] == 'openpine.installed_package_receipt.v1'
    unsigned = {k:v for k,v in report.items() if k != 'content_hash'}
    assert report['content_hash'] == 'sha256:' + hashlib.sha256(json.dumps(unsigned,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest(), 'receipt checksum mismatch'
    seen = set()
    def descriptor(d):
        relative = d['path']
        path = Path(relative)
        assert not path.is_absolute() and '..' not in path.parts
        target = root/path
        assert target.resolve().is_relative_to(root) and not target.is_symlink()
        assert 'sha256:'+hashlib.sha256(target.read_bytes()).hexdigest() == d['sha256'], ('checksum',relative)
        seen.add(relative)
    def walk(value):
        if isinstance(value,dict):
            if isinstance(value.get('path'),str) and isinstance(value.get('sha256'),str):
                descriptor(value)
            for v in value.values():
                walk(v)
        elif isinstance(value,list):
            for v in value:
                walk(v)
    walk(report)
    commands = json.loads((root/'commands.json').read_text())
    walk(commands)
    ids = [c['id'] for c in commands]
    assert len(ids) == len(set(ids)), 'duplicate obligation command'
    if 'artifacts' in report:
        artifacts = json.loads((root/'artifacts.json').read_text())
        assert set(artifacts) == NAMES
        walk(artifacts)
        required = {'source-before','copied-source','source-after'}
        required |= {'build-'+n for n in NAMES} | {'sdist-rebuild-'+n for n in NAMES}
        for version in ('3.13',):
            required |= {'venv-'+version,'version-'+version}
            for kind in ('wheel','rebuilt_wheel'):
                scope = version+'-'+kind
                required |= {'install-'+scope,'pip-check-'+scope}
                required |= {scope+'-'+mode for mode in ('origins','runtime','library','api','positive','missing','tampered','cli','source-shadow','shadow-isolated','tree-check')}
        assert set(ids) >= required, ('missing commands', sorted(required-set(ids)))
        assert json.loads((root/'source-before.json').read_text()) == json.loads((root/'source-after.json').read_text())
    failed = [c['id'] for c in commands if not c['ok']]
    assert failed == report['failed_commands']
    tests = ET.parse(root/'junit.xml').getroot()
    assert int(tests.attrib['tests']) == len(commands)
    assert int(tests.attrib['failures']) == len(failed)
    if report['ok']:
        assert not failed and not report['development'] and report['status'] == 'passed' and report['plan_hash'] and report['run_id'] != 'development'
    return {'transport_ok':True,'package_accepted':report['ok'],'development':report['development'],'status':report['status'],'command_count':len(commands),'failed_commands':failed,'verified_descriptors':len(seen),'source_hash':report.get('source_hash')}

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output',type=Path)
    args = parser.parse_args()
    print(json.dumps(check(args.output),sort_keys=True,indent=2))
