"""Reproduce the additive shared-window profile from immutable Wiki artifacts."""
import argparse
from tools.specification import ROOT
from packages.contracts.array_slice import policy
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');refs={v:read_json(ROOT/lock['snapshot']/f'reference/v{v}.json') for v in range(1,7)}
    return {'schema_version':'openpine.array-slice-spec.1','release':'pine-array-slice-20261101.1','source_lock_sha256':digest(lock),
        'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(refs[v])} for v in refs},
        'evidence':{str(v):[{k:e[k] for k in ('entry_id','source_record_sha256','sources','parameters','result_type_spellings')} for e in refs[v]['entries'] if e['name']=='array.slice'] for v in refs},
        'review':{'external_execution_verified':False,'primary_sources':['https://www.tradingview.com/pine-script-docs/language/arrays/',
            'https://www.tradingview.com/pine-script-docs/v5/language/arrays/','https://www.tradingview.com/pine-script-docs/v4/essential/arrays/'],
            'decisions':['All three arguments required: pinned references document no defaults. Slice exists from v4; methods from v5.',
                'Defined nonnegative half-open bounds with from<to at creation, including v6. Generic v4 scalar support inherits the existing historical reconstruction.',
                'Current v6 slicing prose illustrates shrinking/regrowing windows, while its error section and older guides describe out-of-bounds errors. This profile preserves fixed bounds and raises on access; it does not claim live TradingView equivalence.',
                'A slice of a slice normalizes to the root. Resizing changes only the receiver window extent; all other windows retain absolute bounds. This unspecified edge is an explicit OpenPine interpretation.',
                'Clear/removing every slot leaves an empty valid receiver; creation of an empty slice is rejected. Parent growth can revive a previously invalidated window.',
                'View IDs retain the root array; only roots own physical cells and UDT edges. No cycles, nested element collections, recursive UDT or new request expression types.',
                'ArrayIR layout remains; programs containing slice use runtime-state.9 and array-heap.2. Programs without slice retain legacy codecs and limits.'],
            'test_refs':['tests/test_array_slice.py','conformance/array-slice/cases.json']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/array-slice-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Array slice profile differs.'
    else:path.write_bytes(raw)
    print('Additive array slice profile verified.')
if __name__=='__main__':main()
