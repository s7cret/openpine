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
    if version >= 4:
        name = "ignore_invalid_symbol"
        parameter = {"name":name, "parameter_id":f"parameter.{oid}.{name}",
                     "requiredness":{"state":"optional"}, "default":{"kind":"literal","value":False}}
        extended = replace(contract, overload_id=oid+".optional_symbol",
            assertion_id=contract.assertion_id+".optional_symbol",
            signature_sha256=value_sha({"base":contract.signature_sha256,"parameter":parameter,"profile":1}),
            parameter_ids=(*contract.parameter_ids,parameter["parameter_id"]),
            parameter_types=(*contract.parameter_types,("bool",)), qualifier_limits=(*contract.qualifier_limits,"const"))
        register_optional_targets(catalog,extended)
        catalog.optional_security = ({**signature,"parameters":[*params,parameter]}, extended)
        catalog.contracts[extended.overload_id] = extended
    if version >= 5:
        install_counted(catalog, signature, extended, params, parameter)
    register_prepared(catalog,contract)
    from .tickers import install as install_fixed_tickers
    install_fixed_tickers(catalog)
    from .corporate import install as install_actions
    install_actions(catalog)
    from .heikin_ashi import install as install_ticker
    ticker = install_ticker(catalog)
    if ticker is not None:
        from packages.contracts.heikin_ashi import request_contract
        transformed = request_contract(contract, version, ticker)
        catalog.contracts[transformed.overload_id] = transformed
        if version >= 5:
            lower = catalog.signatures['request.security_lower_tf'][0][1]
            transformed_lower = request_contract(lower, version, ticker)
            catalog.contracts[transformed_lower.overload_id] = transformed_lower

def install_counted(catalog, signature, extended, params, flag):
    # Keep the archived parameter order, including the native-currency slot.
    parameters = [*params,flag]
    for name, default in (("currency",""),("calc_bars_count",None)):
        parameters.append({"name":name,"parameter_id":extended.parameter_ids[0].rsplit(".",1)[0]+"."+name,
            "requiredness":{"state":"required" if default is None else "optional"},
            "default":{"kind":"none"} if default is None else {"kind":"literal","value":default}})
    counted = replace(extended,overload_id=extended.overload_id+".counted",
        assertion_id=extended.assertion_id+".counted",
        signature_sha256=value_sha({"base":extended.signature_sha256,"parameters":parameters,"count_profile":1}),
        parameter_ids=tuple(p["parameter_id"] for p in parameters),
        parameter_types=(*extended.parameter_types,("string",),("int",)),
        qualifier_limits=(*extended.qualifier_limits,"const","const"))
    for with_count in (False, True):
        selected = parameters if with_count else parameters[:-1]
        base = counted if with_count else extended
        suffix = ".fx"
        converted = replace(base, overload_id=base.overload_id+suffix,
            assertion_id=base.assertion_id+suffix,
            signature_sha256=value_sha({"base":base.signature_sha256,"fx_profile":1}),
            parameter_ids=tuple(p["parameter_id"] for p in selected),
            parameter_types=counted.parameter_types[:len(selected)],
            qualifier_limits=counted.qualifier_limits[:len(selected)])
        catalog.contracts[converted.overload_id]=converted
        register_optional_targets(catalog,converted)
        if not with_count:
            catalog.currency_security=({**signature,"parameters":selected},converted)
    catalog.counted_security = ({**signature,"parameters":parameters},counted)
    catalog.contracts[counted.overload_id]=counted
    register_optional_targets(catalog,counted)


def register_optional_targets(catalog, extended):
    if catalog.language_version >= 5:
        from packages.contracts.dynamic_requests import variant
        dynamic = variant(extended)
        catalog.contracts[dynamic.overload_id] = dynamic
    from packages.contracts.prepared_requests import prepared_contract
    from packages.contracts.computed_targets import contract as computed
    from packages.contracts.primary_targets import contract as primary
    for build in (prepared_contract, computed, primary):
        variant = build(extended, catalog.language_version)
        catalog.contracts[variant.overload_id] = variant


def optional_target(binder, base, selected, extended, span):
    suffix = selected.overload_id.removeprefix(base.overload_id)
    variant = binder.catalog.contracts.get(extended.overload_id + suffix)
    if variant is None:
        binder.fail("PROFILE_UNCOVERED","REQUEST_TARGET","Unsupported target expression for ignore_invalid_symbol.",span)
    return variant


