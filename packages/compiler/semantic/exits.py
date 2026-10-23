"""Install the independently pinned bounded exit/cancellation signatures."""
from packages.contracts import CallContract,ValueType
from packages.contracts.protection import exit_policy
from .specification import SpecError,value_sha


def install(catalog):
    if not catalog.snapshot.exits:return
    version=catalog.language_version;row=catalog.snapshot.exits['versions'][str(version)]
    if row['policy']!=exit_policy(version):raise SpecError('IMPLEMENTATION_MISSING','Exit policy differs.')
    when=[('when','bool','series',True)] if version<6 else []
    apis=[('strategy.exit','simulation.exit',[('id','string','const',None),('from_entry','string','const',None),
            ('stop','float','series',{'kind':'na'}),('limit','float','series',{'kind':'na'})]+when),
          ('strategy.cancel','simulation.cancel',[('id','string','const',None)]+when),
          ('strategy.cancel_all','simulation.cancel_all',when)]
    for name,operation,parameters in apis:
        oid=f'overload.openpine.exits.v{version}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else d if type(d) is dict else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':['emits_order']}
        contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,
            tuple(p['parameter_id'] for p in params),tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),
            None,('emits_order',),ValueType('void'),'fixed')
        catalog.contracts[oid]=contract;catalog.signatures[name]=[(signature,contract)]
