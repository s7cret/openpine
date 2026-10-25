"""One bounded strategy shape shared by compiler and artifact admission."""
from packages.contracts.trading import STRATEGY_DECLARATIONS

from dataclasses import fields,is_dataclass
from .model import CallIR,ReferenceIR,IfIR,LiteralIR,NAIR,ConvertIR,Diagnostic,OpenPineError,HISTORICAL_PROFILE_ID
from .trading import BrokerPolicy,BrokerSettings,decimal_text,strategy_policy
from .protection import OPERATIONS,PROFILE
from .partial_exits import OPERATIONS as PARTIAL_OPERATIONS,PROFILE as PARTIAL_PROFILE
from .pending_entries import OPERATION as PENDING_OPERATION,PROFILE as PENDING_PROFILE
from .lots import OPERATION as LOT_OPERATION,LotBrokerPolicy
from .order_groups import OPERATIONS as GROUP_OPERATIONS,OrderGroupPolicy
from .trailing import OPERATION as TRAILING_OPERATION,TrailingPolicy
from .risk import OPERATIONS as RISK_OPERATIONS,RiskPolicy,program_rules,rule_from_node
from .economics import OPERATION as ECONOMIC_OPERATION,EconomicsPolicy,EconomicsSettings,CommissionPolicy


def invalid(message,node,code='STRATEGY_PROFILE'):
    raise OpenPineError(Diagnostic('PROFILE_UNCOVERED',code,message,getattr(node,'span',None)))


def literal(node):
    if isinstance(node,ConvertIR) and isinstance(node.operand,LiteralIR):return float(node.operand.value)
    if isinstance(node,LiteralIR):return node.value
    invalid('Strategy settings and entry IDs must be literals in this profile.',node)


