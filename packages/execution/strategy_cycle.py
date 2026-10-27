"""One bounded phase scheduler shared by historical and observed strategies."""
import copy
from dataclasses import asdict
from packages.contracts import BarFrame,Diagnostic,OpenPineError
from packages.contracts.strategy_cycle import PROFILE,FLAGS,MAX_PASSES,settings
from packages.contracts.realtime import MarketEvent
from packages.execution.intrabar import IntrabarCalculation
from packages.runtime.intrabar import IntrabarMemory
from packages.runtime.passes import commit

def qualify(program):
    from packages.contracts.wire import read_json,digest
    from packages.contracts.strategy_cycle import policy
    from packages.compiler.semantic.specification import value_sha
    from .identity import ROOT
    pinned=read_json(ROOT/'spec/strategy-cycle-profile.json')
    lock=read_json(ROOT/'spec/lock.json')
    if (pinned['source_lock_sha256']!=digest(lock) or value_sha(lock)!=program.identity.spec_lock_sha256
            or pinned['intrabar_profile_sha256']!=digest(read_json(ROOT/'spec/intrabar-profile.json'))
            or pinned['versions'][str(program.identity.language_version)]['policy']!=policy(program.identity.language_version)):
        raise ValueError('Strategy phases differ from pinned policy.')

