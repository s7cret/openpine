"""One finite replay attempt. All calculation stays outside the coordinator."""

import argparse
import os
import threading
import time
from pathlib import Path

from apps.worker.__main__ import watch_parent
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.trading import BrokerSettings
from packages.contracts.realtime import policy
from packages.contracts.realtime import PROFILE
from packages.contracts.live import event_policy
from packages.contracts.wire import atomic_json
from packages.execution.artifacts import import_program
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.storage.database import Database
from packages.storage.objects import ObjectStore
from packages.storage.replay import ReplayRepository
from packages.storage.results import ResultSink
from packages.storage.lease import Lease


def execute(root, identifier, attempt, *, max_events=10000000, checkpoint_every=10000,session=None,cancelled=lambda:False):
    db = Database(Path(root)/'catalog.sqlite3')
    objects = ObjectStore(Path(root)/'objects')
    repository = ReplayRepository(db,objects)
    sink = None
    try:
        config = repository.config(identifier)
        objects.quota_bytes = config['object_quota_bytes']
        session = session or Session(profile_id=HISTORICAL_PROFILE_ID)
        session.memory_mb=config['memory_mb']
        program = import_program(objects.get(config['artifact_ref']))
        profile=config.get('event_profile',PROFILE)
        from packages.contracts.memory import job_memory
        sink = ResultSink(objects,attempt,threads=config['threads'],memory_mb=job_memory(config['memory_mb'],1)['per_io_mb'])
        engine = ReplayEngine(session,program,tuple(MarketContext.from_payload(c) for c in config['contexts']),config['primary_id'],sink,
            broker_settings=BrokerSettings.from_payload(config['broker_settings']) if config['broker_settings'] else None,profile=profile,settings=config.get('settings'),environments=config.get('environments'),broker_profile=config.get('broker_profile'),calculation_profile=config.get('calculation_profile'),cycle_settings=config.get('cycle_settings'),lower_tf=config.get('lower_tf'))
        if config['policy']!=engine.qualification():raise ValueError('Replay execution policy differs from this installation.')
        if engine.identity != config['identity']: raise ValueError('Replay identity differs from its saved program and policy.')
        saved = repository.checkpoint(identifier)
        if saved:
            count = saved['output']['completed']
            if count or sink.observed_simulation:sink.restore(saved['output'],count)
            engine.restore(saved['engine'])
            if engine.simulation and (count or engine.broker_profile):
                expected_records=engine.simulation.broker.event_count if engine.broker_profile else saved['engine']['broker_boundary']['broker']['event_count']
                if sink.trade_sink.records!=expected_records:raise ValueError('Broker journal differs from the checkpoint prefix.')
                if engine.broker_profile and (saved['output']['observations']!=engine.simulation.observations
                        or saved['output']['simulation_completed']!=engine.simulation.ledger_bars):raise ValueError('Observed output and broker cursors differ.')
        previous = engine.sequence
        stop = min(repository.row(identifier)[3], previous+1+max_events)

        def publish():
            nonlocal previous
            summary = engine.status()
            summary = sink.finish(summary)
            checkpoint = {'schema_version':'openpine.replay-checkpoint.1','config_ref':repository.row(identifier)[0],
                'sequence':engine.sequence,'engine':engine.checkpoint(),'output':sink.checkpoint(engine.evaluation.count,
                    **({'simulation_completed':engine.simulation.ledger_bars,'observations':engine.simulation.observations} if engine.broker_profile and engine.simulation else {}))}
            repository.publish(identifier,checkpoint,summary,previous=previous)
            previous=engine.sequence

        repository.archive(identifier,Path(attempt)/'tape')
        for event in repository.events(identifier,engine.sequence+1,stop):
            if cancelled():raise InterruptedError('Replay batch cancelled; the published prefix remains resumable.')
            engine.apply(event)
            if (engine.sequence+1)%checkpoint_every == 0: publish()
        if engine.sequence+1 != stop: raise ValueError('Replay input ended before its frozen event cursor.')
        if engine.sequence > previous: publish()
        from packages.execution.memory import telemetry
        atomic_json(Path(attempt)/'resources.json',telemetry(engine.evaluation,config['memory_mb'],1))
        return repository.status(identifier)
    finally:
        if 'engine' in locals() and engine.simulation:engine.simulation.close()
        if sink: sink.close()
        db.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store',type=Path,required=True)
    parser.add_argument('--id',required=True)
    parser.add_argument('--attempt',type=Path,required=True)
    parser.add_argument('--max-events',type=int,required=True)
    parser.add_argument('--checkpoint-every',type=int,required=True)
    parser.add_argument('--parent-pid',type=int,required=True)
    args=parser.parse_args(argv)
    threading.Thread(target=watch_parent,args=(args.parent_pid,),daemon=True).start()
    stopped=threading.Event()
    def heartbeat():
        while not stopped.is_set():
            atomic_json(args.attempt/'heartbeat.json',{'tick':time.monotonic(),'pid':os.getpid()})
            stopped.wait(.5)
    pulse=threading.Thread(target=heartbeat,daemon=True);pulse.start()
    started=time.monotonic();cpu=time.process_time()
    try:
        with Lease(args.attempt/'.active.lock'):
            atomic_json(args.attempt/'owner.json',{'schema_version':'openpine.replay-attempt.1','replay_id':args.id})
            execute(args.store,args.id,args.attempt,max_events=args.max_events,checkpoint_every=args.checkpoint_every)
    except Exception as error:
        atomic_json(args.attempt/'error.json',{'message':str(error)})
        return 2
    finally:
        stopped.set();pulse.join(timeout=2)
        try:atomic_json(args.attempt/'metrics.json',{'child_cpu_seconds':time.process_time()-cpu,'child_wall_seconds':time.monotonic()-started})
        except OSError:pass
    return 0


if __name__=='__main__': raise SystemExit(main())
