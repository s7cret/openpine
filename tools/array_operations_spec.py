"""Reproduce an additive profile without rewriting the original array bundle."""
import argparse
from tools.specification import ROOT
from packages.contracts.array_operations import policy,OPERATIONS
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json');refs={v:read_json(ROOT/lock['snapshot']/f'reference/v{v}.json') for v in range(1,7)}
    return {'schema_version':'openpine.array-operations-spec.1','release':'pine-array-operations-20261031.1',
        'source_lock_sha256':digest(lock),'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(refs[v])} for v in refs},
        'evidence':{str(v):[{k:e[k] for k in ('entry_id','source_record_sha256','sources','parameters','result_type_spellings')}
            for e in refs[v]['entries'] if e['name'] in {'array.'+op for op in OPERATIONS}] for v in refs},
        'review':{'external_execution_verified':False,'primary_sources':[
            'https://www.tradingview.com/pine-script-docs/v4/essential/arrays/',
            'https://www.tradingview.com/pine-script-docs/v5/language/arrays/',
            'https://www.tradingview.com/pine-script-docs/language/arrays/'],
            'decisions':[
                'Eight operations in the pinned v4-v6 references; v1-v3 reject arrays. Namespace calls v4-v6, methods v5/v6.',
                'V4 generic element behavior reuses the existing bounded primitive-array reconstruction; the historical reference signatures do not fully enumerate every admitted primitive combination.',
                'Keep the immutable original array bundle and original methods profile bytes unchanged; use this explicit overlay.',
                'Insert accepts 0..size, including empty append. V6 insert/remove also normalize negative indices by current size; fill bounds remain nonnegative.',
                'Fill defaults are 0 and na. Omitted or explicit na index_to means the current size after explicit arguments execute. Equal half-open bounds are an explicit OpenPine no-op interpretation.',
                'Search matches defined scalar values, nominal enum members and UDT identities. Missing matches missing is an explicit bounded interpretation where the manuals do not specify search-NA behavior; no external equivalence claim.',
                'Concat appends to and returns the first ID; the second array is unchanged unless it aliases the first. Self-concat appends the original buffer once.',
                'Bulk changes preflight indices, work, storage and scratch memory before replacing one buffer. Existing array-to-record reference counts, COW, varip and checkpoint graph remain authoritative.',
                'No new heap kind or persisted state format. Slice, sorting, nested collections and new request-expression types remain excluded.'],
            'test_refs':['tests/test_array_operations.py','conformance/array-operations/cases.json']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/array-operations-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Array operations profile differs.'
    else:path.write_bytes(raw)
    print('Additive array operations profile verified.')

if __name__=='__main__':main()