def strategy_nodes(program):
    header=program.statements[0] if program.statements else None
    strategy=isinstance(header,CallIR) and header.operation in STRATEGY_DECLARATIONS
    lots=strategy and header.operation in (LOT_OPERATION,ECONOMIC_OPERATION)
    if strategy and len(header.arguments)!=(10 if lots else 9):invalid('Strategy header parameter count differs from its profile.',header)
    orders=[]
    from .performance import OPERATION as TRADE_READ

    def walk(node,statement=False):
        if isinstance(node,CallIR) and node.operation.startswith('simulation.'):
            if not strategy or program.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID:
                invalid('Trading requires a historical strategy declaration.',node)
            if node.operation in STRATEGY_DECLARATIONS:
                if node is not header:invalid('Exactly one global strategy declaration is required.',node)
                for argument in node.arguments:literal(argument.expression)
                return
            if node.operation in RISK_OPERATIONS:
                if not statement or not any(node is n for n in program.statements):invalid('Risk declarations must be global literals.',node,'RISK_POSITION')
                try:rule_from_node(node)
                except ValueError as error:invalid(str(error),node,'RISK_SETTINGS')
                return
            if node.operation not in ('simulation.entry','simulation.close',PENDING_OPERATION,*OPERATIONS,*PARTIAL_OPERATIONS,*GROUP_OPERATIONS,TRAILING_OPERATION) or not statement:
                invalid('Orders must be statements in global/if blocks.',node)
            when=int(program.identity.language_version<6)
            expected={'simulation.entry':3+when,PENDING_OPERATION:5+when,'simulation.close':1+when,'simulation.exit':4+when,
                      'simulation.exit_partial':6+when,'simulation.close_partial':3+when,'simulation.cancel':1+when,'simulation.cancel_all':when,
                      'simulation.order':7+when,'simulation.entry_group':7+when,'simulation.exit_group':7+when,'simulation.close_all':when,TRAILING_OPERATION:12+when}[node.operation]
            if len(node.arguments)!=expected:invalid('Order parameter count differs from its version profile.',node)
            if node.operation in ('simulation.entry',PENDING_OPERATION,'simulation.order','simulation.entry_group') and program.identity.language_version>=5:
                direction=node.arguments[1].expression
                if not isinstance(direction,LiteralIR) or type(direction.value) is not str or direction.value not in ('long','short'):
                    invalid('Invalid typed strategy direction constant.',node)
            if node.operation==TRAILING_OPERATION:
                activation=any(node.arguments[i].origin=='source' for i in (9,10))
                offset=node.arguments[11].origin=='source'
                if activation!=offset:invalid('Trailing requires trail_price/trail_points and trail_offset together.',node,'EXIT_TRAILING_REQUIRED')
            for argument in node.arguments[:2 if node.operation in ('simulation.exit','simulation.exit_partial','simulation.exit_group',TRAILING_OPERATION) else 0 if node.operation in ('simulation.cancel_all','simulation.close_all') else 1]:
                entry_id=literal(argument.expression)
                if type(entry_id) is not str or not 0<len(entry_id)<=128:invalid('Order IDs require nonempty literal strings of at most 128 characters.',node)
            if node.operation in ('simulation.exit','simulation.exit_partial','simulation.exit_group',TRAILING_OPERATION) and all(a.origin!='source' for i,a in enumerate(node.arguments) if i in ((2,3,7,8,9,10) if node.operation==TRAILING_OPERATION else (2,3))):
                invalid('Protective exit requires stop or limit.',node,'EXIT_LEVEL_REQUIRED')
            if node.operation in (*GROUP_OPERATIONS,TRAILING_OPERATION) and node.operation!='simulation.close_all':
                name=literal(node.arguments[6 if node.operation in ('simulation.exit_group',TRAILING_OPERATION) else 5].expression)
                kind=('reduce' if name else 'none') if node.operation in ('simulation.exit_group',TRAILING_OPERATION) else literal(node.arguments[6].expression)
                if type(name) is not str or len(name)>128 or kind not in ('none','cancel','reduce') or kind!='none' and not name:
                    invalid('OCA requires a bounded literal name and none/cancel/reduce type.',node,'OCA_BINDING')
            orders.append(node)
        if isinstance(node,CallIR) and node.operation==TRADE_READ and not strategy:invalid('Trade readers require a strategy declaration.',node,'TRADE_STRATEGY')
        if isinstance(node,ReferenceIR) and node.contract.slot.startswith('strategy.') and not strategy:
            invalid('Position/equity references require a strategy declaration.',node)
        if isinstance(node,IfIR):
            walk(node.condition)
            for child in (*node.body,*node.otherwise):walk(child,True)
        elif is_dataclass(node):
            for field in fields(node):walk(getattr(node,field.name))
        elif type(node) is tuple:
            for item in node:walk(item)
    for statement in program.statements:walk(statement,True)
    program_rules(program)
    if len(orders)>64:invalid('At most 64 written order sites are admitted.',header)
    if strategy:
        values=[literal(a.expression) for a in header.arguments]
        _,_,pyramiding,sizing,_,_,slippage,commission,_=values[:9]
        economic=header.operation==ECONOMIC_OPERATION
        if type(pyramiding) is not int or (not 0<=pyramiding<=16 if lots else pyramiding!=0) or sizing not in (('fixed','cash','percent_of_equity') if economic else ('fixed',)) or commission not in (('percent','cash_per_contract','cash_per_order') if economic else ('percent',)) or type(slippage) is not int or not 0<=slippage<=1000000 or lots and values[9] not in ('FIFO','ANY'):
            invalid('Strategy requires bounded pyramiding, fixed quantity, percent commission and an admitted close rule.',header)
        if strategy_policy(program.identity.language_version)['require_explicit_fixed'] and not economic and header.arguments[3].origin!='source':
            invalid('Pine v6 requires explicit default_qty_type=strategy.fixed in this bounded profile.',header,'STRATEGY_SIZING')
        try:
            from decimal import Decimal
            decimal_text(values[4],positive=not economic or sizing=='fixed');decimal_text(values[5],positive=True)
            if economic and (Decimal(decimal_text(values[4]))<0 or sizing=='percent_of_equity' and Decimal(decimal_text(values[4]))>100):raise ValueError('Cash must be nonnegative; equity percentage requires 0..100.')
            if Decimal(decimal_text(values[8]))<0 or (commission=='percent' and Decimal(decimal_text(values[8]))>100):raise ValueError('Commission percent requires 0..100.')
        except ValueError as error:invalid(str(error),header,'STRATEGY_SETTINGS')
    return tuple(orders)


