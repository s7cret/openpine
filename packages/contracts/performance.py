"""Artifact-defined trade observations; distinct from orders, fills and positions."""
from dataclasses import dataclass,field
from .trading import BrokerPolicy,BrokerSettings
from .economics import EconomicsPolicy,CommissionPolicy
from .risk import RiskLimits,RiskPolicy
from .calendar import timeframe

PROFILE='openpine.performance-broker.1'
VARIABLES={k:('int' if k in ('opentrades','closedtrades','wintrades','losstrades') else 'float') for k in ('netprofit','openprofit','grossprofit','grossloss','wintrades','losstrades','opentrades','closedtrades')}
OPEN_FIELDS=('size','entry_price','profit','commission','entry_bar_index','entry_time')
CLOSED_FIELDS=(*OPEN_FIELDS,'exit_price','exit_bar_index','exit_time')
OPERATION='trade.read'
NOTE='Trade observations use the existing historical ledger: a closed trade is one allocated closed lot slice, open indices are current surviving entry order, closed indices are stable from zero. Profit and win/loss classification use allocated net P/L, open profit deducts remaining entry fees. These commission and early-version choices are explicit reconstructions, not external execution equivalence.'
NAMES={'strategy.'+k for k in VARIABLES}|{'strategy.'+group+'.'+k for group,fields in (('opentrades',OPEN_FIELDS),('closedtrades',CLOSED_FIELDS)) for k in fields}

@dataclass(frozen=True)
class PerformancePolicy(BrokerPolicy):
 profile_id:str=PROFILE
 pyramiding:int=0
 close_entries_rule:str='FIFO'
 language_version:int=6
 quantity_mode:str='fixed'
 commission:CommissionPolicy=field(default_factory=CommissionPolicy)
 limits:RiskLimits|None=None
 timeframe:str='1'
 def __post_init__(self):
  EconomicsPolicy(self.initial_capital,self.default_quantity,self.commission_percent,self.slippage_ticks,self.settings,pyramiding=self.pyramiding,close_entries_rule=self.close_entries_rule,language_version=self.language_version,quantity_mode=self.quantity_mode,commission=self.commission)
  if self.profile_id!=PROFILE:raise ValueError('Unknown performance profile.')
  timeframe(self.timeframe)
  if self.limits is not None:RiskPolicy(self.initial_capital,self.default_quantity,self.commission_percent,self.slippage_ticks,self.settings,pyramiding=self.pyramiding,close_entries_rule=self.close_entries_rule,language_version=self.language_version,quantity_mode=self.quantity_mode,commission=self.commission,limits=self.limits,timeframe=self.timeframe)
 @classmethod
 def from_payload(cls,v):
  if type(v) is not dict or v.keys()!=set(cls.__dataclass_fields__):raise ValueError('Invalid performance policy fields.')
  return cls(**{**v,'settings':BrokerSettings.from_payload(v['settings']),'commission':CommissionPolicy.from_payload(v['commission']),'limits':RiskLimits.from_payload(v['limits']) if v['limits'] is not None else None})

def apis(v):
 return [[f'strategy.{group}.{name}',group,name,'int' if name.endswith(('_bar_index','_time')) else 'float'] for group,fields in (('opentrades',OPEN_FIELDS),('closedtrades',CLOSED_FIELDS)) for name in fields] if v>=5 else []

def policy(v):return {'language_version':v,'profile_id':f'openpine.performance.v{v}.1','variables':VARIABLES,'apis':apis(v),
 'closed_unit':'one_closed_lot_slice_in_ledger_event_order_including_partial_exits','open_unit':'surviving_lot_in_entry_sequence_order',
 'profit':'net_of_allocated_entry_and_exit_fees','openprofit':'unrealized_less_remaining_entry_fees','grossprofit':'sum_positive_closed_net','grossloss':'absolute_sum_negative_closed_net',
 'identity':'equity_equals_initial_plus_netprofit_plus_openprofit_before_output_rounding','visibility':'after_current_bar_fills_before_primary_script_no_future_fills',
 'invalid_index':'na_for_na_negative_or_out_of_range_no_clamping','index_type':'series_int_only','closed_index':'stable_zero_based_no_trimming','read_budget_per_bar':4096,
 'storage':'disposable_sqlite_index_rebuilt_streaming_from_content_addressed_journal_prefix_bounded_lru','checkpoint':'closed_counters_and_hash_chain_checked_against_journal_before_broker_assignment',
 'requested_context':'private_indicator_context_cannot_read_trading_state','replay':'confirmed_history_only','external_execution_verified':False}

def uses_performance(program):
 from dataclasses import fields,is_dataclass
 from .model import ReferenceIR,CallIR
 def walk(n):
  if isinstance(n,ReferenceIR) and n.contract.slot in {'strategy.'+k for k in VARIABLES}:return True
  if isinstance(n,CallIR) and n.operation==OPERATION:return True
  if is_dataclass(n):return any(walk(getattr(n,f.name)) for f in fields(n))
  return type(n) in (tuple,list) and any(walk(x) for x in n)
 return walk(program.statements)
