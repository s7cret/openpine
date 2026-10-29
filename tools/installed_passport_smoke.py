"""One installed CLI/HTTP explanation path; no workers or candle acquisition."""
import json
from tools.specification import ROOT
from packages.contracts.wire import canonical,loads
from packages.contracts.contexts import MarketContext
from dataclasses import asdict

def verify(http,cli,work):
    primary=asdict(MarketContext('primary','TEST:MTF','5','metadata.1'))
    primary_path=work/'passport-primary.json';primary_path.write_text(json.dumps(primary),encoding='utf-8')
    completed=[]
    for v in range(1,7):
        source=ROOT/f'examples/corpus/pending-v{v}.pine'
        compilation=http('/api/compile',{'source':source.read_text(),'source_name':f'owned-v{v}.pine'})
        value=http('/api/launches/requirements',{'input_ref':compilation['input_ref'],'inputs':{},'primary_context':primary,'explain':True})
        artifact=work/f'passport-v{v}.json';artifact.write_bytes(canonical(compilation['artifact']))
        report=cli('passport','--artifact',artifact,'--primary-context',primary_path)
        assert canonical(report)==canonical(value['passport'])==canonical(loads(value['passport_json']))
        assert report['evidence']['whole_program_execution_verified'] is False
        modes={m['mode']:m for m in report['modes']}
        assert modes['replay']['status']=='rejected_configuration' and modes['observed']['status']=='configuration_admitted'
        assert report['readiness']['jobs_submitted'] is False
        completed.append(v)
    inventory=__import__('apps.openpine.capabilities',fromlist=['report']).report()
    assert 'openpine.request-environment.1' in {p['profile_id'] for p in inventory['profile_inventory']}
    from tools.installed_bound_passport import verify as verify_bound_cli
    return {**verify_bound_cli(cli,work),'passport_installed_versions':completed,'passport_cli_http_raw_parity':True,
        'passport_compiler_and_runtime_authority':True,'passport_metadata_not_execution_permission':True}
