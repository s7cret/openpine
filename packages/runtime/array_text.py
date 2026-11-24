"""String/array conversions on the existing per-bar scalar and array budgets."""
from packages.contracts.scalar_functions import MAX_STRING, ScalarError, invoke


def split(heap, evaluation, node, source, separator):
    if source is None or separator is None:
        return None
    if max(len(source),len(separator))>MAX_STRING:
        heap.error('SCALAR_LIMIT','Split input exceeds the scalar string limit.',node)
    try:
        evaluation.scalar_budget.charge(1+len(source)+len(separator))
    except ScalarError as error:
        heap.error('SCALAR_LIMIT',str(error),node)
    matches=source.count(separator) if separator else 0
    count=matches+1 if separator else len(source)
    size=len(source.encode('utf-8'))-matches*len(separator.encode('utf-8'))
    heap.capacity(len(heap.objects)+1,heap.cells+count,count,heap.bytes+size,node)
    heap.charge(count,evaluation,node)
    items=source.split(separator) if separator else list(source)
    return heap.create('string',items,size,evaluation,node)


def join(heap, evaluation, node, reference, separator):
    items=heap.object(reference,node).items
    if separator is None:return None
    if len(separator)>MAX_STRING:
        heap.error('SCALAR_LIMIT','Join separator exceeds the scalar string limit.',node)
    budget=evaluation.scalar_budget
    parts=[]
    size=len(separator)*max(0,len(items)-1)
    try:
        budget.output(size)
        for value in items:
            heap.charge(1,evaluation,node)
            if value is None:return None
            text=value if node.element=='string' else invoke('scalar.tostring',(value,),budget,heap.version)
            size+=len(text)
            if size>MAX_STRING:raise ScalarError('Join exceeds the scalar string limit.')
            budget.output(len(text))
            if text or separator:parts.append(text)
    except ScalarError as error:
        heap.error('SCALAR_LIMIT',str(error),node)
    return separator.join(parts)
