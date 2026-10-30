"""Reproduce the additive flat-UDT profile from pinned versioned evidence."""
import argparse
from tools.specification import ROOT
from packages.contracts.objects import policy
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for version in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')
        versions[str(version)]={'policy':policy(version),'reference_sha256':digest(ref)}
        evidence[str(version)]=[{k:e[k] for k in ('entry_id','source_record_sha256','sources')} for e in ref['entries']
            if e['name']=='type' or e['entry_id'].endswith((':udt-declaration',':udt-field-varip',':udt-new'))]
    return {'schema_version':'openpine.objects-spec.1','release':'pine-flat-objects-20261030.1',
        'source_lock_sha256':digest(lock),'versions':versions,'evidence':evidence,
        'review':{'external_execution_verified':False,'primary_sources':[
            'https://www.tradingview.com/pine-script-docs/v5/language/objects/',
            'https://www.tradingview.com/pine-script-docs/language/objects/',
            'https://www.tradingview.com/pine-script-docs/language/type-system/#user-defined-types',
            'https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#history-of-udt-fields'],
        'decisions':['Admit global flat user-defined types for v5/v6 only, with scalar fields and stable nominal identities.',
            'Field defaults use literals or admitted compatible builtin variables, evaluated at construction.',
            'Use the existing reference heap, work/memory budgets, histories, COW previews and checkpoints.',
            'Only declared varip fields escape intrabar rollback; varip on a reference does not promote fields.',
            'Direct field history is invalid in v6; erroneous historical v5 behavior is deliberately profile-uncovered. Use a scalar alias or a parenthesized historic object.',
            'Object-dependent request contexts/targets, reference fields, object arrays, object tuples/equality and void returns are outside this first profile.']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');args=p.parse_args(argv)
    path=ROOT/'spec/objects-profile.json';raw=canonical(profile())+b'\n'
    if args.check:assert path.read_bytes()==raw,'Object profile differs.'
    else:path.write_bytes(raw)
    print('Flat user-defined object profile verified.')

if __name__=='__main__':main()
