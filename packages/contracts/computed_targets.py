"""Finite const/input target graphs, admitted completely before lazy evaluation."""
import math
from dataclasses import replace
from .model import LiteralIR,LoadIR,InputIR,StoreIR,BinaryIR,ConditionalIR,UnaryIR,ConvertIR,ReferenceIR,ValueType,Diagnostic,OpenPineError
from .wire import digest,read_json
from .scalar_ops import binary

PROFILE='openpine.computed-targets.1'
SUFFIX='.computed.prepared'

def policy(version):
    return {'profile_id':PROFILE,'language_version':version,'qualifiers':['const','input'],
        'nodes':['literal','input','load','binary','unary','convert','conditional'],
        'inputs':['symbol','timeframe'] if version>=5 else [],'max_nodes':128,'max_depth':16,'max_string':4096,
        'validation':'all_branches','evaluation':'versioned_scalar_lazy_conditional','resolution':'before_data_io',
        'empty_target':'reject_explicit_context_required','external_equivalence':False}

def contract(base,version):
    return replace(base,overload_id=base.overload_id+SUFFIX,assertion_id=base.assertion_id+SUFFIX,
        signature_sha256=digest({'base':base.signature_sha256,'policy':policy(version)}),
        qualifier_limits=('input','input',*base.qualifier_limits[2:]))

def admit_profile(root,snapshot,version):
    value=read_json(root/'spec/computed-targets-profile.json');row=value['versions'][str(version)]
    if (value['source_lock_sha256']!=digest(read_json(root/'spec/lock.json')) or row['policy']!=policy(version)
            or row['reference_sha256']!=digest(snapshot.references[version])):
        raise ValueError('Computed target policy differs from pinned reference evidence.')

def input_profile(version):return f'openpine.target-inputs.v{version}.1'

def validate_text(kind,value):
    if type(value) is not str or not value:raise ValueError('Use an explicit nonempty '+kind+'; the chart context is not implicit.')
    if kind=='symbol':
        if len(value)>256 or ':' not in value or any(c.isspace() for c in value) or any(not p for p in value.split(':')):
            raise ValueError('Use an explicit qualified symbol, for example BINANCE:BTCUSDT.')
    elif kind=='timeframe':
        from .calendar import timeframe
        timeframe(value)

def evaluate(program,expression,settings=None,*,allow_primary=False,primary=None,shape_only=False):
    from .context_ir import ir_nodes
    from .inputs import admitted_settings
    selected=admitted_settings(program,settings);dependencies=set();active=set();count=0
    declarations={n.binding.binding_id:n for n in program.statements if isinstance(n,StoreIR) and n.declaration}
    modified={n.binding.binding_id for n in ir_nodes(program.statements) if isinstance(n,StoreIR) and not n.declaration}
    bindings={b.binding_id:b for b in program.bindings}
    def fail(message,node=expression):raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','REQUEST_TARGET',message,node.span,
        language_version=program.identity.language_version))
    def declaration(node):
        b=bindings.get(node.binding_id);d=declarations.get(node.binding_id)
        if (b is None or d is None or b.scope_id!='scope:root' or b.mode!='bar' or b.namespace
                or b.binding_id in modified or b.value_type!=node.value_type):fail('Frozen targets require immutable unconditional global declarations.',node)
        return d
    def validate(node,depth=0):
        nonlocal count
        count+=1
        if count>128 or depth>16:fail('Frozen target graph exceeds 128 nodes or depth 16.',node)
        if node.value_type.base not in ('string','int','float','bool') or node.value_type.qualifier not in (('const','input','simple') if allow_primary else ('const','input')):
            fail('Frozen targets require const/input scalars; per-bar state is unavailable.',node)
        if isinstance(node,LiteralIR):return
        if isinstance(node,ReferenceIR) and allow_primary:
            from .primary_targets import SLOTS
            if node.contract.slot not in SLOTS or node.value_type!=ValueType('string','simple'):fail('Only primary tickerid/period references are admitted in targets.',node)
            dependencies.add('primary:'+SLOTS[node.contract.slot]);return
        if isinstance(node,InputIR):
            if node.definition.input_type=='source':fail('Source inputs require market data.',node)
            dependencies.add(node.definition.input_id);return
        if isinstance(node,LoadIR):
            d=declaration(node)
            if node.binding_id in active:fail('Frozen target dependency cycle.',node)
            active.add(node.binding_id);validate(d.expression,depth+1);active.remove(node.binding_id);return
        if isinstance(node,ConditionalIR):children=(node.condition,node.when_true,node.when_false)
        elif isinstance(node,BinaryIR):
            if node.operator not in ('+','-','*','/','%','==','!=','<','<=','>','>=','and','or'):fail('Unsupported frozen binary operator.',node)
            children=(node.left,node.right)
        elif isinstance(node,(UnaryIR,ConvertIR)):
            if isinstance(node,UnaryIR) and node.operator not in ('+','-','not'):fail('Unsupported frozen unary operator.',node)
            children=(node.operand,)
        else:fail('Frozen targets exclude calls, history, loops, mutable state and nested requests.',node)
        for child in children:validate(child,depth+1)
    validate(expression)
    if expression.value_type.base!='string':fail('Request targets must resolve to strings.')
    if shape_only:return None,tuple(sorted(dependencies))
    memo={}
    def visit(node):
        if isinstance(node,LiteralIR):value=node.value
        elif isinstance(node,InputIR):value=selected['values'][node.definition.input_id]
        elif isinstance(node,ReferenceIR) and allow_primary:
            from .primary_targets import SLOTS,PrimaryRequired
            field=SLOTS[node.contract.slot]
            if primary is None:raise PrimaryRequired(field)
            value=primary[field]
        elif isinstance(node,LoadIR):
            if node.binding_id not in memo:memo[node.binding_id]=visit(declaration(node).expression)
            value=memo[node.binding_id]
        elif isinstance(node,ConditionalIR):value=visit(node.when_true if visit(node.condition) else node.when_false)
        elif isinstance(node,BinaryIR):
            left=visit(node.left)
            if program.language_policy.logical_evaluation=='lazy' and ((node.operator=='and' and left is False) or (node.operator=='or' and left is True)):value=left
            else:value=binary(node.operator,left,visit(node.right),node.value_type.base,program.language_policy)
        else:
            operand=visit(node.operand)
            if isinstance(node,ConvertIR):value=(False if operand is None else bool(operand)) if node.value_type.base=='bool' else None if operand is None else float(operand)
            else:value=None if operand is None else not operand if node.operator=='not' else -operand if node.operator=='-' else operand
        if (type(value) is str and len(value)>4096 or type(value) is int and not -(2**63)<=value<2**63
                or type(value) is float and not math.isfinite(value)):fail('Frozen target value exceeds its scalar range.',node)
        return value
    return visit(expression),tuple(sorted(dependencies))