def register_prepared(catalog,contract):
    if catalog.language_version >= 5:
        from packages.contracts.dynamic_requests import variant
        dynamic = variant(contract)
        catalog.contracts[dynamic.overload_id] = dynamic
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
    from packages.contracts.tickers import SUFFIX as fixed_suffix
    if catalog.language_version >= 2:
        fixed = replace(primary, overload_id=contract.overload_id + fixed_suffix,
                        assertion_id=contract.assertion_id + fixed_suffix,
                        signature_sha256=value_sha({'primary':primary.signature_sha256, 'ticker_profile':1}))
        catalog.contracts[fixed.overload_id] = fixed
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
    optional = binder.catalog.language_version >= 4 and (
        any(arg.name == "ignore_invalid_symbol" for arg in call.arguments)
        or sum(arg.name is None for arg in call.arguments) >= 6)
    counted = binder.catalog.language_version >= 5 and (
        any(arg.name=="calc_bars_count" for arg in call.arguments)
        or sum(arg.name is None for arg in call.arguments)>=8)
    currency_arg = next((arg for i,arg in enumerate(call.arguments)
                         if arg.name=="currency" or i==6 and arg.name is None),None)
    if currency_arg is not None and binder.catalog.language_version<5:
        binder.fail("PROFILE_UNCOVERED","REQUEST_CURRENCY_VERSION","Security currency requires Pine v5 or v6.",currency_arg.span)
    currency_slot = currency_arg is not None and binder.catalog.language_version >= 5
    converted = currency_slot and not (isinstance(currency_arg.expression,parser.Literal) and currency_arg.expression.value=="")
    if counted or currency_slot:
        optional = True
    if optional:
        positions["ignore_invalid_symbol"] = 5
        signature, extended = binder.catalog.optional_security
    if counted:
        positions.update(currency=6,calc_bars_count=7)
        signature,extended=binder.catalog.counted_security
    if currency_slot:
        positions["currency"]=6
        if counted and converted:
            extended=binder.catalog.contracts[extended.overload_id+".fx"]
        elif not counted:
            signature,extended=binder.catalog.currency_security
    arguments=[];positional=0
    for argument in call.arguments:
        index=positions.get(argument.name,-1) if argument.name is not None else positional
        if argument.name is None:positional+=1
        expression=argument.expression
        if counted and index==7:
            from packages.contracts.lower_tf import MAX_COUNT
            if not isinstance(expression,parser.Literal) or type(expression.value) is not int or not 1<=expression.value<=MAX_COUNT:
                binder.fail("PROFILE_UNCOVERED","REQUEST_COUNT","Use a positive literal calc_bars_count up to 10000000.",argument.span)
        elif currency_slot and index==6:
            expression=normalize_currency(binder,expression,argument.span)
        elif optional and index == 5:
            if not isinstance(expression,parser.Literal) or type(expression.value) is not bool:
                binder.fail("PROFILE_UNCOVERED","REQUEST_FLAG","ignore_invalid_symbol requires a literal bool.",argument.span)
        elif index>=3:
            family="gaps" if index==3 else "lookahead"
            if binder.catalog.language_version>=3 and isinstance(expression,parser.Reference) and expression.name in (f"barmerge.{family}_off",f"barmerge.{family}_on"):
                on=expression.name.endswith("_on")
                expression=parser.Literal(on if policy["flags"]=="bool" else "on" if on else "off",expression.span)
            elif policy["flags"]!="bool" or not isinstance(expression,parser.Literal) or type(expression.value) is not bool:
                binder.fail("PROFILE_UNCOVERED","REQUEST_FLAG","Use the version-specific barmerge constant (legacy versions also accept bool).",argument.span)
        arguments.append(replace(argument,expression=expression))
    call=replace(call,arguments=tuple(arguments))
    expressions=tuple(binder.expression(a.expression) for a in call.arguments)
    base = contract
    contract=select_contract(call,expressions,contract,binder.catalog.language_version,binder)
    if optional:
        contract = optional_target(binder,base,contract,extended,call.span)
    return binder.call(call,expressions,signature,contract,top_level)

def normalize_currency(binder,expression,span):
    from packages.contracts.fx import code
    if isinstance(expression,parser.Reference) and expression.name=="syminfo.currency":
        expression=parser.Literal("@chart",expression.span)
    elif isinstance(expression,parser.Reference) and expression.name.startswith("currency."):
        entries=binder.catalog.snapshot.references[binder.catalog.language_version]["entries"]
        if not any(row["name"]==expression.name and row["category"]=="constants" for row in entries):
            binder.fail("PROFILE_UNCOVERED","REQUEST_CURRENCY","Currency constant is unavailable in this version.",span)
        expression=parser.Literal("" if expression.name=="currency.NONE" else expression.name[9:],expression.span)
    elif not isinstance(expression,parser.Literal) or not (expression.value=="" or code(expression.value)):
        binder.fail("PROFILE_UNCOVERED","REQUEST_CURRENCY","Expected a fixed currency or syminfo.currency.",span)
    return expression


