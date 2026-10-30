"""Reproduce the bounded version-specific UDF overload profile offline."""
import argparse
from tools.specification import ROOT
from packages.contracts.overloads import policy
from packages.contracts.wire import canonical,digest,read_json


def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(ref)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','source_record_sha256','sources')}
            for e in ref['entries'] if e['entry_id']==f'wiki:v{v}:overload']
    return {'schema_version':'openpine.function-overloads-spec.1','release':'pine-function-overloads-20261030.1',
        'source_lock_sha256':digest(lock),'versions':versions,'wiki_evidence':evidence,
        'primary_supplements':[
            {'versions':[5],'url':'https://www.tradingview.com/pine-script-docs/v5/release-notes/#november-2021',
             'finding':'November 2021 introduces function overloads: different total parameter counts, or explicitly typed distinct parameter combinations.'},
            {'versions':[6],'url':'https://www.tradingview.com/pine-script-docs/language/user-defined-functions/#function-overloading',
             'finding':'Required parameter counts and qualified types distinguish definitions; optional parameters, parameter names and return types do not.'}],
        'review':{'external_execution_verified':False,'decisions':[
            'v1-v4 preserve single-definition functions and reject distinct overload declarations.',
            'v5 compares all parameters at declaration; v6 compares required parameters. Calls with equally good headers are ambiguous in both.',
            'Exact argument types precede int-to-float widening. Qualifiers constrain admission without a most-specific-qualifier tie break.',
            'Existing simple/series parameter inference and literal-only default subset remain unchanged.',
            'Resolve concrete definition IDs in Binder; bind explicit expressions once in source order and insert closed defaults afterwards.',
            'Typed IR .12 adds required-parameter masks only for overloaded definitions; runtime validates concrete selected calls without dispatching.',
            'State belongs to each written call chain. Same-name calls within an overload can use earlier definitions; recursion remains forbidden.',
            'Unused bodies receive structural and definite dependency checks; type-dependent overload edges are checked on specialization, including dead branches.',
            'Methods, user-defined reference types and general default expressions remain outside this profile.'],
            'test_refs':['tests/test_function_overloads.py','conformance/function-overloads/cases.json']}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/function-overloads-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Function overload profile differs.'
    else:path.write_bytes(raw)
    print('Versioned function overload profile verified.')


if __name__=='__main__':main()
