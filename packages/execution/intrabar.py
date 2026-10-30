"""Calculation passes over confirmed runtime branches, one pass per observation."""
import copy
from dataclasses import asdict
from packages.contracts.intrabar import PROFILE
from packages.contracts.realtime import MarketEvent
from packages.runtime.intrabar import IntrabarMemory
from packages.runtime.checkpoints import layout

class IntrabarCalculation:
    def __init__(self,engine):
        self.engine=engine;self.memory=IntrabarMemory();self.count=0;self.last=None
        self.requests={site:{'memory':IntrabarMemory(),'value':None,'sequence':None} for site,r in engine.requests.items() if r['node'].operation!='context.lower_tf'}
        if engine.simulation:engine.simulation.broker.sampled_commands=True

    def request_update(self,site,event):
        from .realtime import fork,frame,captured
        record=self.engine.requests[site];state=self.requests[site]
        temporary,capture=fork(record['evaluation']);state['memory'].apply(temporary)
        temporary.environment_phase=self.engine.phase(event);temporary.step(frame(event,temporary.count))
        state['memory'].capture(temporary,record['evaluation']);state['value']=captured(capture);state['sequence']=event.sequence

    def request_close_before(self,site):self.requests[site]['memory'].apply(self.engine.requests[site]['evaluation'])
    def request_close_after(self,site):
        state=self.requests[site];state['memory'].clear();state['value']=None;state['sequence']=None

    def request_value(self,site,active):
        state=self.requests[site]
        if state['sequence']!=active.sequence:raise ValueError('Requested preview differs from its observation cursor.')
        return state['value']

    def primary_update(self,event):
        from .realtime import fork,frame
        engine=self.engine;temporary,capture=fork(engine.evaluation);self.memory.apply(temporary)
        temporary.request_values=engine.request_values(event);temporary.environment_phase=engine.phase(event)
        temporary.step(frame(event,temporary.count));self.memory.capture(temporary,engine.evaluation)
        if engine.simulation:engine.simulation.submit(temporary,event)
        self.count+=1;self.last=event.payload()
        engine.preview={'time':event.time,'revision':event.revision,'observed_at':event.event_time,
            'confirmed':False,'outputs':list(capture.values.values())}

    def primary_close_before(self):self.memory.apply(self.engine.evaluation)
    def primary_close_after(self,event):
        self.count+=1;self.last=event.payload();self.memory.clear();self.engine.preview=None

    def summary(self):
        return {'profile_id':PROFILE,'mode':'observed_updates','passes':self.count,
            'last_sequence':self.last['sequence'] if self.last else None,'bar_confirmed':self.engine.members[self.engine.primary_id]['active'] is None}

    def checkpoint(self,*,streaming=False):
        return {'schema_version':'openpine.intrabar-calculation-state.1','passes':self.count,'last_event':self.last,
            'variables':self.memory.export(streaming=streaming,object_graph=self.engine.program.schema_version=='openpine.typed-ir.15'),'preview':copy.deepcopy(self.engine.preview),
            'requests':{site:{'variables':row['memory'].export(streaming=streaming,object_graph=self.engine.program.schema_version=='openpine.typed-ir.15'),'value':copy.deepcopy(row['value']),
                'sequence':row['sequence']} for site,row in self.requests.items()}}

    def restore(self,value,sequence):
        from .realtime import decoded_request
        engine=self.engine;primary=engine.members[engine.primary_id]
        if (type(value) is not dict or value.keys()!={'schema_version','passes','last_event','variables','preview','requests'}
                or value['schema_version']!='openpine.intrabar-calculation-state.1' or type(value['passes']) is not int
                or not primary['count']+bool(primary['active'])<=value['passes']<=sequence+1
                or type(value['requests']) is not dict or value['requests'].keys()!=self.requests.keys()):
            raise ValueError('Invalid intrabar calculation boundary.')
        last=MarketEvent.from_payload(value['last_event']) if value['last_event'] is not None else None
        if (last is None)!=(value['passes']==0):raise ValueError('Missing intrabar calculation event.')
        if last and (last.context_id!=engine.primary_id or last.sequence>sequence or last.event_time>engine.event_time
                or primary['active'] is not None and last.payload()!=primary['active']
                or primary['active'] is None and (last.kind!='close' or last.time!=primary['last_closed'])):
            raise ValueError('Intrabar calculation differs from primary candle.')
        if (value['variables'] is None)!=(primary['active'] is None):raise ValueError('Invalid intrabar variable lifecycle.')
        self.memory.restore(value['variables'],engine.evaluation)
        preview=value['preview']
        if (preview is None)!=(primary['active'] is None):raise ValueError('Invalid intrabar preview lifecycle.')
        if preview is not None:
            if (type(preview) is not dict or preview.keys()!={'time','revision','observed_at','confirmed','outputs'}
                    or preview['confirmed'] is not False or preview['time']!=last.time or preview['revision']!=last.revision
                    or preview['observed_at']!=last.event_time or type(preview['outputs']) is not list):raise ValueError('Invalid intrabar output boundary.')
            calls,_=layout(engine.program);seen=set()
            for row in preview['outputs']:
                if type(row) is not dict or row.keys()!={'output_id','title','value_type','value'}:raise ValueError('Invalid intrabar output.')
                if type(row['output_id']) is not str:raise ValueError('Invalid intrabar output identity.')
                node=calls.get(row['output_id'])
                if node is None or node.operation!='visual.plot' or node.call_site_id in seen:raise ValueError('Unknown or repeated intrabar output.')
                typ=node.arguments[0].expression.value_type
                if row['value_type']!=asdict(typ) or type(row['title']) is not str or len(row['title'])>65536 or not engine.evaluation.matches(row['value'],typ):raise ValueError('Invalid intrabar output type.')
                seen.add(node.call_site_id)
        for site,row in value['requests'].items():
            if type(row) is not dict or row.keys()!={'variables','value','sequence'}:raise ValueError('Invalid requested intrabar state.')
            record=engine.requests[site];active=engine.members[record['context_id']]['active'];target=self.requests[site]
            if (row['variables'] is None)!=(active is None):raise ValueError('Invalid requested variable lifecycle.')
            target['memory'].restore(row['variables'],record['evaluation'])
            if active is not None:
                if type(row['sequence']) is not int or row['sequence']!=active['sequence']:raise ValueError('Invalid requested preview sequence.')
                decoded=decoded_request(row['value'],record['node'].value_type)
                if not record['evaluation'].matches(decoded,record['node'].value_type):raise ValueError('Invalid requested preview type.')
                target['value']=decoded;target['sequence']=row['sequence']
            elif row['sequence'] is not None or row['value'] is not None:raise ValueError('Closed requested context retains preview.')
        if engine.simulation and (engine.simulation.observations!=value['passes'] or engine.simulation.last_observation!=value['last_event']):raise ValueError('Broker and calculation cursors differ.')
        self.count=value['passes'];self.last=last.payload() if last else None;engine.preview=copy.deepcopy(preview)
