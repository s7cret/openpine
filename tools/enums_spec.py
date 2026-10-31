"""Pin versioned enum rules from local evidence and reviewed primary manuals."""
import argparse
from tools.specification import ROOT
from packages.contracts.enums import policy
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json')
    return {'schema_version':'openpine.enums-spec.1','release':'pine-enums-20261031.1','source_lock_sha256':digest(lock),
        'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(read_json(ROOT/lock['snapshot']/f'reference/v{v}.json'))} for v in range(1,7)},
        'review':{'external_execution_verified':False,'primary_sources':['https://www.tradingview.com/pine-script-docs/v5/language/enums/','https://www.tradingview.com/pine-script-docs/language/enums/','https://www.tradingview.com/pine-script-docs/v5/language/type-system/','https://www.tradingview.com/pine-script-docs/language/type-system/','https://www.tradingview.com/pine-script-reference/v5/#fun_input.enum'],
            'decisions':['Nominal member tokens cannot be implicitly converted to strings or numbers. str.tostring returns the declared title.',
                'Pin the v5 manual simple member qualifier separately from the v6 const member qualifier; external behavior remains unverified.',
                'The v5 reference calls input.enum defval const enum, while the v5 manual describes simple enum members. This profile accepts only direct member syntax as defval and records the qualifier discrepancy, without claiming external equivalence.',
                'Enum values are scalars in fields and array slots, with no new heap references or GC edges.',
                'Titles and input presentation are limited to literals, as in the existing input profile.']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/enums-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Enum profile differs.'
    else:path.write_bytes(raw)
    print('Enum profile verified.')

if __name__=='__main__':main()
