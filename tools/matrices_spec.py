"""Reproduce the additive map profile from immutable Wiki reference records."""
import argparse
from tools.specification import ROOT
from packages.contracts.matrices import policy
from packages.contracts.wire import read_json,digest,canonical

def profile():
    lock=read_json(ROOT/'spec/lock.json');refs={v:read_json(ROOT/lock['snapshot']/f'reference/v{v}.json') for v in range(1,7)}
    return {'schema_version':'openpine.matrices-spec.1','release':'pine-matrices-20261102.1','source_lock_sha256':digest(lock),
        'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(refs[v])} for v in refs},
        'evidence':{str(v):[{k:e[k] for k in ('entry_id','source_record_sha256','sources','parameters','result_type_spellings')} for e in refs[v]['entries'] if e['name'].startswith('map.')] for v in refs},
        'review':{'external_execution_verified':False,'primary_sources':['https://www.tradingview.com/pine-script-docs/v5/language/matrices/','https://www.tradingview.com/pine-script-docs/language/matrices/'],
            'decisions':['Matrices and methods require v5/v6; v1-v4 receive explicit MATRIX_VERSION diagnostics.',
                'Primitive int/float/bool/string, nominal enums and flat UDT; no graphics/color, nested collections, matrix requests or linear algebra.',
                'Dense row-major buffers share existing heap IDs and global quotas; each dimension is bounded by100000, product by100000. Zero dimensions are retained.',
                'Only0x0 infers the opposite extent from an inserted array. Omitted vectors insert typed missing, bool false in v6.',
                'Remove defaults to the last valid index. Frozen Wiki v5 spells rows/columns, which would be out of bounds; see ADR0003.',
                'Row/col/copy/transpose have independent containers and shallow shared UDT aliases; remove returns an independent array.',
                'Iteration snapshots shape/row count, reads an independent row immediately before each iteration, and fails next iteration after shape changes. This is a bounded interpretation.',
                'Mutations stage the whole buffer. Remove preflights the final buffer and returned vector together; existing work/memory/storage/cancellation limits apply.',
                'Matrix programs use typed-ir.18/runtime-state.11/array-heap.4 with packed UDT IDs; imported bundles and old program formats remain unchanged.'],
            'test_refs':['tests/test_matrices.py','conformance/matrices/cases.json']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/matrices-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Matrix profile differs.'
    else:path.write_bytes(raw)
    print('Additive matrices profile verified.')
if __name__=='__main__':main()
