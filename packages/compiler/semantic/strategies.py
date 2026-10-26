"""Install only the separately reviewed historical market strategy signatures."""

from packages.contracts import CallContract,ReferenceContract,ValueType
from packages.contracts.trading import strategy_policy
from .specification import SpecError,value_sha
from packages.compiler.frontend import parser
from dataclasses import replace


def install(catalog):
    catalog.constants={}
    if not catalog.snapshot.strategies or 'close' not in catalog.references:return
    version=catalog.language_version;row=catalog.snapshot.strategies['versions'][str(version)]
    policy=strategy_policy(version)
    if row['policy']!=policy:raise SpecError('IMPLEMENTATION_MISSING','Strategy policy is not implemented.')
    catalog.constants={'strategy.fixed':'fixed','strategy.commission.percent':'percent'}
    if version>=2:catalog.constants.update({'strategy.long':True if version<5 else 'long','strategy.short':False if version<5 else 'short'})
    for name in ('position_size','position_avg_price','equity'):
        identity=f'openpine.strategy.v{version}.{name}'
        catalog.references['strategy.'+name]=ReferenceContract('symbol.'+identity,'assertion.'+identity,value_sha(row),
            'strategy.'+name,ValueType('float','series'))
    # Header optional values are named-only; their original Pine positional
    # offsets differ between versions and unsupported parameters are not aliases.
    apis=[('strategy','simulation.strategy',[
        ('title','string','const',None),('overlay','bool','const',False),('pyramiding','int','const',0),
        ('default_qty_type','string','const','fixed' if version<6 else 'percent_of_equity'),
        ('default_qty_value','float','const',1.0),('initial_capital','float','const',float(policy['initial_capital'])),
        ('slippage','int','const',0),('commission_type','string','const','percent'),('commission_value','float','const',0.0)],('declaration',)),
        ('strategy.entry','simulation.entry',[('id','string','const',None),
            (policy['direction_parameter'],'bool' if version<5 else 'string','const',None),('qty','float','series',{'kind':'na'})]
            +([('when','bool','series',True)] if version<6 else []),('emits_order',)),
        ('strategy.close','simulation.close',[('id','string','const',None)]
            +([('when','bool','series',True)] if version<6 else []),('emits_order',))]
    for name,operation,parameters,effects in apis:
        oid=f'overload.openpine.strategy.v{version}.{name}'
        params=[{'name':n,'parameter_id':f'parameter.{oid}.{n}','requiredness':{'state':'required' if d is None else 'optional'},
            'default':{'kind':'none'} if d is None else d if type(d) is dict else {'kind':'literal','value':d}} for n,_,_,d in parameters]
        signature={'parameters':params,'binding':{'named':True},'effects':list(effects)}
        contract=CallContract('symbol.'+oid,oid,'assertion.'+oid,value_sha(row),operation,
            tuple(p['parameter_id'] for p in params),tuple((t,) for _,t,_,_ in parameters),tuple(q for _,_,q,_ in parameters),
            None,effects,ValueType('void'),'fixed')
        catalog.contracts[oid]=contract;catalog.signatures[name]=[(signature,contract)]
    from .exits import install as install_exits
    install_exits(catalog)
    from .partial_exits import install as install_partial
    install_partial(catalog)
    from .pending_entries import install as install_pending
    install_pending(catalog)
    from .lots import install as install_lots
    install_lots(catalog)
    from .order_groups import install as install_order_groups
    install_order_groups(catalog)
    from .trailing import install as install_trailing
    install_trailing(catalog)
    from .economics import install as install_economics
    install_economics(catalog)
    from .risk import install as install_risk
    install_risk(catalog)
    from .performance import install as install_performance
    install_performance(catalog)
    from .intrabar import install as install_intrabar
    install_intrabar(catalog)
    from .strategy_cycle import install as install_cycle
    install_cycle(catalog)


def bind(binder,call,signature,contract,top_level):
    if call.name.startswith("strategy.risk."):
        if not top_level:binder.fail('PROFILE_UNCOVERED','RISK_POSITION','Risk rules require global statements.',call.span)
        return binder.call(call,tuple(binder.expression(a.expression) for a in call.arguments),signature,contract,top_level)
    version=binder.catalog.language_version
    if call.name=='strategy' and binder.catalog.lot_header:
        pyramiding=next((a.expression for a in call.arguments if a.name=='pyramiding'),None)
        if any(a.name=='close_entries_rule' for a in call.arguments) or isinstance(pyramiding,parser.Literal) and type(pyramiding.value) is int and pyramiding.value>0:
            signature,contract=binder.catalog.lot_header
    if call.name=='strategy' and binder.catalog.economics_header:
        for argument in call.arguments:
            expression=argument.expression
            value=expression.name.rsplit('.',1)[-1] if isinstance(expression,parser.Reference) else expression.value if isinstance(expression,parser.Literal) else None
            if argument.name=='default_qty_type' and value in ('cash','percent_of_equity') or argument.name=='commission_type' and value in ('cash_per_contract','cash_per_order'):
                signature,contract=binder.catalog.economics_header;break
    if call.name=='strategy' and any(a.name=='calc_on_every_tick' for a in call.arguments):
        from .intrabar import header
        signature,contract=header(binder,call)
    if call.name=='strategy' and any(a.name in ('calc_on_order_fills','process_orders_on_close') for a in call.arguments):
        from .strategy_cycle import header
        signature,contract=header(binder,call)
    if call.name in binder.catalog.pending_entries and any(a.name in ('stop','limit') for a in call.arguments):
        signature,contract=binder.catalog.pending_entries[call.name]
    if call.name in binder.catalog.partial_exits and any(a.name in ('qty','qty_percent') for a in call.arguments):
        signature,contract=binder.catalog.partial_exits[call.name]
    if call.name in binder.catalog.order_groups and any(a.name in ('oca_name','oca_type') for a in call.arguments):
        signature,contract=binder.catalog.order_groups[call.name]
    if call.name in binder.catalog.trailing and any(a.name in ('profit','loss','trail_price','trail_points','trail_offset') for a in call.arguments):
        signature,contract=binder.catalog.trailing[call.name]
    max_positional=3 if call.name in ('strategy.entry','strategy.order') else 2 if call.name=='strategy.exit' else 0 if call.name in ('strategy.cancel_all','strategy.close_all') else 1
    if sum(a.name is None for a in call.arguments)>max_positional:
        binder.fail('PROFILE_UNCOVERED','STRATEGY_POSITIONAL','Optional strategy arguments require their version-specific names.',call.span)
    if call.name in ('strategy.entry','strategy.order') and version>=5:
        direction=next((a.expression for i,a in enumerate(call.arguments) if a.name=='direction' or a.name is None and i==1),None)
        if not isinstance(direction,parser.Reference) or direction.name not in ('strategy.long','strategy.short'):
            binder.fail('PROFILE_UNCOVERED','STRATEGY_DIRECTION','Use strategy.long or strategy.short for this Pine version.',call.span)
    return binder.call(call,tuple(binder.expression(a.expression) for a in call.arguments),signature,contract,top_level)
