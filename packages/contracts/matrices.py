"""Closed rectangular collections on the shared reference heap."""
from dataclasses import fields,is_dataclass
from .model import ValueType
from .wire import read_json,digest

PROFILE='openpine.matrices.1'
OPERATIONS=('new','get','set','fill','rows','columns','elements_count','copy','row','col','add_row','add_col','remove_row','remove_col','swap_rows','swap_columns','reshape','transpose','is_na')
MAX_DIMENSION=100000

def element(base):return base[7:] if type(base) is str and base.startswith('matrix.') else None
def base(element):return 'matrix.'+element
def is_matrix(value):
    t=element(value)
    if t is None:return False
    from .arrays import ELEMENTS
    from .objects import is_object
    from .enums import is_enum
    return t in (*ELEMENTS,'color') or is_object(t) or is_enum(t)

def uses_matrices(value):
    from .model import ObjectFieldContract
    if isinstance(value,ObjectFieldContract) and (is_matrix(value.base)):return True
    from .model import MatrixIR,ParameterContract
    if isinstance(value,ParameterContract) and (is_matrix(value.annotation) or type(value.annotation) is str and value.annotation.startswith('matrix<')):return True
    if isinstance(value,MatrixIR) or isinstance(value,ValueType) and is_matrix(value.base):return True
    if is_dataclass(value):return any(uses_matrices(getattr(value,f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_matrices,value))

def signature(op):
    """Parameter name, type family and closed default (None means required)."""
    if op=='new':return (('rows','int','zero'),('columns','int','zero'),('initial_value','element','missing'))
    first=(('id','matrix',None),)
    rest={'get':(('row','int',None),('column','int',None)),
        'set':(('row','int',None),('column','int',None),('value','element',None)),
        'fill':(('value','element',None),('from_row','int','zero'),('to_row','int','rows'),('from_column','int','zero'),('to_column','int','columns')),
        'row':(('row','int',None),),'col':(('column','int',None),),
        'add_row':(('row','int','rows'),('array_id','array','array_missing')),
        'add_col':(('column','int','columns'),('array_id','array','array_missing')),
        'remove_row':(('row','int','last_row'),),'remove_col':(('column','int','last_column'),),
        'swap_rows':(('row1','int',None),('row2','int',None)),
        'swap_columns':(('column1','int',None),('column2','int',None)),
        'reshape':(('rows','int',None),('columns','int',None))}
    return first+rest.get(op,())

def argument_type(kind,t):return base(t) if kind=='matrix' else 'array.'+t if kind=='array' else t if kind=='element' else 'int'
def result_type(op,t):
    result=base(t) if op in ('new','copy','transpose') else t if op=='get' else 'array.'+t if op in ('row','col','remove_row','remove_col') else 'int' if op in ('rows','columns','elements_count') else 'bool' if op=='is_na' else 'void'
    return ValueType(result,'const' if result=='void' else 'series')

def validate(node,version,nominal):
    from .model import NAIR,LiteralIR
    from .objects import is_object
    from .enums import is_enum
    if version<5 or node.operation not in OPERATIONS or not is_matrix(base(node.element)):raise ValueError('Unavailable matrix operation/type/version.')
    if (is_object(node.element) or is_enum(node.element)) and node.element not in nominal:raise ValueError('Foreign matrix nominal type.')
    specs=signature(node.operation)
    if len(node.arguments)!=len(specs):raise ValueError('Matrix arity differs.')
    source=[];defaults=[]
    for i,(a,(name,kind,default)) in enumerate(zip(node.arguments,specs,strict=True)):
        if a.parameter_id!=name or a.expression.value_type.base!=argument_type(kind,node.element):raise ValueError('Matrix parameter/type differs.')
        if a.origin=='source' and type(a.source_index) is int:source.append((a.source_index,i))
        elif a.origin=='default' and a.source_index is None and default is not None:
            e=a.expression
            if default=='zero':valid=type(e) is LiteralIR and type(e.value) is int and e.value==0 and e.value_type==ValueType('int')
            elif default=='missing' and node.element=='bool' and version==6:valid=type(e) is LiteralIR and e.value is False and e.value_type==ValueType('bool')
            else:valid=type(e) is NAIR
            if not valid:raise ValueError('Forged matrix default.')
            defaults.append(i)
        else:raise ValueError('Matrix argument provenance differs.')
    if sorted(i for i,_ in source)!=list(range(len(source))) or node.evaluation_order!=tuple(i for _,i in sorted(source))+tuple(defaults):raise ValueError('Matrix argument order differs.')
    if node.value_type!=result_type(node.operation,node.element):raise ValueError('Matrix result type differs.')

def policy(v):
    return {'profile_id':PROFILE if v>=5 else None,'available':v>=5,'language_version':v,'elements':['int','float','bool','string','enum','flat_udt'],
        'operations':list(OPERATIONS) if v>=5 else [],'methods':v>=5,'max_elements':100000,'max_dimension':MAX_DIMENSION,
        'indices':'defined_nonnegative_in_both_versions','empty_shape':'zero_dimensions_retained;only_0x0_infers_opposite_dimension_on_insert',
        'defaults':'new_0_0_typed_missing;bool_v6_false;insert_at_end;remove_last;fill_full_extent;omitted_array_fills_missing',
        'copies':'independent_shape_and_slots;shallow_flat_udt_aliases','iteration':'row_count_and_shape_at_entry;independent_row_copy_read_before_iteration;shape_change_fails_next_iteration',
        'storage':'shared_heap;row_major_dense_buffer;matrix_to_flat_record_edges;no_cycles',
        'mutation':'full_buffer_staging;remove_and_returned_array_preflight_together;atomic_publication;existing_limits',
        'checkpoint':'typed-ir.18;runtime-state.11;array-heap.4;packed_udt_ids;legacy_programs_unchanged',
        'excluded':['color','graphics','nested_collections','reference_udt_fields','matrix_requests','matrix_tuple_results','linear_algebra'],
        'external_execution_verified':False,'limits_increased':False}

def admit(root,snapshot,v):
    value=read_json(root/'spec/matrices-profile.json')
    if value.get('schema_version')!='openpine.matrices-spec.1' or value['source_lock_sha256']!=digest(snapshot.lock) or value['versions'][str(v)]!={'policy':policy(v),'reference_sha256':digest(snapshot.references[v])}:raise ValueError('Matrix profile differs from pinned evidence.')
