"""Export one frozen experiment and rerun its variants in a new local catalogue."""
import argparse
import hashlib
import tempfile
import time
from pathlib import Path
import apsw

from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from packages.contracts.wire import canonical, digest, loads
from packages.storage.backup import backup, restore, durable_copy
from packages.storage.experiments import ExperimentRepository


def observation(manager, job):
    summary = manager.summary(job)['result']
    chart, journal = hashlib.sha256(), hashlib.sha256()
    for start in range(0, summary['observation_count'], 256):
        chart.update(canonical(manager.chart(job,start,256)))
    count = (summary.get('simulation') or {}).get('journal',{}).get('records',0)
    for start in range(0,count,256):
        journal.update(canonical(manager.ledger(job,start,256)['events']))
    return dict(bars=summary['observation_count'], chart_sha256=chart.hexdigest(),
                events=count, journal_sha256=journal.hexdigest())


def export(manager, key, destination):
    repo = ExperimentRepository(manager.db)
    manifest, identity = repo.manifest(key)
    variants = repo.variants(key,limit=len(manifest['variants']))
    expected, objects, datasets = [], set(), set()
    for row in variants:
        req, plan = row['request'], row['plan']
        if manifest['variants'][row['ordinal']] != dict(request_sha256=digest(req),plan_sha256=digest(plan.payload())):
            raise ValueError('Variant differs from the frozen composition.')
        if not row['job_id'] or manager.get(row['job_id'])['status'] != 'succeeded':
            raise ValueError('Export requires every variant to have succeeded.')
        expected.append(observation(manager,row['job_id']))
        objects.add(req['input_ref'])
        content = manager.objects.get(req['input_ref'])
        objects.update(content[k] for k in ('artifact_ref','source_ref') if k in content)
        objects.update(req[k] for k in ('fx_snapshot_id','corporate_snapshot_id') if k in req)
        datasets.add(req['snapshot_id'])
        datasets.update(r['snapshot_id'] for r in req.get('contexts',[]))
        if req.get('magnifier'):
            datasets.add(req['magnifier']['snapshot_id'])
    destination = Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(manager.directory):
        raise ValueError('Choose a new export directory outside the source catalogue.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as scratch, local_manager(scratch,autostart=False) as target:
        for dataset in datasets:
            data = manager.market.manifest(dataset)
            ref = target.objects.put(data)
            target.db.execute('INSERT INTO datasets VALUES(?,?,?)',(dataset,canonical(dict(object_id=ref)),time.time()))
            objects.update(part['object_id'] for part in data['parts'])
        for sha in objects:
            path = target.objects.path(sha)
            path.parent.mkdir(parents=True,exist_ok=True)
            durable_copy(manager.objects.verify(sha),path)
            target.objects.verify(sha)
        fresh = ExperimentRepository(target.db).create(manifest,[(r['request'],r['plan']) for r in variants])
        receipt = dict(schema_version='openpine.portable-experiment.1',source_experiment=key,
                       experiment_id=fresh,manifest_sha256=identity,expected=expected)
        target.db.execute('INSERT INTO settings(name,value) VALUES(?,?)',('portable:experiment',canonical(receipt)))
        backup(target.db,target.directory,destination)
    return receipt


def repeat(source, destination):
    restore(source,destination)
    with local_manager(destination,autostart=False) as inspection:
        values = inspection.db.execute('SELECT value FROM settings WHERE name=?',('portable:experiment',))
        if not values:
            raise ValueError('Backup has no portable experiment descriptor.')
        receipt = loads(values[0][0])
        key = receipt['experiment_id']
        if (receipt.get('schema_version') != 'openpine.portable-experiment.1'
                or inspection.db.execute('SELECT experiment_id FROM experiments') != [(key,)]
                or inspection.repository.list(limit=1)):
            raise ValueError('Expected one unstarted portable experiment.')
    with local_manager(destination) as manager:
        service = Experiments(manager,start=False)
        try:
            while True:
                service.tick()
                status = service.status(key,limit=1)
                if status['status'] != 'running':
                    break
                time.sleep(.1)
            if status['status'] != 'succeeded' or status['manifest_sha256'] != receipt['manifest_sha256']:
                raise ValueError('Transferred experiment failed or its composition changed: '+str(status['variants']))
            rows = service.repository.variants(key,limit=status['total'])
            actual = [observation(manager,r['job_id']) for r in rows]
            if actual != receipt['expected']:
                raise ValueError('Recomputed charts or trade journals differ from the exported experiment.')
            return dict(status='verified',experiment_id=key,manifest_sha256=status['manifest_sha256'],variants=actual)
        finally:
            service.close()


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action',required=True)
    send = sub.add_parser('export')
    send.add_argument('--id',required=True)
    send.add_argument('--state-dir',type=Path,required=True)
    send.add_argument('--bundle',type=Path,required=True)
    receive = sub.add_parser('repeat')
    receive.add_argument('--bundle',type=Path,required=True)
    receive.add_argument('--state-dir',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        if args.action=='export':
            if not (args.state_dir/'catalog.sqlite3').is_file():
                raise ValueError('Expected an existing source catalogue.')
            with local_manager(args.state_dir,autostart=False) as manager:
                result = export(manager,args.id,args.bundle)
        else:
            result = repeat(args.bundle,args.state_dir)
    except (ValueError,OSError,KeyError,apsw.Error) as error:
        print(canonical(dict(status='rejected',message=str(error))).decode())
        return 2
    print(canonical(result).decode())
    return 0
