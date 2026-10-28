"""Commit one selected calculation branch without executing Pine again."""
from packages.runtime.rollback import RingOverlay,AppendOverlay
from packages.runtime.series.technical import CellOverlay,TECHNICAL_TYPES
from packages.runtime.series.composite import COMPOSITE_TYPES
from packages.runtime.history import Ring

def materialize(value,budget,heap):
    if type(value) is CellOverlay:
        result=value.base
        for index,item in value.changed.items():result[index]=item
        return result
    if type(value) is AppendOverlay:
        result=value.base if type(value.base) is list else list(value.base)
        result.extend(value.tail);return result
    if type(value) is RingOverlay:
        result=value.base
        for index,item in sorted(value.changed.items()):
            if index==len(result.items):result.items.append(item)
            else:result.items[index]=item
        result.count=value.count;result.budget=budget;result.string_bytes=value.string_bytes
        if result.kind=='string' and result.payload_label is None:result.payload_label=result.label+' string payload'
        if hasattr(result,'heap'):result.heap=heap
        return result
    if isinstance(value,Ring):
        value.items=materialize(value.items,budget,heap);value.budget=budget
        return value
    return value

def kernel(state,budget,heap):
    for name,value in vars(state).items():
        if type(value) in (CellOverlay,AppendOverlay,RingOverlay) or isinstance(value,Ring):setattr(state,name,materialize(value,budget,heap))
        elif name in ('rings','prefix'):
            setattr(state,name,[materialize(item,budget,heap) for item in value])
        elif name=='parts':
            for part in value:kernel(part,budget,heap)
        elif type(value) in (*TECHNICAL_TYPES,*COMPOSITE_TYPES):kernel(value,budget,heap)
    return state

def commit(evaluation,candidate):
    if candidate.count!=evaluation.count or not candidate.at_boundary or not candidate.deferred_commit:raise ValueError('Invalid selected calculation branch.')
    for state in candidate.states.values():kernel(state,candidate.budget,candidate.heap)
    # Materialized confirmed buffers keep the old owner charge. Changed-cell
    # dictionaries have ended their lifetime; detached resize debt becomes
    # redundant once the original branch is replaced.
    if candidate.budget.allocation:
        evaluation.budget.allocation.transfer(candidate.budget.allocation)
        candidate.budget.allocation.discard_resize_debt()
        # Seed samples borrowed by a preview may have ended their lifetime in
        # that branch. Reconcile after ownership transfer, never on rollback.
        candidate.budget.temporary.close()
        from packages.runtime.history_memory import Allocation
        candidate.budget.temporary=Allocation(candidate.budget.allocation.account)
        def reconcile(value):
            if isinstance(value,Ring):value.reconcile()
            elif hasattr(value,'__dict__'):
                if hasattr(value,'reconcile'):value.reconcile()
                for name,child in vars(value).items():
                    if name in ('parts','rings','prefix'):
                        for item in child:reconcile(item)
                    elif isinstance(child,Ring) or type(child) in TECHNICAL_TYPES+COMPOSITE_TYPES:reconcile(child)
        for state in candidate.states.values():reconcile(state)
    candidate.history.buffers=evaluation.history.buffers
    for ring in candidate.history.buffers.values():
        ring.budget=candidate.budget
        if hasattr(ring,'heap'):ring.heap=candidate.heap
    for name in ('budget','work','heap','history','persistent','states','values','pending_outputs','frame','context_id','first_time','last_time','request_values','strategy_values','environment_phase'):
        if hasattr(candidate,name):setattr(evaluation,name,getattr(candidate,name))
    if evaluation.output_sink is None:evaluation.times.append(evaluation.frame.time)
    evaluation.at_boundary=False
    try:evaluation._finish_step(evaluation.frame);evaluation.at_boundary=True
    except BaseException as error:
        evaluation.closed=True
        from packages.runtime.series import KernelError
        from packages.contracts import Diagnostic,OpenPineError
        if isinstance(error,KernelError):raise OpenPineError(Diagnostic('RUNTIME_ERROR',error.code,str(error),rule_id=error.rule_id,
            language_version=evaluation.program.identity.language_version)) from error
        raise
