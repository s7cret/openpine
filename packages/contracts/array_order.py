"""Versioned ordering/search ABI over the existing array and flat-record heap."""
from .model import ValueType,LiteralIR
from .objects import is_object
from .wire import digest,read_json

PROFILE='openpine.array-order.1'
SORT=('sort','sort_indices')
SEARCH=('binary_search','binary_search_leftmost','binary_search_rightmost')
OPERATIONS=(*SORT,*SEARCH)
RANK=('min','max')
ORDER='sort_order'

def order_type(v):return 'bool' if v==4 else ORDER
def order_value(v,ascending=True):return ascending if v==4 else 'ascending' if ascending else 'descending'
def valid_order(value):return type(value) is str and value in ('ascending','descending')
def available(op,v,element,arity=0):
    if v<4 or op!='sort' and v<5:return False
    if op in RANK:return element in ('int','float')
    if is_object(element):return v==6
    return element in (('int','float','string') if v>=5 and op in SORT else ('int','float'))

def parameters(op,v,element,arity=0):
    if op in RANK:return ('id','nth') if arity==2 else ('id',)
    names=('id','order') if op in SORT else ('id','val')
    return (*names,'sort_field') if is_object(element) and v==6 else names

def result_type(op,element):return ValueType('void') if op=='sort' else ValueType('array.int' if op=='sort_indices' else element if op in RANK else 'int','series')

def field_layout(element,selector,object_types):
    if type(selector) not in (int,str):raise ValueError('Sort field must be a const integer index or string name.')
    layout=next((t for t in object_types if getattr(t,'type_id',None)==element),None)
    if layout is None:raise ValueError('Unknown UDT sorting layout.')
    index=selector if type(selector) is int else next((i for i,f in enumerate(layout.fields) if f.name==selector),-1)
    if not 0<=index<len(layout.fields):raise ValueError('Sort field is outside the UDT layout.')
    base=layout.fields[index].base
    if base not in ('int','float','string'):raise ValueError('Sort fields require int, float or string.')
    return index,base

def argument_types(op,v,element,arity,selector=None,object_types=()):
    key=field_layout(element,selector,object_types)[1] if is_object(element) else element
    result=('array.'+element,order_type(v) if op in SORT else 'int' if op in RANK else key)
    if op in RANK and arity==1:return result[:1]
    return (*result,'int' if type(selector) is int else 'string') if is_object(element) else result

def optional(op,index):return op in SORT and index==1 or op in (*SORT,*SEARCH) and index==2

def valid_default(op,index,value,v):
    return type(value) is LiteralIR and value.value_type.qualifier=='const' and (
        op in SORT and index==1 and value.value_type.base==order_type(v) and type(value.value) is type(order_value(v)) and value.value==order_value(v)
        or index==2 and op in (*SORT,*SEARCH) and value.value_type==ValueType('int') and type(value.value) is int and value.value==0)

def validate(node,v,object_types=()):
    op,element,args=node.operation,node.element,node.arguments
    if not available(op,v,element,len(args)):raise ValueError('Ordering operation/version/element is unavailable.')
    names=parameters(op,v,element,len(args))
    if len(names)!=len(args):raise ValueError('Ordering arity differs.')
    selector=None
    if is_object(element):
        field=args[2].expression
        if type(field) is not LiteralIR or field.value_type.qualifier!='const' or field.value_type.base not in ('int','string'):raise ValueError('Sort field must be a closed const selector.')
        selector=field.value
    expected=argument_types(op,v,element,len(args),selector,object_types)
    if op in SEARCH and expected[1] in ('int','float') and args[1].expression.value_type.base in ('int','float'):
        expected=(expected[0],args[1].expression.value_type.base,*expected[2:])
    if tuple(a.parameter_id for a in args)!=names or tuple(a.expression.value_type.base for a in args)!=expected:raise ValueError('Ordering arguments differ from the typed ABI.')
    if any(a.origin not in ('source','default') or a.origin=='default' and a.source_index is not None for a in args):raise ValueError('Invalid ordering argument origin.')
    indices=[a.source_index for a in args if a.origin=='source']
    if any(type(i) is not int for i in indices) or sorted(indices)!=list(range(len(indices))):raise ValueError('Invalid ordering source argument indices.')
    order=tuple(sorted(range(len(args)),key=lambda i:(args[i].origin=='default',args[i].source_index if args[i].source_index is not None else i)))
    if any(type(i) is not int for i in node.evaluation_order) or node.evaluation_order!=order:raise ValueError('Invalid ordering argument evaluation order.')
    if any(a.origin=='default' and not valid_default(op,i,a.expression,v) for i,a in enumerate(args)):raise ValueError('Ordering default differs.')
    if node.value_type!=result_type(op,element):raise ValueError('Ordering result type differs.')

def policy(v):
    return {'profile_id':PROFILE if v>=4 else None,'language_version':v,'sort':v>=4,'sort_indices':v>=5,'binary_search':v>=5,'rank_argument':v>=5,
        'order_type':order_type(v) if v>=4 else None,'udt_sort_field':v==6,'methods':v>=5,
        'primitive_sort_elements':['int','float']+(['string'] if v>=5 else []),'binary_primitive_elements':['int','float'],
        'udt_field_types':['int','float','string'] if v==6 else [],'selector':'closed_const_int_index_or_string_name;default_0',
        'sorting':'stable;ascending_default;missing_and_empty_strings_last_ascending_first_descending;udt_missing_ids_error',
        'search_precondition':'ascending_defined_keys;caller_responsibility;no_implicit_sort_or_linear_validation;missing_probe_error',
        'binary_search':'middle_match;otherwise_-1','leftmost':'first_equal;otherwise_max_0_predecessor',
        'rightmost':'last_equal;otherwise_successor_insertion_index_may_equal_size','empty_search':-1,'missing_search_target':'runtime_error',
        'nth':'zero_based;duplicates_count;skip_na;empty_all_na_return_na;invalid_or_unavailable_rank_error',
        'work':'linear_staging_and_actual_comparisons;string_comparisons_charge_length;probe_search_logarithmic',
        'ownership':'existing_heap;transactional_buffer_publication;separate_int_array_for_indices',
        'limits_increased':False,'external_execution_verified':False,'excluded':['slice','nested_collections','new_request_expression_types','sort_order_arrays_or_object_fields']}

def admit(root,snapshot,v):
    value=read_json(root/'spec/array-order-profile.json')
    if (value.get('schema_version')!='openpine.array-order-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock)
        or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}):raise ValueError('Array ordering profile differs from pinned evidence.')
