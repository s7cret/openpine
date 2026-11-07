"""Bind signal policy and independently retained pinned default claims."""
import argparse
from tools.specification import ROOT
from packages.contracts.markers import policy,apis
from packages.contracts.wire import canonical,digest,read_json
from tools.colors_spec import assert_overlay

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        r=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        entries=[e for e in r['entries'] if e['name'] in ('bgcolor','barcolor','plotshape','plotchar')]
        assert {e['name'] for e in entries}=={'bgcolor','barcolor','plotshape','plotchar'}
        for e in entries:
            for p in e['parameters']:
                expected={'offset':'0','style':'shape.xcross','location':'location.abovebar','size':'size.auto'}.get(p['name'])
                if expected:assert p['default_claim']['state']=='explicit_value' and p['default_claim']['spellings']==[expected]
        signatures=apis(v)
        for name in ('bgcolor','plotshape','plotchar'):assert_overlay(r,name,signatures,v)
        versions[str(v)]={'policy':policy(v),'apis':signatures,'reference_sha256':digest(r)}
        evidence[str(v)]=[dict(name=e['name'],basis=e['basis'],entry_id=e['entry_id'],source_record_sha256=e['source_record_sha256'],
            syntax=e['syntax_spellings'],parameters=e['parameters']) for e in entries]
    return dict(schema_version='openpine.visual-signals-spec.1',source_lock_sha256=digest(lock),versions=versions,evidence=evidence)

def main(argv=None):
    p=argparse.ArgumentParser();p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/visual-signals-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Visual signal profile is stale.'
    else:path.write_bytes(raw)
    print('Visual signal profile verified.')
if __name__=='__main__':main()
