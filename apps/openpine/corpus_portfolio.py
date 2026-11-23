"""Build a portable result portfolio and reproduce it against the installed catalogue."""
import argparse
import time
import uuid
from pathlib import Path

from apps.openpine.bootstrap import local_manager
from apps.openpine.corpus import Corpus, Runs
from packages.contracts.wire import canonical, digest, immutable_json, read_json


def portfolio(manager, corpus, ids):
    service = Runs(manager,corpus,recover=True)
    key = uuid.uuid4().hex
    try:
        service.create(dict(request_id=key,case_ids=ids))
        while True:
            state = service.report(key)['report']
            if state['status'] != 'running':
                break
            time.sleep(.12)
        if state['status'] != 'passed':
            raise ValueError(f'Corpus run {key} ended with {state["status"]}.')
        cases = []
        for row in state['cases']:
            member = dict(case_id=row['case_id'],version=row['version'],status=row['status'],
                          provenance=row['provenance'],compilation=row['compilation'])
            if row['jobs']:
                job = row['jobs'][0]['job_id']
                chart = manager.chart(job,0,2000)
                simulation = manager.summary(job)['result'].get('simulation')
                ledger = []
                if simulation:
                    total = simulation['journal']['records']
                    for start in range(0,total,1000):
                        ledger.extend(manager.ledger(job,start,1000)['events'])
                member.update(snapshot_id=row['snapshot_id'],chart=chart,journal=ledger)
                if len(chart['bars']) != chart['total']:
                    for start in range(len(chart['bars']),chart['total'],2000):
                        member.setdefault('additional_chart_pages',[]).append(manager.chart(job,start,2000))
            cases.append(member)
        return dict(schema_version='openpine.corpus-portfolio.1',manifest_sha256=corpus.sha256,
                    execution_identity=state['identity'],cases=cases)
    finally:
        service.close()


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('export','repeat'))
    parser.add_argument('--state-dir',required=True,type=Path)
    parser.add_argument('--portfolio',required=True,type=Path)
    parser.add_argument('--case',dest='cases',action='append')
    args = parser.parse_args(argv)
    try:
        corpus = Corpus()
        if args.action=='repeat':
            if args.cases or args.state_dir.exists():
                raise ValueError('Repeat takes its cases from the file and requires a new catalogue.')
            saved = read_json(args.portfolio,max_bytes=128*1024*1024)
            body = saved['portfolio']
            if saved['sha256'] != digest(body) or body['manifest_sha256'] != corpus.sha256:
                raise ValueError('Portfolio checksum or catalogue identity differs.')
            ids = [row['case_id'] for row in body['cases']]
        else:
            if not args.cases or args.portfolio.exists() or args.portfolio.resolve().is_relative_to(args.state_dir.resolve()):
                raise ValueError('Select cases and a new portfolio file outside the state directory.')
            ids = args.cases
        with local_manager(args.state_dir) as manager:
            result = portfolio(manager,corpus,ids)
        sha = digest(result)
        if args.action=='repeat':
            if sha != saved['sha256']:
                raise ValueError('Recomputed portfolio differs from the exported results.')
        else:
            payload = dict(sha256=sha,portfolio=result)
            if len(canonical(payload))>128*1024*1024:
                raise ValueError('Portfolio exceeds 128 MiB. Export fewer cases.')
            immutable_json(args.portfolio,payload)
        print(canonical(dict(status='verified',sha256=sha,cases=ids)).decode())
        return 0
    except (ValueError,KeyError,OSError) as error:
        print(canonical(dict(status='rejected',message=str(error))).decode())
        return 2
