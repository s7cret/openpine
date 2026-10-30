"""Reproduce the additive methods profile from local versioned evidence."""
import argparse
from tools.specification import ROOT
from packages.contracts.methods import policy
from packages.contracts.wire import canonical,digest,read_json


def profile():
    lock=read_json(ROOT/'spec/lock.json');versions={};evidence={}
    for v in range(1,7):
        ref=read_json(ROOT/lock['snapshot']/f'reference/v{v}.json')
        versions[str(v)]={'policy':policy(v),'reference_sha256':digest(ref)}
        evidence[str(v)]=[{k:e[k] for k in ('entry_id','source_record_sha256','sources')} for e in ref['entries']
            if e['entry_id'].endswith(':method-overload') or e['name']=='method']
    return {'schema_version':'openpine.methods-spec.1','release':'pine-methods-20261030.1','source_lock_sha256':digest(lock),'versions':versions,'evidence':evidence,
        'review':{'external_execution_verified':False,'primary_sources':[
            'https://www.tradingview.com/pine-script-docs/v5/release-notes/#february-2023',
            'https://www.tradingview.com/pine-script-docs/v5/language/methods/',
            'https://www.tradingview.com/pine-script-docs/language/methods/'],
            'decisions':[
                'Methods are admitted for v5/v6; v1-v4 explicitly reject method declarations and instance calls.',
                'First parameter is explicitly typed and required; only existing scalar/primitive array receiver types are admitted.',
                'Receiver dispatch requires its exact underlying type; remaining arguments use the bounded versioned UDF header rules.',
                'Match minimum int-to-float cost; one matching user method overrides an equally good builtin. Tied user methods are ambiguous.',
                'The precise ranking and v5/v6 arity policy are pinned OpenPine interpretations; external execution equivalence remains unverified.',
                'Legacy qualified namespaces are preserved; parenthesizing a receiver disambiguates a name shared with a namespace.',
                'User methods use dot-only calls in this slice. Defaults and qualifier inference retain existing bounded UDF policies.',
                'Receiver is bound once before explicit arguments, closed defaults last. Existing UserCallIR/ArrayIR perform execution.',
                'Typed IR .13 distinguishes method metadata from ordinary same-name functions. Private request graphs may retain one selected method.',
                'No additional heap or runtime dispatch. State, rollback and checkpoint ownership remain tied to written call chains.'],
            'test_refs':['tests/test_methods.py','conformance/methods/cases.json']}}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--check',action='store_true');a=p.parse_args(argv)
    path=ROOT/'spec/methods-profile.json';raw=canonical(profile())+b'\n'
    if a.check:assert path.read_bytes()==raw,'Methods profile differs.'
    else:path.write_bytes(raw)
    print('Versioned method profile verified.')


if __name__=='__main__':main()
