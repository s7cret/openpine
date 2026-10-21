"""One bounded strategy shape shared by compiler and artifact admission."""

from dataclasses import fields,is_dataclass
from .model import CallIR,ReferenceIR,IfIR,LiteralIR,ConvertIR,Diagnostic,OpenPineError,HISTORICAL_PROFILE_ID
from .trading import BrokerPolicy,BrokerSettings,decimal_text,strategy_policy


def invalid(message,node,code='STRATEGY_PROFILE'):
    raise OpenPineError(Diagnostic('PROFILE_UNCOVERED',code,message,getattr(node,'span',None)))


def literal(node):
    if isinstance(node,ConvertIR) and isinstance(node.operand,LiteralIR):return float(node.operand.value)
    if isinstance(node,LiteralIR):return node.value
    invalid('Strategy settings and entry IDs must be literals in this profile.',node)


def strategy_nodes(program):
    header=program.statements[0] if program.statements else None
    strategy=isinstance(header,CallIR) and header.operation=='simulation.strategy'
    if strategy and len(header.arguments)!=9:invalid('Strategy header parameter count differs from its profile.',header)
    orders=[]
    def walk(node,statement=False):
        if isinstance(node,CallIR) and node.operation.startswith('simulation.'):
            if not strategy or program.identity.implementation_profile_id!=HISTORICAL_PROFILE_ID:
                invalid('Trading requires a historical strategy declaration.',node)
            if node.operation=='simulation.strategy':
                if node is not header:invalid('Exactly one global strategy declaration is required.',node)
                for argument in node.arguments:literal(argument.expression)
                return
            if node.operation not in ('simulation.entry','simulation.close') or not statement:
                invalid('Orders must be statements in global/if blocks.',node)
            expected=(4 if program.identity.language_version<6 else 3) if node.operation=='simulation.entry' else (2 if program.identity.language_version<6 else 1)
            if len(node.arguments)!=expected:invalid('Order parameter count differs from its version profile.',node)
            if node.operation=='simulation.entry' and program.identity.language_version>=5:
                direction=node.arguments[1].expression
                if not isinstance(direction,LiteralIR) or type(direction.value) is not str or direction.value not in ('long','short'):
                    invalid('Invalid typed strategy direction constant.',node)
            entry_id=literal(node.arguments[0].expression)
            if type(entry_id) is not str or not 0<len(entry_id)<=128:invalid('Entry ID requires a nonempty literal string of at most 128 characters.',node)
            orders.append(node)
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
    if len(orders)>64:invalid('At most 64 written order sites are admitted.',header)
    if strategy:
        values=[literal(a.expression) for a in header.arguments]
        _,_,pyramiding,sizing,_,_,slippage,commission,_=values
        if type(pyramiding) is not int or pyramiding!=0 or sizing!='fixed' or commission!='percent' or type(slippage) is not int or not 0<=slippage<=1000000:
            invalid('This strategy profile requires pyramiding=0, fixed quantity and percent commission.',header)
        if strategy_policy(program.identity.language_version)['require_explicit_fixed'] and header.arguments[3].origin!='source':
            invalid('Pine v6 requires explicit default_qty_type=strategy.fixed in this bounded profile.',header,'STRATEGY_SIZING')
        try:
            from decimal import Decimal
            decimal_text(values[4],positive=True);decimal_text(values[5],positive=True)
            if not 0<=Decimal(decimal_text(values[8]))<=100:raise ValueError('Commission percent requires 0..100.')
        except ValueError as error:invalid(str(error),header,'STRATEGY_SETTINGS')
    return tuple(orders)


def broker_policy(program,settings=None):
    strategy_nodes(program)
    if not program.statements or program.statements[0].operation!='simulation.strategy':return None
    v=[literal(a.expression) for a in program.statements[0].arguments]
    try:return BrokerPolicy(decimal_text(v[5],positive=True),decimal_text(v[4],positive=True),decimal_text(v[8]),v[6],settings or BrokerSettings())
    except ValueError as error:invalid(str(error),program.statements[0],'STRATEGY_SETTINGS')
