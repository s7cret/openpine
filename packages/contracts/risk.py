"""Pinned global risk rules, separate from sizing and the broker implementation."""
from dataclasses import asdict,dataclass,field
from decimal import Decimal
from .trading import BrokerPolicy,BrokerSettings,decimal_text
from .economics import CommissionPolicy,EconomicsPolicy
from .calendar import timeframe
from .wire import digest

PROFILE='openpine.risk-broker.1'
KINDS=('allow_entry_in','max_position_size','max_drawdown','max_cons_loss_days','max_intraday_loss','max_intraday_filled_orders')
OPERATIONS=tuple('simulation.risk_'+n for n in KINDS)
NAMES={'strategy.risk.'+n for n in KINDS}|{'strategy.direction.all','strategy.direction.long','strategy.direction.short','strategy.cash','strategy.percent_of_equity'}
NOTE='Static global risk rules v1-v6 use observed open, actual fills and confirmed close. Close-triggered liquidation waits for next open; known-price boundaries liquidate immediately with normal costs. UTC observed days, W/M bar periods and explicit early-version reconstruction; no tick-path or external execution equivalence.'
FACTS={'entry_scope':'Direction and position caps apply to strategy.entry, not strategy.order.',
 'global_stops':'Drawdown and consecutive losing days halt trading; intraday rules halt until the next session.',
 'effects':'A triggered stop cancels pending orders and closes the position.',
 'timing':'Official risk commands apply independently of placement on ticks and order execution events.',
 'higher_periods':'The official intraday descriptions use a chart bar for timeframes higher than daily.'}
def source_review():return {'date':'2026-10-25','urls':['https://www.tradingview.com/pine-script-docs/concepts/strategies/#risk-management'],
 'facts':dict(FACTS),'selected_facts_sha256':digest(FACTS)}

@dataclass(frozen=True)
class RiskRule:
 kind:str
 call_site_id:str
 value:str|int
 unit:str|None=None
 alert_message:str=''
 def __post_init__(self):
  if self.kind not in KINDS or type(self.call_site_id) is not str or not 0<len(self.call_site_id)<=256 or type(self.alert_message) is not str or len(self.alert_message.encode())>1024:raise ValueError('Invalid static risk rule identity/message.')
  if self.kind=='allow_entry_in':
   if type(self.value) is not str or self.value not in ('all','long','short') or self.unit is not None or self.alert_message:raise ValueError('Invalid allowed entry direction.')
  elif self.kind in ('max_cons_loss_days','max_intraday_filled_orders'):
   if type(self.value) is not int or not 0<=self.value<=1000000 or self.unit is not None:raise ValueError('Risk counts require integers 0..1000000.')
  else:
   if type(self.value) is not str or decimal_text(self.value,positive=self.kind=='max_position_size')!=self.value or Decimal(self.value)<0:raise ValueError('Risk values require canonical nonnegative decimals.')
   if self.kind=='max_position_size':
    if self.unit is not None or self.alert_message:raise ValueError('Position size has no unit or alert message.')
   elif self.unit not in ('cash','percent_of_equity') or self.unit=='percent_of_equity' and Decimal(self.value)>100:raise ValueError('Risk threshold requires cash or percentage 0..100.')
 @classmethod
 def from_payload(cls,v):
  if type(v) is not dict or v.keys()!=set(cls.__dataclass_fields__):raise ValueError('Invalid risk rule fields.')
  return cls(**v)

@dataclass(frozen=True)
class RiskLimits:
 allow_entry_in:RiskRule|None=None
 max_position_size:RiskRule|None=None
 max_drawdown:RiskRule|None=None
 max_cons_loss_days:RiskRule|None=None
 max_intraday_loss:RiskRule|None=None
 max_intraday_filled_orders:RiskRule|None=None
 def ordered(self):return tuple(getattr(self,n) for n in KINDS if getattr(self,n) is not None)
 def __post_init__(self):
  for n in KINDS:
   r=getattr(self,n)
   if r is not None and (type(r) is not RiskRule or r.kind!=n):raise ValueError('Risk rule belongs to another slot.')
  rows=self.ordered()
  if not rows or len({r.call_site_id for r in rows})!=len(rows):raise ValueError('Require distinct source sites for risk rules.')
 @classmethod
 def from_payload(cls,v):
  if type(v) is not dict or v.keys()!=set(cls.__dataclass_fields__):raise ValueError('Invalid risk limit fields.')
  return cls(**{k:RiskRule.from_payload(r) if r is not None else None for k,r in v.items()})

