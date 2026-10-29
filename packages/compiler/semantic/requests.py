"""Install the separately pinned lazy request-expression contract."""

from dataclasses import replace
from packages.contracts import CallContract,ValueType
from packages.contracts.contexts import request_policy
from .specification import value_sha,SpecError
from packages.compiler.frontend import parser


def install(catalog):
    from packages.contracts.request_environment import admit
    from .specification import ROOT
    admit(ROOT,catalog.snapshot,catalog.language_version)
    from .lower_tf import install as install_lower
    install_lower(catalog)
    release=catalog.snapshot.requests
    if not release or "close" not in catalog.references:return
    version=catalog.language_version;row=release["versions"][str(version)];policy=row["policy"]
    if policy!=request_policy(version):raise SpecError("IMPLEMENTATION_MISSING","Request policy is not implemented")
    oid=f"overload.openpine.requests.v{version}.security"
    flag_type="bool" if version<5 else "string"
    params=[]
    for index,name in enumerate(policy["parameters"]):
        params.append({"name":name,"parameter_id":f"parameter.{oid}.{name}",
            "requiredness":{"state":"required" if index<3 else "optional"},
            "default":{"kind":"none"} if index<3 else {"kind":"literal","value":False if version<5 else "off"}})
    signature={"parameters":params,"binding":{"named":policy["named"]},"effects":["requests_context","reads_history"]}
    contract=CallContract(f"symbol.openpine.requests.v{version}.security",oid,f"assertion.openpine.requests.v{version}.security",
        value_sha(row),"context.request",tuple(p["parameter_id"] for p in params),
        (("string",),("string",),("float",'bool','tuple'),*((flag_type,) for _ in params[3:])),
        ("const","const","series",*("const" for _ in params[3:])),None,
        ("requests_context","reads_history"),ValueType("float","series"),"request_series")
    catalog.contracts[oid]=contract;catalog.signatures[policy["name"]]=[(signature,contract)]
    register_prepared(catalog,contract)

def register_prepared(catalog,contract):
    from packages.contracts.prepared_requests import prepared_contract,policy
    from packages.contracts.wire import read_json,digest
    from .specification import ROOT
    profile=read_json(ROOT/'spec/prepared-requests-profile.json')
    # Pin this installation's overlay and the selected snapshot's actual reference
    # evidence; unrelated release repins retain their own policy diagnostics.
    version=profile['versions'][str(catalog.language_version)]
    if (profile['source_lock_sha256']!=digest(read_json(ROOT/'spec/lock.json'))
            or version['reference_sha256']!=digest(catalog.snapshot.references[catalog.language_version])
            or version['policy']!=policy(catalog.language_version)):
        raise SpecError('SPEC_EVIDENCE','Prepared request policy differs from its pinned evidence.')
    prepared=prepared_contract(contract,catalog.language_version);catalog.contracts[prepared.overload_id]=prepared
    from packages.contracts.computed_targets import contract as computed_contract,admit_profile
    try:admit_profile(ROOT,catalog.snapshot,catalog.language_version)
    except ValueError as error:raise SpecError('SPEC_EVIDENCE',str(error)) from error
    computed=computed_contract(contract,catalog.language_version);catalog.contracts[computed.overload_id]=computed
    from packages.contracts.primary_targets import contract as primary_contract,admit_profile as admit_primary
    try:admit_primary(ROOT,catalog.snapshot,catalog.language_version)
    except ValueError as error:raise SpecError('SPEC_EVIDENCE',str(error)) from error
    primary=primary_contract(contract,catalog.language_version);catalog.contracts[primary.overload_id]=primary
    from packages.contracts.function_targets import contract as function_contract,admit_profile as admit_functions
    try:admit_functions(ROOT,catalog.snapshot,catalog.language_version)
    except ValueError as error:raise SpecError('SPEC_EVIDENCE',str(error)) from error
    functions=function_contract(contract,catalog.language_version);catalog.contracts[functions.overload_id]=functions
    from packages.contracts.scalar_targets import contract as scalar_contract
    scalar=scalar_contract(contract,catalog.language_version);catalog.contracts[scalar.overload_id]=scalar
    from packages.contracts.numeric_targets import contract as numeric_contract
    numeric=numeric_contract(contract,catalog.language_version);catalog.contracts[numeric.overload_id]=numeric


