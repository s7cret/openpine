"""Admit typed IR once, then evaluate frames supplied by execution."""

from __future__ import annotations
from packages.contracts.trading import STRATEGY_DECLARATIONS

import hashlib
import json
import math
from dataclasses import asdict

from packages.contracts import (
    BarFrame, CallIR, ConvertIR, Diagnostic, HISTORICAL_PROFILE_ID, IndicatorDeclaration,
    LITERAL_PROFILE_ID, LiteralIR, OpenPineError, PlotOutput, ProgramIR, ReferenceIR,
    RunResult, UnaryIR, ValueType, BinaryIR, ConditionalIR, IfIR, LoadIR, NAIR, StoreIR, HistoryIR,
    BlockIR, EvaluateIR, UserCallIR,
)
from packages.contracts import InputIR
from packages.contracts import TupleIR,TupleType,UnpackIR,ForIR,WhileIR,LoopControlIR,ArrayIR,ForInIR
from packages.contracts.arrays import ARRAY_BASES, uses_arrays,is_array
from packages.runtime.arrays import ArrayHeap, ArrayRef,ObjectRef,RecordObject
from packages.contracts.objects import is_object,uses_objects,is_reference
from packages.contracts import ObjectIR
from packages.contracts import EnumIR
from packages.contracts.enums import is_enum,valid_value as enum_value_matches
from packages.contracts.loops import uses_loops
from packages.runtime.loops import WorkBudget, LoopTransfer, evaluate_loop
from packages.contracts.inputs import admitted_settings,configured_result
from packages.runtime.admission import admit_program
from packages.runtime.operations import KERNELS, PlotEvent
from packages.runtime.series import KernelError
from packages.runtime.state import KernelContext
from packages.runtime.history import HistoryStore, StorageBudget