@dataclass(frozen=True)
class RiskPolicy(BrokerPolicy):
 profile_id:str=PROFILE
 pyramiding:int=0
 close_entries_rule:str='FIFO'
 language_version:int=6
 quantity_mode:str='fixed'
 commission:CommissionPolicy=field(default_factory=CommissionPolicy)
 limits:RiskLimits|None=None
 timeframe:str='1'
 def __post_init__(self):
  # Reuse the economic contract validation without changing its existing wire ABI.
  EconomicsPolicy(self.initial_capital,self.default_quantity,self.commission_percent,self.slippage_ticks,self.settings,
   pyramiding=self.pyramiding,close_entries_rule=self.close_entries_rule,language_version=self.language_version,quantity_mode=self.quantity_mode,commission=self.commission)
  if self.profile_id!=PROFILE or type(self.limits) is not RiskLimits:raise ValueError('Require complete static risk limits.')
  timeframe(self.timeframe)
  if self.timeframe in ('W','M') and self.limits.max_cons_loss_days is not None:raise ValueError('Consecutive losing days require primary candles no higher than D.')
 @classmethod
 def from_payload(cls,v):
  if type(v) is not dict or v.keys()!=set(cls.__dataclass_fields__):raise ValueError('Invalid risk policy fields.')
  return cls(**{**v,'settings':BrokerSettings.from_payload(v['settings']),'commission':CommissionPolicy.from_payload(v['commission']),'limits':RiskLimits.from_payload(v['limits'])})

def rule_from_node(node):
 from .strategy_ir import literal
 kind=node.operation.removeprefix('simulation.risk_');v=[literal(a.expression) for a in node.arguments]
 expected=1 if kind in ('allow_entry_in','max_position_size') else 3 if kind in ('max_drawdown','max_intraday_loss') else 2
 if len(v)!=expected:raise ValueError('Risk declaration argument count differs.')
 value=v[0] if kind in ('allow_entry_in','max_cons_loss_days','max_intraday_filled_orders') else decimal_text(v[0])
 return RiskRule(kind,node.call_site_id,value,v[1] if kind in ('max_drawdown','max_intraday_loss') else None,v[-1] if expected>1 else '')

def program_rules(program):
 from .model import CallIR
 from .strategy_ir import invalid
 rows={}
 for node in program.statements:
  if isinstance(node,CallIR) and node.operation in OPERATIONS:
   try:r=rule_from_node(node)
   except ValueError as e:invalid(str(e),node,'RISK_SETTINGS')
   if r.kind in rows:invalid('Duplicate static risk declaration.',node,'RISK_DUPLICATE')
   rows[r.kind]=r
 return RiskLimits(**rows) if rows else None

def risk_apis(v):
 result=[]
 for kind in KINDS:
  parameters=[('value','string' if kind=='allow_entry_in' else 'float','const',None)]
  if kind=='max_position_size':parameters=[('contracts','float','const',None)]
  if kind in ('max_cons_loss_days','max_intraday_filled_orders'):parameters=[('count','int','const',None)]
  if kind in ('max_drawdown','max_intraday_loss'):parameters+=[('type','string','const',None)]
  if kind not in ('allow_entry_in','max_position_size'):parameters+=[('alert_message','string','const','')]
  result.append(('strategy.risk.'+kind,'simulation.risk_'+kind,parameters))
 return result

def risk_policy(v):return {'language_version':v,'profile_id':f'openpine.risk.v{v}.1',
 'basis':'explicit_early_signature_reconstruction' if v<3 else 'artifact_names_with_explicit_ohlc_risk_model',
 'declarations':'global_literals_one_per_kind_no_conditional_udf_or_runtime_toggle','entry_scope':'direction_and_position_cap_only_strategy_entry_including_grouped_entries',
 'cap':'actual_fill_destination_units_floor_to_step_then_existing_fill_minima_no_resize_strategy_order',
 'disallowed_reversal':'close_existing_opposite_position_without_new_entry',
 'sizing':'existing_creation_close_quantity_before_risk_actual_fill_cap',
 'observations':['known_open','each_actual_fill_after_ledger_update','confirmed_close'],
 'liquidation':'cancel_all_before_next_candidate_known_boundary_immediate_close_close_boundary_next_open_with_normal_slippage_and_fees',
 'priority':['max_drawdown','max_cons_loss_days','max_intraday_loss','max_intraday_filled_orders'],
 'threshold':'inclusive_zero_halts_immediately_percent_of_nonpositive_equity_permanent',
 'daily':'UTC_observed_calendar_days_W_M_each_chart_bar_no_fabricated_gap_days',
 'consecutive_days':'D_or_lower_only_change_in_realized_profit_minus_all_fees_zero_resets_unobserved_days_ignored',
 'filled_orders':'distinct_order_id_per_period_reversal_once_partial_exit_once_same_period_forced_close_included_only_live_seen_ids',
 'warmup':'risk_state_stays_pristine_until_orders_start',
 'messages':'bounded_literal_journal_metadata_no_alert_transport','accounting':'linear_quote_pointvalue_one_no_margin_or_fx',
 'replay':'confirmed_history_only','external_execution_verified':False}
