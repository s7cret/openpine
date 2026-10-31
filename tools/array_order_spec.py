"""Reproduce additive array ordering contracts from pinned versioned evidence."""
import argparse
from tools.specification import ROOT
from packages.contracts.array_order import policy,OPERATIONS,RANK
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');refs={v:read_json(ROOT/lock['snapshot']/f'reference/v{v}.json') for v in range(1,7)}
    names={'array.'+op for op in (*OPERATIONS,*RANK)}|{'order.ascending','order.descending'}
    return {'schema_version':'openpine.array-order-spec.1','release':'pine-array-order-20261031.1','source_lock_sha256':digest(lock),
        'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(refs[v])} for v in refs},
        'evidence':{str(v):[{k:e[k] for k in ('entry_id','source_record_sha256','sources','parameters','result_type_spellings')} for e in refs[v]['entries'] if e['name'] in names] for v in refs},
        'review':{'external_execution_verified':False,'primary_sources':['https://www.tradingview.com/pine-script-docs/language/arrays/',
            'https://www.tradingview.com/pine-script-docs/v5/language/arrays/','https://www.tradingview.com/pine-script-docs/v4/essential/arrays/',
            'https://www.tradingview.com/pine-script-docs/release-notes/','https://in.tradingview.com/pine-script-reference/v6/'],
            'decisions':['V4 sort uses bool order; v5/v6 use closed sort_order tokens. New search/indices/nth APIs occur from v5.',
                'V6 typed UDT search overloads admit string targets/fields, although some reference prose says numeric; typed overloads are the bounded authority.',
                'Equal-key order is explicitly stable; historical missing/empty-string sorting follows the reviewed current ordering rule, without external execution proof.',
                'Search precondition is caller-owned ascending order, not checked with a linear scan. Empty search returns -1; missing targets fail. Undefined precondition inputs carry no equivalence claim.',
                'Leftmost clamps the predecessor to zero; rightmost may return size, as explicitly described by the current reference, despite the older guide saying existing index.',
                'Nth counts duplicate defined slots; unavailable/negative/missing ranks fail explicitly. Empty/all-missing aggregates return na. Unspecified edges are OpenPine interpretations.',
                'Only closed const sort_field selectors are serialized; pure const selector folding belongs to the same binder, no runtime field choice.',
                'Existing ArrayIR layouts, heap graph, legacy bundles and limits remain. Profiles and implementation fingerprint change.'],
            'test_refs':['tests/test_array_order.py','conformance/array-order/cases.json']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/array-order-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Array order profile differs.'
    else:path.write_bytes(raw)
    print('Additive array order profile verified.')
if __name__=='__main__':main()