def bind(binder,call,signature,contract,top_level):
    policy=request_policy(binder.catalog.language_version);positions={n:i for i,n in enumerate(policy["parameters"])}
    arguments=[];positional=0
    for argument in call.arguments:
        index=positions.get(argument.name,-1) if argument.name is not None else positional
        if argument.name is None:positional+=1
        expression=argument.expression
        if index>=3:
            family="gaps" if index==3 else "lookahead"
            if binder.catalog.language_version>=3 and isinstance(expression,parser.Reference) and expression.name in (f"barmerge.{family}_off",f"barmerge.{family}_on"):
                on=expression.name.endswith("_on")
                expression=parser.Literal(on if policy["flags"]=="bool" else "on" if on else "off",expression.span)
            elif policy["flags"]!="bool" or not isinstance(expression,parser.Literal) or type(expression.value) is not bool:
                binder.fail("PROFILE_UNCOVERED","REQUEST_FLAG","Use the version-specific barmerge constant (legacy versions also accept bool).",argument.span)
        arguments.append(replace(argument,expression=expression))
    call=replace(call,arguments=tuple(arguments))
    expressions=tuple(binder.expression(a.expression) for a in call.arguments)
    contract=select_contract(call,expressions,contract,binder.catalog.language_version,binder)
    return binder.call(call,expressions,signature,contract,top_level)

