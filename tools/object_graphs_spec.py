"""Reproduce the additive reference-field profile from immutable Wiki records."""
import argparse
from tools.specification import ROOT
from packages.contracts.object_graphs import policy
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(ref)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','source_record_sha256','sources')} for e in ref['entries'] if e['name']=='type' or e['entry_id'].endswith((':udt-declaration',':udt-field-varip',':udt-new'))]
    return {'schema_version':'openpine.object-graphs-spec.1','release':'pine-object-graphs-20261102.1','source_lock_sha256':digest(lock),
        'versions':versions,'evidence':evidence,'review':{'external_execution_verified':False,
        'primary_sources':['https://www.tradingview.com/pine-script-docs/v5/language/type-system/',
            'https://www.tradingview.com/pine-script-docs/language/type-system/',
            'https://www.tradingview.com/pine-script-docs/v5/language/objects/',
            'https://www.tradingview.com/pine-script-docs/language/objects/',
            'https://www.tradingview.com/pine-script-docs/v5/language/arrays/',
            'https://www.tradingview.com/pine-script-docs/language/variable-declarations/'],
        'decisions':['Reference fields support self and previously declared UDT types and the existing closed collection types in v5/v6.',
            'Reference defaults are typed na; constructors still evaluate explicit arguments once in written order.',
            'copy is shallow; cyclic runtime graphs use iterative, charged reachability and typed integer IDs in a two-pass codec.',
            'Rollback selects ordinary fields from confirmed/constructor baselines before traversing the retained graph. Marked fields carry references; UDT child fields require their own marks.',
            'Varip collections admit value fields and value-only collection fields; direct UDT fields are explicitly outside this bounded eligibility rule.',
            'Only graph programs use typed-ir.19/runtime-state.12/array-heap.5. Imported profiles and previous program schemas remain unchanged.']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    target=ROOT/'spec/object-graphs-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert target.read_bytes()==raw,'Object graph profile differs.'
    else:target.write_bytes(raw)
    print('Additive object graph profile verified.')
if __name__=='__main__':main()