class StrategyCycle:
    def __init__(self,evaluation,simulation,selected,*,historical=False):
        qualify(evaluation.program)
        self.evaluation,self.simulation=evaluation,simulation;self.settings=settings(evaluation.program,selected)
        self.historical=historical
        self.memory=IntrabarMemory();self.count=0;self.observations=0;self.last=None;self.last_pass=None
        self.counts={name:0 for name in ('regular','after_fill','after_close_fill')};self.recent=[];self.candidate=None;self.outputs=None
        simulation.broker.sampled_commands=True;simulation.cycle=self

    def calculate(self,bar,phase,event,ordinal,environment_phase,requests):
        if ordinal>MAX_PASSES:raise OpenPineError(Diagnostic('RUNTIME_ERROR','CALCULATION_BUDGET','Strategy calculation pass budget exceeded.'))
        from .realtime import fork
        temporary,_=fork(self.evaluation);self.memory.apply(temporary)
        if self.simulation.broker.performance:self.simulation.broker.performance.reads=0
        temporary.deferred_commit=True;temporary.strategy_values=self.simulation.broker.views()
        temporary.environment_phase=environment_phase;temporary.request_values=requests
        temporary.step(bar);self.memory.capture(temporary,self.evaluation)
        intents=temporary.order_intents
        if self.simulation.orders_start_ms is not None and bar.time<self.simulation.orders_start_ms:intents=[]
        events=self.simulation.broker.commit(intents,bar.bar_index,bar.time,confirmed=False,preserve_pending=True)
        self.simulation.publish_events(events);temporary.order_intents=[]
        self.count+=1;self.counts[phase]+=1;self.last=event
        self.last_pass={'event_sequence':event['sequence'],'phase':phase,'ordinal':ordinal}
        self.recent.append({**self.last_pass,'fills':self.simulation.broker.fill_count})
        self.outputs=[{'output_id':site,'title':title,'value_type':asdict(typ),'value':value} for site,title,typ,value in temporary.pending_outputs]
        self.candidate=temporary

    def run(self,bar,event,*,confirmed,filled,environment_phase,requests):
        self.observations+=1;self.recent=[];self.candidate=None;self.outputs=None
        if filled and self.settings[FLAGS[1]]:self.calculate(bar,'after_fill',event,1,environment_phase,requests)
        if confirmed or self.settings[FLAGS[0]] and not self.historical:self.calculate(bar,'regular',event,len(self.recent)+1,environment_phase,requests)
        if confirmed and self.settings[FLAGS[2]]:
            broker=self.simulation.broker;before=broker.fill_count
            broker.begin(bar.bar_index,bar.time,bar.close,bar.close,high=bar.close,low=bar.close,continuing=True)
            self.simulation.publish_events(broker.commit([],bar.bar_index,bar.time,confirmed=False,preserve_pending=True))
            if broker.fill_count>before and self.settings[FLAGS[1]]:
                self.calculate(bar,'after_close_fill',event,len(self.recent)+1,environment_phase,requests)
        if confirmed:
            if self.candidate is None:raise ValueError('Closed bar lacks a regular calculation.')
            commit(self.evaluation,self.candidate);self.memory.clear()
            self.simulation.publish_events(self.simulation.broker.commit([],bar.bar_index,bar.time,preserve_pending=True))
        self.candidate=None

    def summary(self):
        return {'profile_id':PROFILE,'mode':'strategy_phases','settings':self.settings,'observations':self.observations,
            'passes':self.count,**({'synthetic_tick_calculations':False} if self.historical else {}),'phase_counts':dict(self.counts),'last_pass':copy.deepcopy(self.last_pass),'recent_passes':copy.deepcopy(self.recent)}

    def checkpoint(self):
        return {**self.summary(),'schema_version':'openpine.strategy-cycle-state.1','last_event':copy.deepcopy(self.last),
            'variables':copy.deepcopy(self.memory.value)}

    def restore(self,value,*,count,sequence,active,observations,fill_count):
        names={'schema_version','profile_id','mode','settings','observations','passes','phase_counts','last_pass','recent_passes','last_event','variables'}
        if self.historical:names.add('synthetic_tick_calculations')
        if (type(value) is not dict or value.keys()!=names or self.historical and value.get('synthetic_tick_calculations') is not False or value['schema_version']!='openpine.strategy-cycle-state.1'
                or value['profile_id']!=PROFILE or value['mode']!='strategy_phases' or value['settings']!=self.settings
                or type(value['observations']) is not int or value['observations']!=observations
                or not count<=observations<=sequence+1 or type(value['passes']) is not int or not count<=value['passes']<=observations*MAX_PASSES):raise ValueError('Invalid strategy phase cursor.')
        counts=value['phase_counts']
        if (type(counts) is not dict or counts.keys()!=self.counts.keys() or any(type(v) is not int or v<0 for v in counts.values())
                or sum(counts.values())!=value['passes'] or counts['regular']!=(observations if self.settings[FLAGS[0]] and not self.historical else count)
                or counts['after_fill']>observations or counts['after_close_fill']>count
                or counts['after_fill']+counts['after_close_fill']>fill_count
                or not self.settings[FLAGS[1]] and (counts['after_fill'] or counts['after_close_fill'])
                or not self.settings[FLAGS[2]] and counts['after_close_fill']):raise ValueError('Invalid strategy pass counts.')
        last=value['last_pass'];event=value['last_event'];recent=value['recent_passes']
        if (last is None)!=(value['passes']==0) or (event is None)!=(last is None):raise ValueError('Missing calculation identity.')
        if event is not None:
            parsed=MarketEvent.from_payload(event)
            if ((parsed.context_id!=self.evaluation.context_id and self.evaluation.context_id is not None) or parsed.sequence>sequence
                    or active is None and (parsed.time!=self.evaluation.last_time or parsed.kind!='close')
                    or active is not None and (parsed.time>active['time'] or parsed.event_time>active['event_time'])):raise ValueError('Invalid calculation event.')
        def valid(row):
            return (type(row) is dict and row.keys()=={'event_sequence','phase','ordinal'} and type(row['event_sequence']) is int
                and 0<=row['event_sequence']<=sequence and row['phase'] in counts and type(row['ordinal']) is int and 1<=row['ordinal']<=MAX_PASSES)
        if last is not None and (not valid(last) or last['event_sequence']!=event['sequence']):raise ValueError('Invalid last calculation identity.')
        if type(recent) is not list or len(recent)>MAX_PASSES:raise ValueError('Invalid recent pass trace.')
        for i,row in enumerate(recent,1):
            if (type(row) is not dict or row.keys()!={'event_sequence','phase','ordinal','fills'} or not valid({k:v for k,v in row.items() if k!='fills'})
                    or row['ordinal']!=i or type(row['fills']) is not int or not 0<=row['fills']<=fill_count):raise ValueError('Invalid recent pass identity.')
        phases=[row['phase'] for row in recent]
        if (len(set(phases))!=len(phases) or phases!=sorted(phases,key=lambda x:('after_fill','regular','after_close_fill').index(x))
                or any(row['event_sequence']!=last['event_sequence'] for row in recent)
                or active is not None and 'after_close_fill' in phases):raise ValueError('Invalid calculation phase order.')
        if recent and {k:v for k,v in recent[-1].items() if k!='fills'}!=last:raise ValueError('Pass trace differs from last calculation.')
        if active is None and value['variables'] is not None:raise ValueError('Closed bar retains intrabar roots.')
        self.memory.restore(value['variables'],self.evaluation)
        self.count=value['passes'];self.observations=observations;self.counts=dict(counts);self.last=copy.deepcopy(event);self.last_pass=copy.deepcopy(last);self.recent=copy.deepcopy(recent)