def program_hash(program: ProgramIR) -> str:
    raw = json.dumps(asdict(program), ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def fail(code: str, message: str, node=None, category="ARTIFACT_INCOMPATIBLE"):
    raise OpenPineError(Diagnostic(category, code, message, getattr(node, "span", None)))


def value_matches(value, value_type: ValueType, version=6, *, missing_string=False) -> bool:
    if isinstance(value_type,TupleType):
        return type(value) is tuple and len(value)==len(value_type.elements) and all(value_matches(v,t,version,missing_string=missing_string) for v,t in zip(value,value_type.elements,strict=True))
    if value_type.qualifier not in ("const", "input", "simple", "series"):
        return False
    if is_enum(value_type.base):return enum_value_matches(value,value_type.base)
    if value is None:
        return is_reference(value_type.base) or value_type.base in ("int", "float") or (missing_string and value_type.base == 'string') or (value_type.base == "bool" and version < 6)
    if is_object(value_type.base):return type(value) is ObjectRef and value.type_id==value_type.base
    if is_array(value_type.base):
        return type(value) is ArrayRef and value.element == value_type.base.removeprefix('array.')
    expected = {"int": int, "float": float, "bool": bool, "string": str}.get(value_type.base)
    return type(value) is expected and (type(value) is not float or math.isfinite(value)) and (
        type(value) is not int or -(2**63) <= value <= 2**63 - 1
    ) and (
        type(value) is not str or len(value) <= 65536
    )


class Runtime:
    def __init__(self, identity, contracts, references=(), *, history_account=None):
        self.identity = identity
        self.contracts = {contract.overload_id: contract for contract in contracts}
        self.references = {contract.symbol_id: contract for contract in references}
        self.history_account=history_account
        self.observer=None

    def admit(self, program: ProgramIR) -> None:
        admit_program(self, program, fail, lambda v, t: value_matches(v, t, self.identity.language_version))

    def run(self, program: ProgramIR,*,settings=None) -> RunResult:
        if program.identity.implementation_profile_id != LITERAL_PROFILE_ID:
            fail("BARS_REQUIRED", "Historical execution requires confirmed bar frames.", category="INPUT_UNAVAILABLE")
        evaluation = self.prepare(program,settings=settings)
        evaluation.step()
        return configured_result(evaluation.finish(),evaluation.input_settings)

    def prepare(self, program: ProgramIR, *, output_sink=None,settings=None):
        # Validate the entire program before evaluating any effectful operation.
        self.admit(program)
        result=Evaluation(program, output_sink=output_sink,settings=admitted_settings(program,settings),history_account=self.history_account)
        result.observe(self.observer)
        return result

    def prepare_expression(self,program,sink,*,settings=None):
        admit_program(self,program,fail,lambda v,t:value_matches(v,t,self.identity.language_version),expression=True)
        result=Evaluation(program,output_sink=sink,settings=admitted_settings(program,settings),history_account=self.history_account)
        result.expression_sink=sink
        result.observe(self.observer)
        return result


class Evaluation:
    """Private state of a single run; no state survives a new prepare()."""

    def __init__(self, program, *, output_sink=None,settings=None,history_account=None):
        self.program = program
        self.observer = None
        self.observation_branch = 0
        self.is_preview = False
        self.evaluate = self._evaluate
        self.storage_owner='primary'
        self.input_settings=admitted_settings(program,settings)
        self.states = {}
        self.outputs = {}
        self.frame = None
        self.environment = None
        self.environment_phase = "historical"
        self.times = []
        self.output_sink = output_sink
        self.expression_sink = None
        self.first_time = None
        self.last_time = None
        self.context_id = None
        self.count = 0
        self.closed = False
        self.at_boundary = True
        self.pending_outputs = []
        self.request_values = {}
        self.strategy_values = {}
        self.trade_reader = None
        self.order_intents = []
        self.persistent = {}
        self.values = {}
        self.work = WorkBudget(program.identity.language_version) if uses_loops(program) or uses_arrays(program) or uses_objects(program) else None
        self.heap = ArrayHeap(program.identity.language_version,history_account,getattr(program,'object_types',())) if uses_arrays(program) or uses_objects(program) else None
        self.cancelled = None
        self.first_only_loop = None
        self.budget = StorageBudget(account=history_account)
        from packages.contracts.history_storage import plan
        self.history_plan=plan(program,self.input_settings)
        self.history_pinned=bool(self.history_plan['calls'] or self.history_plan['histories'])
        self.history_capacities={r['call_site_id']:r['retention'] for r in self.history_plan['calls']}
        from packages.contracts.scalar_functions import ScalarBudget
        self.scalar_budget=ScalarBudget()
        from packages.contracts.context_ir import ir_nodes
        self.scalar_mode=any(getattr(n,"operation","").startswith("scalar.") for n in ir_nodes(program.statements))
        from packages.contracts.numeric_functions import NEW_OPERATIONS
        self.numeric_mode=any(getattr(n,"operation",None) in NEW_OPERATIONS or getattr(n,"overload_id","").endswith(".numeric.scalar.function.primary.prepared") for n in ir_nodes(program.statements))
        self.history = HistoryStore(program, self.budget, self.heap,self.history_plan)
        self.declaration = self.evaluate(program.statements[0])

    def observe(self,observer):
        self.observer=observer
        self.evaluate=type(self).evaluate.__get__(self) if observer is not None else self._evaluate

    def matches(self, value, value_type):
        if isinstance(value_type,TupleType):
            return type(value) is tuple and len(value)==len(value_type.elements) and all(self.matches(v,t) for v,t in zip(value,value_type.elements,strict=True))
        valid = value_matches(value, value_type, self.program.identity.language_version, missing_string=self.heap is not None or self.scalar_mode)
        if valid and type(value) is ArrayRef:
            return self.heap is not None and value.object_id in self.heap.objects and type(self.heap.objects[value.object_id]) is not RecordObject and self.heap.objects[value.object_id].element == value.element
        if valid and type(value) is ObjectRef:
            return self.heap is not None and type(self.heap.objects.get(value.object_id)) is RecordObject and self.heap.objects[value.object_id].element==value.type_id
        return valid

    def execute(self, node):
        if self.work:self.work.charge(node, self.cancelled)
        if isinstance(node, LoopControlIR):
            raise LoopTransfer(node.action, node.loop_id)
        elif isinstance(node, EvaluateIR):
            value=self.evaluate(node.expression)
            if self.expression_sink is not None and node is self.program.statements[-1]:self.expression_sink.last=value
        elif isinstance(node,UnpackIR):
            values=self.evaluate(node.expression)
            if not self.matches(values,node.expression.value_type):fail('UNPACK_VALUE','Tuple result differs from its type.',node)
            for binding,value in zip(node.bindings,values,strict=True):
                if binding is not None:self.values[binding.binding_id]=value
        elif isinstance(node, StoreIR):
            key = node.binding.binding_id
            if node.declaration and node.binding.mode in ('var','varip') and key in self.persistent:
                self.values[key] = self.persistent[key]
                if self.observer:self.observer.record(self,'store_reuse',node,value=self.values[key],binding_id=key)
                return
            previous_loop = self.first_only_loop
            direct = node.expression
            while isinstance(direct, ConvertIR):direct = direct.operand
            self.first_only_loop = direct.loop_id if node.declaration and node.binding.mode in ('var','varip') and isinstance(direct, (ForIR, WhileIR, ForInIR)) else previous_loop
            try:value = self.evaluate(node.expression)
            finally:self.first_only_loop = previous_loop
            if not self.matches(value, node.binding.value_type):
                fail("STORE_VALUE", "Stored value does not match its variable type.", node)
            self.values[key] = value
            if node.binding.mode in ('var','varip'):
                self.persistent[key] = value
            if self.observer:self.observer.record(self,'store',node,value=value,binding_id=key)
        elif isinstance(node, IfIR):
            condition = self.evaluate(node.condition)
            if self.observer:self.observer.record(self,'branch',node,value=bool(condition))
            chosen = node.body if condition else node.otherwise
            for statement in chosen:
                self.execute(statement)
        else:
            self.evaluate(node)

    def binary(self, node):
        left = self.evaluate(node.left)
        operator = node.operator
        if self.program.language_policy.logical_evaluation == "lazy":
            if operator == "and" and left is False:
                return False
            if operator == "or" and left is True:
                return True
        right = self.evaluate(node.right)
        if self.scalar_mode and operator=='+' and type(left) is str and type(right) is str:
            from packages.contracts.scalar_functions import concat,ScalarError
            try:return concat(left,right,self.scalar_budget)
            except ScalarError as error:raise KernelError('SCALAR_LIMIT',str(error),'rule.scalar') from error
        from packages.contracts.scalar_ops import binary
        return binary(operator,left,right,node.value_type.base,self.program.language_policy)

    def evaluate(self, node):
        if self.observer:
            from packages.runtime.observation import evaluated
            return evaluated(self,node)
        return self._evaluate(node)

    def _evaluate(self, node):
        if self.work:self.work.charge(node, self.cancelled)
        if isinstance(node,EnumIR):
            from .enums import evaluate
            return evaluate(self,node)
        if isinstance(node,ObjectIR):
            from .objects import evaluate
            value=evaluate(self,node)
            if node.value_type.base!='void' and not self.matches(value,node.value_type):fail('UDT_VALUE','Object operation result differs from its declared type/range.',node,'RUNTIME_ERROR')
            return value
        if isinstance(node, ArrayIR):
            try:value = self.heap.evaluate(self, node)
            except KernelError as error:
                raise OpenPineError(Diagnostic('RUNTIME_ERROR',error.code,str(error),node.span,error.rule_id,self.program.identity.language_version)) from error
            if node.value_type.base != 'void' and not self.matches(value, node.value_type):
                fail('ARRAY_VALUE', 'Array operation result exceeds its declared type/range.', node, 'RUNTIME_ERROR')
            return value
        if isinstance(node, (ForIR, WhileIR, ForInIR)):
            return evaluate_loop(self, node)
        if isinstance(node,InputIR):
            selected=self.input_settings['values'][node.definition.input_id]
            return getattr(self.frame,selected) if node.definition.input_type=='source' else selected
        if isinstance(node,CallIR) and node.operation=='trade.read':
            if self.trade_reader is None:fail('BROKER_REQUIRED','Trade reader requires the primary historical strategy.',node,'INPUT_UNAVAILABLE')
            try:
                group,field=node.overload_id.split('.')[-2:]
                return self.trade_reader(group,field,self.evaluate(node.arguments[0].expression))
            except ValueError as error:fail('TRADE_READ_BUDGET',str(error),node,'RUNTIME_ERROR')
        if isinstance(node,CallIR) and node.operation.startswith('simulation.'):
            from packages.contracts.trading import OrderIntent,StrategyDeclaration,decimal_text
            arguments=[None]*len(node.arguments)
            for position in node.evaluation_order:arguments[position]=self.evaluate(node.arguments[position].expression)
            if node.operation in STRATEGY_DECLARATIONS:return StrategyDeclaration(arguments[0],arguments[1])
            def order_fail(code,message,category='RUNTIME_ERROR'):
                raise OpenPineError(Diagnostic(category,code,str(message),node.span,language_version=self.program.identity.language_version))
            from packages.contracts.risk import OPERATIONS as RISK_OPERATIONS
            if node.operation in RISK_OPERATIONS:return None
            if not self.strategy_values:order_fail('BROKER_REQUIRED','Strategy broker was not scheduled.','INPUT_UNAVAILABLE')
            if self.program.identity.language_version<6 and not arguments[-1]:return None
            if len(self.order_intents)>=16:order_fail('ORDER_LIMIT','At most 16 reached commands per calculation are admitted.','PROFILE_UNCOVERED')
            from packages.contracts.order_groups import GroupEntryIntent,GroupExitIntent,CloseAllIntent
            if node.operation=='simulation.exit_extended':
                from packages.contracts.trailing import TrailingExitIntent,validate_distances
                try:
                    qty=decimal_text(arguments[4]) if arguments[4] is not None else None
                    percent=decimal_text(arguments[5]) if qty is None and arguments[5] is not None else None
                    levels=[decimal_text(arguments[i],positive=i in (2,3,9)) if arguments[i] is not None else None for i in (2,3,7,8,9,10,11)]
                    intent=TrailingExitIntent(node.call_site_id,'exit',arguments[1],arguments[0],levels[0],levels[1],qty,percent,arguments[6],*levels[2:])
                    validate_distances(intent);self.order_intents.append(intent)
                except ValueError as error:order_fail('EXIT_LEVEL',error)
                return None
            if node.operation=='simulation.close_all':
                self.order_intents.append(CloseAllIntent(node.call_site_id));return None
            if node.operation in ('simulation.order','simulation.entry_group','simulation.exit_group'):
                try:
                    if node.operation=='simulation.exit_group':
                        qty=decimal_text(arguments[4]) if arguments[4] is not None else None
                        percent=decimal_text(arguments[5]) if qty is None and arguments[5] is not None else None
                        intent=GroupExitIntent(node.call_site_id,'exit',arguments[1],arguments[0],
                            decimal_text(arguments[2],positive=True) if arguments[2] is not None else None,
                            decimal_text(arguments[3],positive=True) if arguments[3] is not None else None,qty,percent,arguments[6])
                    else:
                        direction=(1 if arguments[1] else -1) if self.program.identity.language_version<5 else (1 if arguments[1]=='long' else -1)
                        intent=GroupEntryIntent(node.call_site_id,'order' if node.operation=='simulation.order' else 'entry',arguments[0],direction,
                            decimal_text(arguments[2],positive=True) if arguments[2] is not None else None,
                            decimal_text(arguments[3],positive=True) if arguments[3] is not None else None,
                            decimal_text(arguments[4],positive=True) if arguments[4] is not None else None,arguments[5],arguments[6])
                    self.order_intents.append(intent)
                except ValueError as error:order_fail('ORDER_LEVEL',error)
                return None
            from packages.contracts.pending_entries import OPERATION as PENDING_OPERATION,EntryIntent
            if node.operation==PENDING_OPERATION:
                try:
                    direction=(1 if arguments[1] else -1) if self.program.identity.language_version<5 else (1 if arguments[1]=='long' else -1)
                    self.order_intents.append(EntryIntent(node.call_site_id,'entry',arguments[0],direction,
                        decimal_text(arguments[2],positive=True) if arguments[2] is not None else None,
                        decimal_text(arguments[3],positive=True) if arguments[3] is not None else None,
                        decimal_text(arguments[4],positive=True) if arguments[4] is not None else None))
                except ValueError as error:
                    raise OpenPineError(Diagnostic('RUNTIME_ERROR','ORDER_LEVEL',str(error),node.span,
                        language_version=self.program.identity.language_version)) from error
                return None
            from packages.contracts.partial_exits import OPERATIONS as PARTIAL_OPERATIONS,PartialCloseIntent,PartialProtectionIntent
            if node.operation in PARTIAL_OPERATIONS:
                try:
                    closing=node.operation=='simulation.close_partial';q=1 if closing else 4
                    quantity=decimal_text(arguments[q]) if arguments[q] is not None else None
                    percent=decimal_text(arguments[q+1]) if quantity is None and arguments[q+1] is not None else None
                    if closing:intent=PartialCloseIntent(node.call_site_id,'close',arguments[0],0,quantity,percent)
                    else:intent=PartialProtectionIntent(node.call_site_id,'exit',arguments[1],arguments[0],
                        decimal_text(arguments[2],positive=True) if arguments[2] is not None else None,
                        decimal_text(arguments[3],positive=True) if arguments[3] is not None else None,quantity,percent)
                    self.order_intents.append(intent)
                except ValueError as error:order_fail('ORDER_QUANTITY',error)
                return None
            from packages.contracts.protection import OPERATIONS,ProtectionIntent
            if node.operation in OPERATIONS:
                try:
                    if node.operation=='simulation.exit':
                        intent=ProtectionIntent(node.call_site_id,'exit',arguments[1],arguments[0],
                            decimal_text(arguments[2],positive=True) if arguments[2] is not None else None,
                            decimal_text(arguments[3],positive=True) if arguments[3] is not None else None)
                    else:intent=ProtectionIntent(node.call_site_id,node.operation.removeprefix('simulation.'),'',arguments[0] if node.operation=='simulation.cancel' else '')
                    self.order_intents.append(intent)
                except ValueError as error:order_fail('ORDER_LEVEL',error)
                return None
            entry=node.operation=='simulation.entry'
            try:
                direction=((1 if arguments[1] else -1) if self.program.identity.language_version<5 else (1 if arguments[1]=='long' else -1)) if entry else 0
                quantity=decimal_text(arguments[2],positive=True) if entry and arguments[2] is not None else None
                self.order_intents.append(OrderIntent(node.call_site_id,'entry' if entry else 'close',arguments[0],direction if entry else 0,quantity))
            except ValueError as error:order_fail('ORDER_QUANTITY',error)
            return None
        if isinstance(node,CallIR) and node.operation in ("context.request","context.lower_tf"):
            if node.call_site_id not in self.request_values:
                fail("CONTEXT_INPUT_REQUIRED","Request context was not scheduled.",node,"INPUT_UNAVAILABLE")
            value=self.request_values[node.call_site_id]
            if node.operation=='context.lower_tf':
                if node.call_site_id in self.lower_arrays:return self.lower_arrays[node.call_site_id]
                from packages.contracts.lower_tf import ClosedWindow
                if type(value) is not ClosedWindow:fail('REQUEST_VALUE','Missing closed lower window.',node)
                value=value.arrays(node.value_type)
                def materialize(items,typ):
                    if isinstance(typ,TupleType):
                        if type(items) not in (tuple,list) or len(items)!=len(typ.elements):fail('REQUEST_VALUE','Invalid lower tuple.',node)
                        return tuple(materialize(v,t) for v,t in zip(items,typ.elements,strict=True))
                    element=typ.base.removeprefix('array.')
                    if type(items) not in (tuple,list) or not all(value_matches(v,ValueType(element),self.program.identity.language_version) for v in items):
                        fail('REQUEST_VALUE','Invalid lower array elements.',node)
                    self.heap.capacity(len(self.heap.objects)+1,self.heap.cells+len(items),len(items),self.heap.bytes,node)
                    self.heap.charge(len(items),self,node)
                    return self.heap.create(element,list(items),0,self,node)
                value=materialize(value,node.value_type);self.lower_arrays[node.call_site_id]=value
            if not self.matches(value,node.value_type):fail("REQUEST_VALUE","Invalid requested value.",node)
            return value
        if isinstance(node, (BlockIR, UserCallIR)):
            if isinstance(node, UserCallIR):
                arguments = [None] * len(node.arguments)
                for index in node.evaluation_order:
                    arguments[index] = self.evaluate(node.arguments[index].expression)
                for parameter, value in zip(node.parameters, arguments, strict=True):
                    if not self.matches(value, parameter.value_type):
                        fail("FUNCTION_PARAMETER_VALUE", "Function argument has an incompatible value.", node)
                    self.values[parameter.binding_id] = value
            for statement in node.statements:
                self.execute(statement)
            return self.evaluate(node.result)
        if isinstance(node, LiteralIR):
            return node.value
        if isinstance(node,TupleIR):
            return tuple(self.evaluate(child) for child in node.elements)
        if isinstance(node, NAIR):
            return None
        if isinstance(node, LoadIR):
            if node.binding_id not in self.values:
                fail("VARIABLE_UNINITIALIZED", "Variable was not initialized on this execution path.", node)
            return self.values[node.binding_id]
        if isinstance(node, ReferenceIR):
            if node.contract.slot.startswith('environment.'):
                from packages.runtime.environment import reference
                try:return reference(self,node)
                except KernelError as error:
                    raise OpenPineError(Diagnostic('RUNTIME_ERROR',error.code,str(error),node.span,error.rule_id,self.program.identity.language_version)) from error
            if node.contract.slot.startswith('strategy.'):
                key=node.contract.slot.removeprefix('strategy.')
                if key not in self.strategy_values:fail('BROKER_REQUIRED','Strategy broker was not scheduled.',node,'INPUT_UNAVAILABLE')
                return self.strategy_values[key]
            if self.frame is None:
                fail("BARS_REQUIRED", "A context reference requires a current bar.", node, "INPUT_UNAVAILABLE")
            return getattr(self.frame, node.contract.slot)
        if isinstance(node, HistoryIR):
            current, offset = self.evaluate(node.operand), self.evaluate(node.offset)
            try:
                return self.history.read(node, offset, current, self.program.identity.language_version)
            except KernelError as error:
                raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), node.span,
                    error.rule_id, self.program.identity.language_version)) from error
        if isinstance(node, BinaryIR):
            value = self.binary(node)
        elif isinstance(node, ConditionalIR):
            condition=self.evaluate(node.condition)
            if self.observer:self.observer.record(self,'branch',node,value=bool(condition))
            chosen = node.when_true if condition else node.when_false
            value = self.evaluate(chosen)
        elif isinstance(node, (UnaryIR, ConvertIR)):
            operand = self.evaluate(node.operand)
            if isinstance(node,ConvertIR) and isinstance(node.value_type,TupleType):
                value=tuple(None if v is None else float(v) if t.base=='float' else v for v,t in zip(operand,node.value_type.elements,strict=True))
            elif isinstance(node, ConvertIR) and node.value_type.base == "bool":
                value = False if operand is None else bool(operand)
            elif operand is None:
                value = None
            elif isinstance(node, ConvertIR):
                value = float(operand)
            else:
                value = not operand if node.operator == "not" else -operand if node.operator == "-" else operand
        else:
            arguments = [None] * len(node.arguments)
            for position in node.evaluation_order:
                arguments[position] = self.evaluate(node.arguments[position].expression)
            context = KernelContext(node.call_site_id, self.frame.context_id if self.frame else "literal", self.states,
                                    self.budget, self.program.identity.language_version, node.value_type.base, self.scalar_budget, self.scalar_budget if self.numeric_mode else None,self.history_capacities.get(node.call_site_id,100000))
            self.budget.label=f'{self.storage_owner} / {context.call_site_id} / {context.context_id}'
            try:
                value = KERNELS[node.operation].function(tuple(arguments), context)
            except KernelError as error:
                raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), node.span,
                                               error.rule_id, self.program.identity.language_version)) from error
            if isinstance(value, IndicatorDeclaration):
                return value
            if isinstance(value, PlotEvent):
                if self.observer:self.observer.record(self,'output',node,value=value.value,title=value.title)
                self.pending_outputs.append((node.call_site_id,value.title,
                    node.arguments[0].expression.value_type,value.value))
                return None
        if not self.matches(value, node.value_type):
            raise OpenPineError(Diagnostic("PROFILE_UNCOVERED", "VALUE_RANGE", "Result exceeds the numeric or string range.", node.span,
                                           language_version=self.program.identity.language_version))
        return value

    def step(self, frame: BarFrame | None = None):
        self._validate_frame(frame)
        self.at_boundary = False
        try:
            self._step(frame)
            self.at_boundary = True
        except BaseException:
            self.closed = True
            self.pending_outputs.clear()
            raise

    def _validate_frame(self, frame):
        if self.closed:
            fail("EXECUTION_CLOSED", "This evaluation has already finished.")
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            if type(frame) is not BarFrame or frame.bar_index != self.count or type(frame.bar_index) is not int or type(frame.time) is not int or not 0 <= frame.time < 2**63 or (self.last_time is not None and frame.time <= self.last_time):
                fail("BAR_ORDER", "Historical frames must be supplied once in chronological order.")
            if self.environment is not None:
                from packages.contracts.calendar import MAX_TIME,aligned
                if (frame.context_id!=self.environment.context_id or frame.time>=MAX_TIME or not aligned(frame.time,self.environment.timeframe)
                        or self.environment.mode=='historical' and frame.bar_index>=self.environment.count):
                    fail('ENVIRONMENT_FRAME','Frame differs from the pinned environment/calendar.',category='INPUT_INVALID')
            if not value_matches(frame.close, ValueType("float", "series")) or type(frame.context_id) is not str or not frame.context_id or (self.context_id is not None and frame.context_id != self.context_id):
                fail("BAR_CONTEXT", "A run requires finite close values and one fixed context.")
            if any(not value_matches(getattr(frame, slot), ValueType("float", "series")) for slot in ("open", "high", "low", "volume")) or (frame.volume is not None and frame.volume < 0):
                fail("BAR_CONTEXT", "OHLCV values must be finite numbers or na; volume cannot be negative.")
            prices = [v for v in (frame.open, frame.close) if v is not None]
            if (frame.high is not None and any(frame.high < v for v in [*prices, *([frame.low] if frame.low is not None else [])])) or (frame.low is not None and any(frame.low > v for v in prices)):
                fail("BAR_CONTEXT", "OHLC values are inconsistent.")
        elif frame is not None or self.count:
            fail("BAR_PROFILE", "The literal profile accepts one synthetic observation.")

    def _step(self, frame):
        self.lower_arrays={}
        if self.work:self.work.reset()
        from packages.contracts.scalar_functions import ScalarBudget
        self.scalar_budget=ScalarBudget()
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            self.context_id = frame.context_id
            if self.first_time is None:
                self.first_time = frame.time
            self.last_time = frame.time
            if self.output_sink is None:
                self.times.append(frame.time)
        self.frame = frame
        self.pending_outputs.clear()
        self.values = dict(self.persistent)
        try:
            for statement in self.program.statements[1:]:
                self.execute(statement)
            if getattr(self,'deferred_commit',False):return
            self._finish_step(frame)
        except KernelError as error:
            self.closed = True
            raise OpenPineError(Diagnostic("RUNTIME_ERROR", error.code, str(error), rule_id=error.rule_id,
                language_version=self.program.identity.language_version)) from error
        except OpenPineError:
            self.closed = True  # An incomplete bar cannot be resumed or published as a result.
            raise

    def _finish_step(self,frame):
        self.history.owner=self.storage_owner
        self.history.commit(frame, self.values,self.strategy_values,self.evaluate)
        if self.heap:
            self.heap.collect(self.persistent, self, self.program.statements[-1])
            if not self.is_preview:self.heap.confirm_records()
        # Output becomes eligible only after every statement and history
        # update succeeded. A sink failure poisons this evaluation; its
        # incomplete tail can never become a checkpoint or final result.
        for site,title,value_type,value in self.pending_outputs:
            if site not in self.outputs:
                self.outputs[site] = (title,value_type,[])
            if self.output_sink is None:
                self.outputs[site][2].append(value)
            else:
                self.output_sink.emit(site,title,value_type,value,self.count,frame.time)
        self.pending_outputs.clear()
        self.count += 1
        if self.observer:self.observer.record(self,'bar_commit' if self.observation_branch==0 else 'candidate_complete')

    def checkpoint(self,*,streaming=False):
        from packages.runtime.checkpoints import export_state
        return export_state(self,streaming=streaming)

    def memory_status(self):
        account=self.budget.allocation.account if self.budget.allocation else None
        if self.heap is not None and self.heap.object_types:
            arrays=[o for o in self.heap.objects.values() if type(o) is not RecordObject]
            objects=[o for o in self.heap.objects.values() if type(o) is RecordObject]
            return dict(plan=self.history_plan,logical_cells=self.budget.cells,logical_cell_limit=self.budget.limit,
                arrays=dict(elements=sum(len(o.items) for o in arrays),objects=len(arrays),string_bytes=sum(o.string_bytes for o in arrays)),
                objects=dict(fields=sum(len(o.items) for o in objects),objects=len(objects),string_bytes=sum(o.string_bytes for o in objects)),
                ledger=account.snapshot() if account else None)
        return dict(plan=self.history_plan,logical_cells=self.budget.cells,logical_cell_limit=self.budget.limit,
            arrays=dict(elements=self.heap.cells,objects=len(self.heap.objects),string_bytes=self.heap.bytes) if self.heap else None,
            ledger=account.snapshot() if account else None)

    def restore(self, state):
        from packages.runtime.checkpoints import restore_state
        restore_state(self,state)

    def finish(self, data=None) -> RunResult:
        if self.closed or not self.count:
            fail("EXECUTION_COUNT", "A completed, nonempty evaluation is required.")
        if self.program.identity.implementation_profile_id == HISTORICAL_PROFILE_ID:
            if data is None or data.context_id != self.context_id or data.times != tuple(self.times):
                fail("DATA_IDENTITY", "Result data identity does not match the executed frames.")
        elif data is not None:
            fail("DATA_IDENTITY", "Literal results have no bar dataset.")
        self.closed = True
        outputs = tuple(PlotOutput(site, title, value_type, tuple(values))
                        for site, (title, value_type, values) in self.outputs.items())
        return RunResult(self.program.identity, program_hash(self.program), self.declaration, outputs, self.count, data)

    def finish_stream(self, data):
        """Finalize a sink-backed run without retaining timestamps or plot arrays."""
        if (self.closed or self.output_sink is None or not self.count or
                (data.context_id, data.count, data.first_ms, data.last_ms) !=
                (self.context_id, self.count, self.first_time, self.last_time)):
            fail("DATA_IDENTITY", "Stream identity does not match the executed frames.")
        self.closed = True
        return {"schema_version": "openpine.run-summary.3", "observation_count": self.count,
            "declaration": asdict(self.declaration), "identity": asdict(self.program.identity),
            "program_sha256": program_hash(self.program), "data": asdict(data)}
