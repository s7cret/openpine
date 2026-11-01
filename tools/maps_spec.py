"""Reproduce the additive map profile from immutable Wiki reference records."""
import argparse
from tools.specification import ROOT
from packages.contracts.maps import policy
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');refs={v:read_json(ROOT/lock['snapshot']/f'reference/v{v}.json') for v in range(1,7)}
    return {'schema_version':'openpine.maps-spec.1','release':'pine-maps-20261101.1','source_lock_sha256':digest(lock),
        'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(refs[v])} for v in refs},
        'evidence':{str(v):[{k:e[k] for k in ('entry_id','source_record_sha256','sources','parameters','result_type_spellings')} for e in refs[v]['entries'] if e['name'].startswith('map.')] for v in refs},
        'review':{'external_execution_verified':False,'primary_sources':['https://www.tradingview.com/pine-script-docs/v5/language/maps/','https://www.tradingview.com/pine-script-docs/language/maps/'],
            'decisions':['Maps and methods require v5/v6. V1-v4 receive explicit MAP_VERSION diagnostics.',
                'Keys are existing scalar or nominal enum types; values add flat UDT. No color, graphics, nested collections or reference fields.',
                'Map.put returns the previous value or typed missing; get/remove of absent key do likewise. Missing bool results are false in v6.',
                'Typed na keys share one canonical missing key. Finite floating keys use numeric equality, including equal signed zero. This is an explicit bounded interpretation of sparse reference records.',
                'Existing keys retain insertion position and original key spelling when replaced; remove then reinsert appends.',
                'For-in snapshots keys at entry, reads the current value, and yields missing for a removed key; new keys are not visited. Mutation during traversal is an explicit OpenPine interpretation.',
                'Shared map-to-flat-record heap edges, shallow copies, COW previews, field-level varip and two physical cells per pair. Lookup index storage is included in the memory ledger.',
                'Mutations stage the complete map; cost/scratch grow with its size. Work/storage/cancellation failures do not publish partial slots or a new ID. Existing global limits are preserved.',
                'Map programs use typed-ir.17/runtime-state.10/array-heap.3; previous program formats remain. Imported Wiki bundles/profiles are unchanged.'],
            'test_refs':['tests/test_maps.py','conformance/maps/cases.json']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/maps-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Map profile differs.'
    else:path.write_bytes(raw)
    print('Additive maps profile verified.')
if __name__=='__main__':main()
