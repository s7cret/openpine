"""Closed method headers. Runtime executes the selected ordinary IR operations."""
from .overloads import Header,choose,bind_positions,validate_headers
from .wire import digest,read_json
from .arrays import ARRAY_BASES,OPERATIONS,parameters,is_array
from .objects import is_object

RECEIVERS=('int','float','bool','string',*ARRAY_BASES)
INSTANCE_OPERATIONS=tuple(op for op in OPERATIONS if op not in ('new','from','is_na'))


def key(name):return 'method:'+name


def validate(headers,version):
    if version<5:raise ValueError('METHOD_VERSION')
    for h in headers:
        if not h.names or not h.types or len(h.required)!=len(h.names) or not h.required[0] or h.types[0] not in RECEIVERS and not is_object(h.types[0]) and not is_array(h.types[0]):
            raise ValueError('METHOD_RECEIVER')
    try:validate_headers(headers,version)
    except ValueError as e:raise ValueError(str(e).replace('FUNCTION_OVERLOAD','METHOD_OVERLOAD')) from e


def builtin(member,receiver):
    if is_object(receiver) and member=='copy':return Header('builtin.udt.copy',('id',),(receiver,),(None,),(True,))
    if not is_array(receiver) or member not in INSTANCE_OPERATIONS:return None
    names=parameters(member);element=receiver.removeprefix('array.')
    return Header('builtin.array.'+member,names,tuple(receiver if i==0 else 'int' if n=='index' else element for i,n in enumerate(names)),(None,)*len(names),(True,)*len(names))


def resolve(headers,builtin_header,names,types,version):
    # Receiver dispatch uses its underlying type, never a numeric conversion.
    headers=tuple(h for h in headers if h.types[0]==types[0].base)
    all_headers=(*headers,*((builtin_header,) if builtin_header else ()))
    rows=[]
    for h in all_headers:
        try:_,supplied=choose((h,),names,types,version)
        except ValueError:continue
        cost=sum(types[i].base=='int' and h.types[p]=='float' for p,i in supplied.items())
        rows.append((cost,h))
    if not rows:
        try:choose(all_headers,names,types,version)
        except ValueError as e:raise ValueError(str(e).replace('FUNCTION_OVERLOAD','METHOD')) from e
        raise ValueError('METHOD_NO_MATCH')
    best=min(c for c,h in rows);matches=[h for c,h in rows if c==best]
    users=[h for h in matches if not h.function_id.startswith('builtin.')]
    if len(users)>1:raise ValueError('METHOD_AMBIGUOUS')
    return users[0] if users else matches[0]


def policy(v):
    return {'language_version':v,'available':v>=5,'receivers':list(RECEIVERS),
        'receiver':'explicit_typed_required_first_parameter_exact_underlying_type',
        'syntax':'dot_calls_including_history_and_call_receivers_and_chains',
        'namespaces':'legacy_qualified_namespaces_preserved_parenthesized_receiver_disambiguates',
        'user_calls':'dot_only','overloads':'existing_versioned_header_policy',
        'selection':'minimum_numeric_widenings_user_overrides_builtin_tie_ambiguous_user_ties_fail',
        'evaluation':'receiver_once_then_explicit_arguments_in_source_order_then_closed_defaults',
        'state':'existing_written_call_chain','builtins':list(INSTANCE_OPERATIONS),
        'frozen_targets':'existing_numeric_scalar_function_profile_unchanged_purity_budgets',
        'unused_bodies':'structural_checks_definite_edges_then_existing_call_specialization',
        'external_execution_verified':False}


def admit(root,snapshot,version):
    v=read_json(root/'spec/methods-profile.json')
    if (v.get('schema_version')!='openpine.methods-spec.1' or v['source_lock_sha256']!=digest(snapshot.lock)
        or v['versions'][str(version)]!={'policy':policy(version),'reference_sha256':digest(snapshot.references[version])}):
        raise ValueError('Method profile differs from pinned evidence.')


def uses_methods(program):
    from .model import MethodContract
    return any(isinstance(f,MethodContract) for f in program.functions)
