"""Reproduce the artifact-defined first historical trading profile for v1-v6."""

import argparse
from packages.contracts.trading import strategy_policy
from tools.specification import ROOT,SpecError,encode,file_map,read_json,sha

RELEASE='pine-strategies-20261021.1'
NAMES={'strategy','strategy.entry','strategy.close','strategy.long','strategy.short','strategy.fixed',
    'strategy.commission.percent','strategy.position_size','strategy.position_avg_price','strategy.equity'}


def prepare():
    lock=read_json(ROOT/'spec/lock.json');versions={}
    for version in range(1,7):
        reference=read_json(ROOT/lock['snapshot']/f'reference/v{version}.json')
        entries=[e for e in reference['entries'] if e['name'] in NAMES]
        versions[str(version)]={'profile_id':f'openpine.strategy.v{version}.1','policy':strategy_policy(version),
            'evidence':[{'entry_id':e['entry_id'],'source_record_sha256':e['source_record_sha256']} for e in entries]}
    data={'schema_version':'openpine.strategy-spec.1','base_manifest_sha256':lock['manifest_sha256'],
        'version_review_manifest_sha256':lock['version_review']['manifest_sha256'],'versions':versions,
        'review':{'date':'2026-10-21','external_pine_execution':False,
            'basis':'Bounded historical market broker by pinned artifacts; reconstructed legacy behavior and model choices are explicit.',
            'sources':['https://www.tradingview.com/pine-script-docs/v3/essential/strategies/',
                'https://www.tradingview.com/pine-script-docs/concepts/strategies/'],
            'decisions':[
                'v1 has names without verified signatures/direction constants. Its title/entry(id,long,qty)/close(id) and defaults are explicit reconstructions, not historical equivalence claims. v2 remains example-based.',
                'v1-v4 use bool long; v2-v4 also expose strategy.long/short. v5-v6 require named direction constants. when is admitted only through v5; unsupported positional optional slots are rejected.',
                'Only market entry and full close, one open entry, pyramiding=0, opposite entry closes and reverses. Repeated same-side entries are ignored at execution. Same-bar commands sharing an entry ID replace earlier pending commands, retaining first insertion order.',
                'Evaluate once at confirmed primary close, execute previous pending orders in source order at the next available primary open. Missing bars are not synthesized; final pending orders/positions remain open. No limit/stop/intrabar inference.',
                'v5 capital default 1000000 and v6 100000 follow the local catalogs; legacy 100000/fixed quantity 1 are model reconstructions. v6 native sizing default is unsupported, so strategy.fixed must be explicit.',
                'Linear quote accounting: signed principal cash movements, unrealized mark-to-market, no FX, margin/liquidation or funding. Capital is an accounting initial condition, not a collateral/leverage check. Commission is percent per fill; reversal has close/open fills.',
                'Decimal arithmetic at precision 80; fees and published money use pinned HALF_EVEN scale, raw cash/gross retain exact precision. Quantity must match the pinned step. Positive OHLC are required. Nonzero slippage requires an explicitly supplied instrument tick size, never guessed from candles.',
                'Broker state, pending IDs/counters, runtime, context cursors and append-only journal prefix form one committed checkpoint. Orders are never published outside the attempt result/checkpoint roots.'],
            'test_refs':['tests/test_strategies.py','conformance/strategies/cases.json']}}
    files={'strategies.json':encode(data)}
    files['manifest.json']=encode({'schema_version':'openpine.strategy-manifest.1','release':RELEASE,
        'files':{n:sha(raw) for n,raw in files.items()}})
    return files


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    target=ROOT/'spec/bundles'/RELEASE;files=prepare()
    if target.exists():
        if file_map(target)!={n:sha(raw) for n,raw in files.items()}:raise SpecError('SPEC_IMMUTABLE','Strategy release differs; choose a new release.')
    elif args.check:raise SpecError('SPEC_IMPORT','Strategy release is missing.')
    else:
        target.mkdir(parents=True)
        for name,raw in files.items():(target/name).write_bytes(raw)
    print('Historical strategy release verified.')


if __name__=='__main__':main()