class ObservedCycle(IntrabarCalculation):
    def __init__(self,engine,selected):
        super().__init__(engine);self.cycle=StrategyCycle(engine.evaluation,engine.simulation,selected)

    def primary(self,event,filled):
        from .realtime import frame
        e=self.engine;bar=frame(event,e.evaluation.count)
        self.cycle.run(bar,event.payload(),confirmed=event.kind=='close',filled=filled,environment_phase=e.phase(event),requests=e.request_values(event))
        if event.kind=='close':e.preview=None
        elif self.cycle.recent:
            # The last branch was submitted, but only its outputs become preview.
            e.preview={'time':event.time,'revision':event.revision,'observed_at':event.event_time,'confirmed':False,'outputs':copy.deepcopy(self.cycle.outputs)}

    def summary(self):return {**self.cycle.summary(),'bar_confirmed':self.engine.members[self.engine.primary_id]['active'] is None}

    def checkpoint(self):
        return {'schema_version':'openpine.observed-cycle-state.1','cycle':self.cycle.checkpoint(),'preview':copy.deepcopy(self.engine.preview),
            'requests':super().checkpoint()['requests']}

    def restore(self,value,sequence):
        e=self.engine
        if type(value) is not dict or value.keys()!={'schema_version','cycle','preview','requests'} or value['schema_version']!='openpine.observed-cycle-state.1':raise ValueError('Invalid observed calculation phases.')
        primary=e.members[e.primary_id]
        self.cycle.restore(value['cycle'],count=e.evaluation.count,sequence=sequence,active=primary['active'],observations=e.simulation.observations,fill_count=e.simulation.broker.fill_count)
        if self.cycle.last is not None and self.cycle.last['context_id']!=e.primary_id:raise ValueError('Calculation belongs to a secondary context.')
        # Reuse the original requested-context codec and strict preview validation.
        if primary['active'] is not None and value['preview'] is None and self.cycle.settings[FLAGS[0]]:raise ValueError('Missing cycle preview.')
        self._restore_requests(value['requests'],sequence)
        preview=value['preview'];last=self.cycle.last
        active_calculation=bool(primary['active'] is not None and last is not None and last['time']==primary['active']['time'])
        if (preview is not None)!=active_calculation or (self.cycle.memory.value is not None)!=active_calculation:raise ValueError('Cycle preview and intrabar roots differ from calculation boundary.')
        if preview is not None:
            from packages.runtime.checkpoints import layout
            if (type(preview) is not dict or preview.keys()!={'time','revision','observed_at','confirmed','outputs'}
                    or preview['confirmed'] is not False or preview['time']!=last['time'] or preview['revision']!=last['revision']
                    or preview['observed_at']!=last['event_time'] or type(preview['outputs']) is not list):raise ValueError('Invalid cycle preview cursor.')
            calls,_=layout(e.program);seen=set()
            for row in preview['outputs']:
                if type(row) is not dict or row.keys()!={'output_id','title','value_type','value'} or type(row['output_id']) is not str:raise ValueError('Invalid cycle output.')
                node=calls.get(row['output_id'])
                if node is None or node.operation!='visual.plot' or row['output_id'] in seen:raise ValueError('Unknown or repeated cycle output.')
                typ=node.arguments[0].expression.value_type
                if row['value_type']!=asdict(typ) or type(row['title']) is not str or len(row['title'])>65536 or not e.evaluation.matches(row['value'],typ):raise ValueError('Invalid cycle output type.')
                seen.add(row['output_id'])
        e.preview=copy.deepcopy(preview)

    def _restore_requests(self,rows,sequence):
        from .realtime import decoded_request
        if type(rows) is not dict or rows.keys()!=self.requests.keys():raise ValueError('Invalid cycle requested contexts.')
        for site,row in rows.items():
            record=self.engine.requests[site];active=self.engine.members[record['context_id']]['active'];target=self.requests[site]
            if type(row) is not dict or row.keys()!={'variables','value','sequence'} or (row['variables'] is None)!=(active is None):raise ValueError('Invalid requested phase boundary.')
            target['memory'].restore(row['variables'],record['evaluation'])
            if active is not None:
                decoded=decoded_request(row['value'],record['node'].value_type)
                if type(row['sequence']) is not int or row['sequence']!=active['sequence'] or not record['evaluation'].matches(decoded,record['node'].value_type):raise ValueError('Invalid requested cycle value.')
                target['value']=decoded;target['sequence']=row['sequence']
            elif row['value'] is not None or row['sequence'] is not None:raise ValueError('Closed requested context retains preview.')
