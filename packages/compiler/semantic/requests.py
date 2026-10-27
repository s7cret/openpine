"""Install the separately pinned lazy request-expression contract."""

from dataclasses import replace
from packages.contracts import CallContract,ValueType
from packages.contracts.contexts import request_policy
from .specification import value_sha,SpecError
from packages.compiler.frontend import parser


def install(catalog):
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
        if target and not isinstance(expression,BoundLiteral):
            nonliteral=True;computed=computed or not aliases(argument.expression)
    if computed:
        from packages.contracts.computed_targets import contract as computed_contract
        return computed_contract(contract,version)
    if nonliteral:
        from packages.contracts.prepared_requests import prepared_contract
        return prepared_contract(contract,version)
    return contract