def broker_policy(program,settings=None):
    orders=strategy_nodes(program)
    if not program.statements or program.statements[0].operation not in STRATEGY_DECLARATIONS:return None
    v=[literal(a.expression) for a in program.statements[0].arguments]
    limits=program_rules(program)
    from .performance import PerformancePolicy,uses_performance
    performance=uses_performance(program)
    if program.statements[0].operation==ECONOMIC_OPERATION or type(settings) is EconomicsSettings or limits or performance:
        if any(node.operation==TRAILING_OPERATION for node in orders) and (settings is None or settings.tick_size is None):
            raise OpenPineError(Diagnostic('INPUT_UNAVAILABLE','EXIT_TICK_METADATA','Relative/trailing exits require pinned tick metadata.',program.statements[0].span,language_version=program.identity.language_version))
        try:return (PerformancePolicy if performance else RiskPolicy if limits else EconomicsPolicy)(decimal_text(v[5],positive=True),decimal_text(v[4]),'0',v[6],settings or BrokerSettings(),pyramiding=v[2],close_entries_rule=v[9] if len(v)==10 else 'FIFO',language_version=program.identity.language_version,quantity_mode=v[3],commission=CommissionPolicy(v[7],decimal_text(v[8])),**({'limits':limits} if limits or performance else {}))
        except ValueError as error:invalid(str(error),program.statements[0],'STRATEGY_SETTINGS')
    if any(node.operation==TRAILING_OPERATION for node in orders):
        if settings is None or type(settings) is BrokerSettings and settings.tick_size is None:
            raise OpenPineError(Diagnostic('INPUT_UNAVAILABLE','EXIT_TICK_METADATA','Relative/trailing exits require pinned tick_size metadata.',program.statements[0].span,language_version=program.identity.language_version))
        try:return TrailingPolicy(decimal_text(v[5],positive=True),decimal_text(v[4],positive=True),decimal_text(v[8]),v[6],settings or BrokerSettings(),pyramiding=v[2],close_entries_rule=v[9] if len(v)==10 else 'FIFO',language_version=program.identity.language_version)
        except ValueError as error:raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','STRATEGY_SETTINGS',str(error),program.statements[0].span,language_version=program.identity.language_version)) from error
    if any(node.operation in GROUP_OPERATIONS for node in orders):
        try:return OrderGroupPolicy(decimal_text(v[5],positive=True),decimal_text(v[4],positive=True),decimal_text(v[8]),v[6],settings or BrokerSettings(),pyramiding=v[2],close_entries_rule=v[9] if len(v)==10 else 'FIFO')
        except ValueError as error:invalid(str(error),program.statements[0],'STRATEGY_SETTINGS')
    if program.statements[0].operation==LOT_OPERATION:
        try:return LotBrokerPolicy(decimal_text(v[5],positive=True),decimal_text(v[4],positive=True),decimal_text(v[8]),v[6],settings or BrokerSettings(),pyramiding=v[2],close_entries_rule=v[9])
        except ValueError as error:invalid(str(error),program.statements[0],'STRATEGY_SETTINGS')
    try:return BrokerPolicy(decimal_text(v[5],positive=True),decimal_text(v[4],positive=True),decimal_text(v[8]),v[6],settings or BrokerSettings(),
        profile_id=PENDING_PROFILE if any(node.operation==PENDING_OPERATION for node in orders) else PARTIAL_PROFILE if any(node.operation in PARTIAL_OPERATIONS for node in orders) else PROFILE if any(node.operation in OPERATIONS for node in orders) else 'openpine.market-broker.1')
    except ValueError as error:invalid(str(error),program.statements[0],'STRATEGY_SETTINGS')


def group_site_rules(program):
    result={}
    for node in strategy_nodes(program):
        operation=node.operation;arguments=node.arguments
        command='entry' if operation in ('simulation.entry',PENDING_OPERATION,'simulation.entry_group') else 'order' if operation=='simulation.order' else 'exit' if operation in ('simulation.exit','simulation.exit_partial','simulation.exit_group',TRAILING_OPERATION) else 'close_all' if operation=='simulation.close_all' else 'close' if operation in ('simulation.close','simulation.close_partial') else 'cancel'
        rule={'command':command,'entry_id':'','exit_id':'','oca_name':'','oca_type':'none'}
        if command in ('entry','order','close'):rule['entry_id']=literal(arguments[0].expression)
        if command=='exit':rule.update(exit_id=literal(arguments[0].expression),entry_id=literal(arguments[1].expression))
        if operation in ('simulation.entry_group','simulation.order'):
            rule.update(oca_name=literal(arguments[5].expression),oca_type=literal(arguments[6].expression))
        elif operation in ('simulation.exit_group',TRAILING_OPERATION):
            rule['oca_name']=literal(arguments[6].expression);rule['oca_type']='reduce' if rule['oca_name'] else 'none'
        if command=='exit':
            from .trailing import PARAMETERS
            slots={'stop':2,'limit':3,**{name:7+i for i,name in enumerate(PARAMETERS)}}
            prices={}
            for name,index in slots.items():
                if index>=7 and operation!=TRAILING_OPERATION:prices[name]={'dynamic':False,'value':None};continue
                expression=arguments[index].expression
                if arguments[index].origin!='source' or isinstance(expression,NAIR):prices[name]={'dynamic':False,'value':None}
                elif isinstance(expression,LiteralIR):prices[name]={'dynamic':False,'value':expression.value}
                elif isinstance(expression,ConvertIR) and isinstance(expression.operand,LiteralIR):prices[name]={'dynamic':False,'value':literal(expression)}
                else:prices[name]={'dynamic':True,'value':None}
            rule['exit_price_arguments']=prices;rule['extended']=operation==TRAILING_OPERATION
        result[node.call_site_id]=rule
    return result
