import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.openpine.bootstrap import local_manager, local_services
from packages.contracts.wire import read_json
from packages.marketdata.providers import input_snapshot
from test_environment_pipeline import wait


class AccountLedgerTests(unittest.TestCase):
    def test_three_dated_fills_equity_fees_and_paged_reporting(self):
        folder = Path(__file__).resolve().parents[1]/'examples/account-ledger'
        state = self.enterContext(TemporaryDirectory())
        with local_manager(state) as manager:
            program = manager.freeze_input(dict(source=(folder/'partial.pine').read_text(), source_name='partial.pine'))
            snapshot = manager.market.save(input_snapshot(read_json(folder/'bars.json')))
            job = manager.submit(dict(schema_version='openpine.job-request.2', input_ref=program,
                                      snapshot_id=snapshot, timeout_seconds=60))['job_id']
            wait(manager,job)
            events = manager.ledger(job)['events']
            exit_fill = next(e for e in events if e['kind']=='fill' and e['bar_index']==2)
            trade = next(e for e in events if e['kind']=='trade' and e['bar_index']==2)
            episode = manager.queries.episode(job,exit_fill['sequence'])
            self.assertEqual(episode,manager.queries.episode(job,trade['sequence']))
            self.assertEqual(episode['source_line'].strip(),'strategy.close("L",qty=2)')
            self.assertEqual(episode['span']['line'],6)
            self.assertEqual(episode['bar_close_state']['position_size'],'2')
            self.assertEqual(episode['candle']['close'],108)
        with local_services(state) as reopened:
            self.assertEqual(reopened.queries.episode(job,exit_fill['sequence']),episode)
        args = [sys.executable,'-m','apps.openpine','account-ledger','--state-dir',state,
                '--id',job,'--currency','EUR','--fx-rates',str(folder/'rates.json')]
        report = json.loads(subprocess.check_output(args))
        self.assertEqual(report['snapshot_id'], snapshot)
        fields = ('position','gross_flow','fee','cash','exposure','equity')
        self.assertEqual([tuple(row[k] for k in fields) for row in report['rows']], [
            ('0','0','0','1000','0','1000'),
            ('4','-320','3.2','676.8','336','1012.8'),
            ('2','198','1.98','872.82','194.4','1067.22'),
            ('0','198','1.98','1068.84','0','1068.84'),
        ])
        page = json.loads(subprocess.check_output([*args,'--start','2','--limit','1']))
        self.assertEqual(page['rows'],report['rows'][2:3])
