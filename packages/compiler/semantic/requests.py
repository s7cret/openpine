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
    return binder.call(call,tuple(binder.expression(a.expression) for a in call.arguments),signature,contract,top_level)
