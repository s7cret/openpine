"""Installed-wheel search acceptance, using the same public CLI and loopback API."""
import json
import time
from packages.compiler.semantic.specification import ROOT


def verify(http,cli,work):
    example=ROOT/'examples/search';request=json.loads((example/'request.json').read_text(encoding='utf-8'))
    # Two candidates keep this bounded installed acceptance below CLI's deadline.
    request.update(budget=2,space={'n':[1,3]})
    snapshot=http('/api/snapshots',{'provider':'upload','raw':(ROOT/'examples/inputs/bars.json').read_text(encoding='utf-8')})['sha256']
    for version in (1,6):
        source=example/f'v{version}.pine';compiled=http('/api/compile',{'source':source.read_text(encoding='utf-8'),'source_name':source.name})
        created=http('/api/searches',{**request,'input_ref':compiled['input_ref'],'snapshot_id':snapshot})
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            state=http('/api/searches/'+created['search_id'])
            if state['status'] not in ('selecting','checking_holdout'):break
            time.sleep(.1)
        if state['status']!='succeeded' or state['decision']['winner_ordinal']!=1 or state['holdout']['assessment']['score']!='43.5':
            raise AssertionError('Installed search/holdout differed: '+str(state))
    request_file=work/'search-request.json';request_file.write_text(json.dumps(request),encoding='utf-8')
    state=cli('search','run','--source',example/'v6.pine','--bars',ROOT/'examples/inputs/bars.json','--request',request_file,'--state-dir',work/'search-cli')
    if state['status']!='succeeded' or state['holdout']['assessment']['score']!='43.5':raise AssertionError('Installed search CLI failed: '+str(state))
    return {'search_http_versions':[1,6],'search_cli_version':6,'search_holdout_isolated':True,'search_fixed_expected_scores':True}