def select_contract(call,expressions,contract,version,binder):
    from .dynamic_requests import check_target_mode
    check_target_mode(binder, call, expressions)
    from packages.contracts.objects import uses_objects
    from packages.contracts.labels import uses_labels
    if uses_labels(expressions):
        binder.fail('PROFILE_UNCOVERED', 'LABEL_REQUEST', 'Label values and effects are outside request targets.', call.span)
    from packages.contracts.lines import uses_lines
    if uses_lines(expressions):
        binder.fail('PROFILE_UNCOVERED', 'LINE_REQUEST', 'Line values and effects are outside request targets.', call.span)
    from packages.contracts.boxes import uses_boxes
    if uses_boxes(expressions):
        binder.fail('PROFILE_UNCOVERED', 'BOX_REQUEST', 'Box values and effects are outside request targets.', call.span)
    from packages.contracts.tables import uses_tables
    if uses_tables(expressions):
        binder.fail('PROFILE_UNCOVERED', 'TABLE_REQUEST', 'Table values and effects are outside request targets.', call.span)
    from packages.contracts.linefills import uses_linefills
    if uses_linefills(expressions):
        binder.fail('PROFILE_UNCOVERED', 'LINEFILL_REQUEST', 'Linefill values and effects are outside request targets.', call.span)
    if uses_objects(expressions):binder.fail('PROFILE_UNCOVERED','UDT_REQUEST','Object-dependent request expressions and targets are outside the flat object profile.',call.span)
    if version >= 5 and any(e.value_type == ValueType('string','series') for i,(arg,e) in
                            enumerate(zip(call.arguments,expressions,strict=True))
                            if arg.name in ('symbol','timeframe') or arg.name is None and i < 2):
        from packages.contracts.dynamic_requests import SUFFIX as dynamic_suffix
        return binder.catalog.contracts[contract.overload_id + dynamic_suffix]
    from .binder import BoundCall, BoundLiteral
    from packages.contracts.heikin_ashi import OPERATION, SUFFIX, public_name

    def ticker_alias(node):
        seen = set()
        while isinstance(node, parser.Reference):
            owner = binder.resolver.references.get(id(node))
            if owner is None or id(owner) in seen or len(seen) > 128:
                return False
            seen.add(id(owner))
            node = owner.declaration.expression
        return node.name if isinstance(node, parser.Call) else None

    symbol_parameter = request_policy(version)['parameters'][0]
    for i, (argument, expression) in enumerate(zip(call.arguments, expressions, strict=True)):
        if argument.name == symbol_parameter or (i == 0 and argument.name is None):
            from packages.contracts.tickers import names, SUFFIX as fixed_suffix
            constructor = ticker_alias(argument.expression)
            if constructor in names(version):
                return binder.catalog.contracts[contract.overload_id + fixed_suffix]
            if (isinstance(expression, BoundCall) and expression.contract.operation == OPERATION
                    or isinstance(argument.expression, parser.Reference) and constructor and constructor == public_name(version)):
                return binder.catalog.contracts[contract.overload_id + SUFFIX]
    position=0;nonliteral=False;computed=False
    from dataclasses import fields,is_dataclass
    from packages.contracts.primary_targets import TARGET_SLOTS
    # New method syntax is resolved already. Use the existing widest closed
    # helper evaluator, including its unchanged purity and expansion budgets.
    from .binder import BoundUserCall
    def method_feature(node,seen=None):
        seen=set() if seen is None else seen
        if id(node) in seen:return False
        seen.add(id(node))
        if isinstance(node,BoundUserCall) and isinstance(binder.shared['definitions'].get(node.function_id),parser.Method):return True
        if isinstance(node,parser.MemberCall) and id(node) in binder.resolver.method_calls:return True
        if isinstance(node,parser.Reference):
            owner=binder.resolver.references.get(id(node))
            if owner is not None and method_feature(owner.declaration.expression,seen):return True
        if is_dataclass(node):return any(method_feature(getattr(node,f.name),seen) for f in fields(node) if f.name!='span')
        return type(node) is tuple and any(method_feature(n,seen) for n in node)
    for i,(arg,expression) in enumerate(zip(call.arguments,expressions,strict=True)):
        target=arg.name in ('symbol','timeframe','resolution') if arg.name is not None else i<2
        if target and (method_feature(arg.expression) or method_feature(expression)):
            from packages.contracts.numeric_targets import contract as numeric_contract
            return numeric_contract(contract,version)
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
        from packages.contracts.numeric_functions import registered_names
        if isinstance(node,parser.Call):
            if node.name in registered_names(version)|({"abs","round","min","max"} if version<5 else {"math.abs","math.round","math.min","math.max"})|{"int","float","na","nz"}:return True
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
            return not empty and node.name in binder.catalog.references and binder.catalog.references[node.name].slot in TARGET_SLOTS
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
