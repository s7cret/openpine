"""Closed header matching; selection never inspects or evaluates a function body."""
from dataclasses import dataclass
from .wire import read_json,digest


def base(annotation):
    return 'array.'+annotation[6:-1] if annotation and annotation.startswith('array<') else annotation


@dataclass(frozen=True)
class Header:
    function_id: str
    names: tuple[str,...]
    types: tuple[str|None,...]
    qualifiers: tuple[str|None,...]
    required: tuple[bool,...]


def signature(header, version):
    positions=tuple(range(len(header.names))) if version==5 else tuple(i for i,r in enumerate(header.required) if r)
    return tuple((header.types[i], 'series' if (header.types[i] or '').startswith('array.') else header.qualifiers[i] or 'series') for i in positions)


def validate_headers(headers, version):
    if version<5:raise ValueError('FUNCTION_OVERLOAD_VERSION')
    for index,left in enumerate(headers):
        if (any(len(x)!=len(left.names) for x in (left.required,left.types,left.qualifiers)) or any(type(r) is not bool for r in left.required)
                or any(q not in (None,'simple','series') for q in left.qualifiers)):
            raise ValueError('FUNCTION_OVERLOAD_TYPE')
        if any((t or '').startswith('array.') and q=='simple' for t,q in zip(left.types,left.qualifiers)):
            raise ValueError('FUNCTION_OVERLOAD_TYPE')
        for right in headers[:index]:
            a,b=signature(left,version),signature(right,version)
            if len(a)!=len(b):continue
            if any(t is None for t,q in (*a,*b)):
                raise ValueError('FUNCTION_OVERLOAD_TYPE')
            if a==b:raise ValueError('FUNCTION_OVERLOAD_DUPLICATE')


def bind_positions(header, names):
    supplied={};named=False;positions={name:i for i,name in enumerate(header.names)}
    for index,name in enumerate(names):
        if name is not None:
            named=True
            if name not in positions:return None,'UNKNOWN_ARGUMENT'
            position=positions[name]
        else:
            if named:return None,'POSITIONAL_AFTER_NAMED'
            position=index
        if position>=len(header.names):return None,'TOO_MANY_ARGUMENTS'
        if position in supplied:return None,'DUPLICATE_ARGUMENT'
        supplied[position]=index
    if any(required and i not in supplied for i,required in enumerate(header.required)):
        return None,'MISSING_ARGUMENT'
    return supplied,None


def choose(headers, names, types, version=6):
    candidates=[];errors=[]
    for h in headers:
        supplied,error=bind_positions(h,names)
        if error:errors.append(error);continue
        cost=0
        for position,index in supplied.items():
            actual=types[index];expected=h.types[position];q=h.qualifiers[position]
            if actual is None:cost=None;break
            if actual.base in ('void','tuple') or actual.base=='na' and expected is None:break
            if expected is None or expected==actual.base:pass
            elif expected=='float' and actual.base=='int':cost+=1
            elif actual.base=='na' and (expected in ('int','float') or (expected or '').startswith('array.') or expected=='bool' and version<6):pass
            else:break
            if q=='simple' and actual.qualifier=='series':break
        else:
            candidates.append((cost,h,supplied));continue
        # Unknown abstract types are used only by the conservative static graph.
        if cost is None:continue
    if not candidates:
        if errors and len(errors)==len(headers) and len(set(errors))==1:raise ValueError(errors[0])
        raise ValueError('FUNCTION_OVERLOAD_NO_MATCH')
    best=min(row[0] for row in candidates);matches=[row for row in candidates if row[0]==best]
    if len(matches)!=1:raise ValueError('FUNCTION_OVERLOAD_AMBIGUOUS')
    return matches[0][1],matches[0][2]


def policy(version):
    return {'language_version':version,'available':version>=5,
        'definition_signature':'all_parameters' if version==5 else 'required_parameters',
        'same_signature_arity':'explicit_types_required','optional_parameters_distinguish':version==5,
        'selection':'shape_then_exact_types_then_int_to_float_ambiguous_ties',
        'qualifiers':'existing_simple_series_admission_no_qualifier_tie_break',
        'same_name_calls':'earlier_overloads_or_self_for_recursion_diagnostic',
        'state':'written_call_chain_selected_definition','max_definitions':64,'max_group':16,
        'unused_bodies':'structural_checks_and_definite_edges_type_dependent_edges_at_specialization',
        'external_execution_verified':False}


def admit(root,snapshot,version):
    value=read_json(root/'spec/function-overloads-profile.json')
    if (value.get('schema_version')!='openpine.function-overloads-spec.1'
            or value['source_lock_sha256']!=digest(snapshot.lock)
            or value['versions'][str(version)]!={'policy':policy(version),'reference_sha256':digest(snapshot.references[version])}):
        raise ValueError('Function overload profile differs from pinned evidence.')


def uses_overloads(program):
    from .model import OverloadedFunctionContract
    return any(isinstance(f,OverloadedFunctionContract) for f in program.functions)


def execution_schema(program):
    if program.schema_version not in ('openpine.typed-ir.12','openpine.typed-ir.13'):return program.schema_version
    from .prepared_requests import base_schema
    return base_schema(program)


def seal_schema(program):
    from .methods import uses_methods
    if uses_methods(program):
        from dataclasses import replace
        return replace(program,schema_version='openpine.typed-ir.13')
    if uses_overloads(program):
        from dataclasses import replace
        return replace(program,schema_version='openpine.typed-ir.12')
    return program