def select_contract(call,expressions,contract,version,binder):
    from .binder import BoundLiteral
    position=0;nonliteral=False;computed=False
    from dataclasses import fields,is_dataclass
    from packages.contracts.primary_targets import SLOTS
    def memoized_classifier(predicate):
        # Source aliases form a DAG. Feature discovery must not expand that DAG
        # before the actual target admission applies its node/depth budgets.
        cache={}
        def visit(node,active=None,**options):
            if active is not None and len(active)>128:return False
            key=(id(node),options.get('empty',False))
            if key not in cache:cache[key]=predicate(node,active,**options)
            return cache[key]
        return visit
    @memoized_classifier
    def function_feature(node,active=None):
        active=set() if active is None else active
        if len(active)>128:return False
        if isinstance(node,parser.Reference):
            owner=binder.resolver.references.get(id(node))
            if owner is None or id(owner) in active:return False
            active.add(id(owner));result=function_feature(owner.declaration.expression,active);active.remove(id(owner));return result
        if isinstance(node,parser.Call) and node.name in binder.shared['functions']:return True
        if is_dataclass(node):return any(function_feature(getattr(node,f.name),active) for f in fields(node) if f.name!='span')
        if type(node) is tuple:return any(function_feature(x,active) for x in node)
        return False
    @memoized_classifier
    def numeric_feature(node,active=None):
        active=set() if active is None else active
        if isinstance(node,parser.Reference):
            owner=binder.resolver.references.get(id(node))
            if owner is None or id(owner) in active:return False
            active.add(id(owner));result=numeric_feature(owner.declaration.expression,active);active.remove(id(owner));return result
        from packages.contracts.numeric_functions import apis
        if isinstance(node,parser.Call):
            if node.name in {a["name"] for a in apis(version)}|({"abs","round","min","max"} if version<5 else {"math.abs","math.round","math.min","math.max"})|{"int","float","na","nz"}:return True
            fn=binder.shared["functions"].get(node.name)
            if fn is not None:
                if id(fn) in active:return False
                active.add(id(fn));result=numeric_feature(fn,active);active.remove(id(fn))
                if result:return True
        if is_dataclass(node):return any(numeric_feature(getattr(node,f.name),active) for f in fields(node) if f.name!="span")
        if type(node) in (tuple,list):return any(numeric_feature(x,active) for x in node)
        return False
    for i,arg in enumerate(call.arguments):
        target=arg.name in ("symbol","timeframe","resolution") if arg.name is not None else i<2
        if target and numeric_feature(arg.expression):
            from packages.contracts.numeric_targets import contract as numeric_contract
            return numeric_contract(contract,version)
    @memoized_classifier
    def scalar_feature(node,active=None):
        active=set() if active is None else active
        if isinstance(node,parser.Reference):
            owner=binder.resolver.references.get(id(node))
            if owner is None or id(owner) in active:return False
            active.add(id(owner));result=scalar_feature(owner.declaration.expression,active);active.remove(id(owner));return result
        from packages.contracts.scalar_functions import apis
        if isinstance(node,parser.Call):
            if node.name in {a["name"] for a in apis(version)}:return True
            fn=binder.shared["functions"].get(node.name)
            if fn is not None:
                if id(fn) in active:return False
                active.add(id(fn));result=scalar_feature(fn,active);active.remove(id(fn))
                if result:return True
        if is_dataclass(node):return any(scalar_feature(getattr(node,f.name),active) for f in fields(node) if f.name!="span")
        if type(node) in (tuple,list):return any(scalar_feature(x,active) for x in node)
        return False
    for i,arg in enumerate(call.arguments):
        target=arg.name in ("symbol","timeframe","resolution") if arg.name is not None else i<2
        if target and scalar_feature(arg.expression):
            from packages.contracts.scalar_targets import contract as scalar_contract
            return scalar_contract(contract,version)
    # Choose over all targets first: an earlier primary reference must not mask
    # a function in the other target or an unselected branch.
    for i,arg in enumerate(call.arguments):
        target=arg.name in ('symbol','timeframe','resolution') if arg.name is not None else i<2
        if target and function_feature(arg.expression):
            from packages.contracts.function_targets import contract as function_contract
            return function_contract(contract,version)
    @memoized_classifier
    def feature(node,active=None,*,empty=False):
        active=set() if active is None else active
        if len(active)>128:return False
        if isinstance(node,parser.Reference):
            owner=binder.resolver.references.get(id(node))
            if owner is not None:
                if id(owner) in active:return False
                active.add(id(owner));result=feature(owner.declaration.expression,active,empty=empty);active.remove(id(owner));return result
            return not empty and node.name in binder.catalog.references and binder.catalog.references[node.name].slot in SLOTS
        if isinstance(node,parser.Literal):return empty and node.value==''
        if isinstance(node,parser.Call):
            if not empty:return node.name in ('input.symbol','input.timeframe') or any(feature(a.expression,active) for a in node.arguments)
            return node.name in ('input','input.string') and any((a.name in (None,'defval','options')) and (feature(a.expression,active,empty=True) or isinstance(a.expression,parser.InputOptions) and any(feature(x,active,empty=True) for x in a.expression.values)) for a in node.arguments)
        if empty:
            if isinstance(node,parser.Conditional):return feature(node.when_true,active,empty=True) or feature(node.when_false,active,empty=True)
            if isinstance(node,parser.Binary) and node.operator=='+':return feature(node.left,active,empty=True) and feature(node.right,active,empty=True)
            return False
        if is_dataclass(node):return any(feature(getattr(node,f.name),active) for f in fields(node) if f.name!='span')
        if type(node) is tuple:return any(feature(x,active) for x in node)
        if hasattr(node,'expression'):return feature(node.expression,active)
        return False
    def aliases(node,active=None,budget=None):
        active=set() if active is None else active;budget=[0] if budget is None else budget
        budget[0]+=1
        if budget[0]>64:return False
        if isinstance(node,parser.Literal):return type(node.value) is str
        if isinstance(node,parser.Call):return node.name in ('input','input.string')
        if not isinstance(node,parser.Reference):return False
        owner=binder.resolver.references.get(id(node))
        if (owner is None or owner.scope_id!='scope:root' or owner.namespace or owner.mutable
                or owner.declaration.mode!='bar' or id(owner) in active):return False
        active.add(id(owner));result=aliases(owner.declaration.expression,active,budget);active.remove(id(owner));return result
    for argument,expression in zip(call.arguments,expressions,strict=True):
        target=argument.name in ('symbol','timeframe','resolution') if argument.name is not None else position<2
        if argument.name is None:position+=1
        if target and (feature(argument.expression) or feature(argument.expression,empty=True)):
            from packages.contracts.primary_targets import contract as primary_contract
            return primary_contract(contract,version)
        if target and not isinstance(expression,BoundLiteral):
            nonliteral=True;computed=computed or not aliases(argument.expression)
    if computed:
        from packages.contracts.computed_targets import contract as computed_contract
        return computed_contract(contract,version)
    if nonliteral:
        from packages.contracts.prepared_requests import prepared_contract
        return prepared_contract(contract,version)
    return contract
