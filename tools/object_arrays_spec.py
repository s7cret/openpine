"""Pin flat nominal collection semantics without executing Pine."""
import argparse
from tools.specification import ROOT
from packages.contracts.object_arrays import policy
from packages.contracts.wire import canonical,digest,read_json

def profile():
    lock=read_json(ROOT/'spec/lock.json')
    return {'schema_version':'openpine.object-arrays-spec.1','release':'pine-object-arrays-20261030.1',
        'source_lock_sha256':digest(lock),'versions':{str(v):{'policy':policy(v),'reference_sha256':digest(read_json(ROOT/lock['snapshot']/f'reference/v{v}.json'))} for v in range(1,7)},
        'review':{'external_execution_verified':False,'primary_sources':['https://www.tradingview.com/pine-script-docs/v5/language/arrays/','https://www.tradingview.com/pine-script-docs/v5/language/objects/','https://www.tradingview.com/pine-script-docs/language/arrays/','https://www.tradingview.com/pine-script-docs/language/objects/'],
            'decisions':['Keep nominal element invariance and typed missing references. Array copies share object IDs.',
                'Only arrays of the existing scalar-field records; no reference fields or cycles.',
                'varip retains array structure. Object fields still follow their own varip flags; a varip array does not promote ordinary fields.',
                'New intrabar objects retain the constructor baseline for ordinary fields, as in the existing object profile.']}}

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/object-arrays-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Object-array profile differs.'
    else:path.write_bytes(raw)
    print('Nominal object-array profile verified.')

if __name__=='__main__':main()
