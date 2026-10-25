"""Orthogonal sizing, trading constraints and fee contracts for confirmed history."""
from dataclasses import asdict,dataclass,field
from decimal import Decimal
from .trading import BrokerPolicy,BrokerSettings,decimal_text,strategy_policy
from .wire import digest

PROFILE='openpine.economics-broker.1'
OPERATION='simulation.strategy_economics'
NAMES={'strategy','strategy.entry','strategy.order','strategy.fixed','strategy.cash','strategy.percent_of_equity',
       'strategy.commission.percent','strategy.commission.cash_per_contract','strategy.commission.cash_per_order'}
NOTE='Economic orders freeze quantity at confirmed creation close, with explicit qty precedence and downward rounding only for derived quantities. This bounded creation-time policy deliberately differs from fill-time equity sizing; gaps change notional, not frozen units. Flat fees are charged on the first actual fill per order ID. Legacy names/early signatures are reconstructed; no external execution equivalence is claimed.'
SOURCE_FACTS={'quantity_override':'An explicit non-na qty overrides the default mode and value.',
 'modes':'Fixed units, account-currency amount and percentage of equity are distinct sizing modes.',
 'official_equity_timing':'The support guide describes available equity when the trade opens.',
 'fees':'Fees can be percent of transaction value, cash per unit, or cash per order.',
 'cash_example':'The v5/v6 cash reference example divides the amount by close price.'}
def source_review():return {'date':'2026-10-25','urls':['https://www.tradingview.com/support/solutions/43000628599-strategy-properties/','https://www.tradingview.com/pine-script-docs/concepts/strategies/','https://www.tradingview.com/pine-script-reference/v6/'],
 'facts':dict(SOURCE_FACTS),'selected_facts_sha256':digest(SOURCE_FACTS)}

@dataclass(frozen=True)
class EconomicsSettings(BrokerSettings):
    schema_version:str='openpine.broker-settings.2'
    minimum_quantity:str|None=None
    minimum_notional:str|None=None
    def __post_init__(self):
        BrokerSettings(self.tick_size,self.quantity_step,self.money_decimals)
        if self.schema_version!='openpine.broker-settings.2':raise ValueError('Invalid economic settings version.')
        for n in ('minimum_quantity','minimum_notional'):
            v=getattr(self,n)
            if v is not None and (type(v) is not str or decimal_text(v,positive=True)!=v):raise ValueError('Minimum constraints require canonical positive values or null.')
        if self.minimum_quantity is not None and Decimal(self.minimum_quantity)%Decimal(self.quantity_step):raise ValueError('Minimum quantity must align with its step.')

@dataclass(frozen=True)
class CommissionPolicy:
    kind:str='percent'
    value:str='0'
    schema_version:str='openpine.commission-policy.1'
    def __post_init__(self):
        if self.schema_version!='openpine.commission-policy.1' or self.kind not in ('percent','cash_per_contract','cash_per_order') or type(self.value) is not str or decimal_text(self.value)!=self.value or Decimal(self.value)<0 or self.kind=='percent' and Decimal(self.value)>100:raise ValueError('Invalid commission policy.')
    @classmethod
    def from_payload(cls,v):
        if type(v) is not dict or v.keys()!=set(cls.__dataclass_fields__):raise ValueError('Invalid commission fields.')
        return cls(**v)

@dataclass(frozen=True)
class EconomicsPolicy(BrokerPolicy):
    profile_id:str=PROFILE
    pyramiding:int=0
    close_entries_rule:str='FIFO'
    language_version:int=6
    quantity_mode:str='fixed'
    commission:CommissionPolicy=field(default_factory=CommissionPolicy)
    def __post_init__(self):
        super().__post_init__()
        if self.profile_id!=PROFILE or type(self.pyramiding) is not int or not 0<=self.pyramiding<=16 or self.close_entries_rule not in ('FIFO','ANY') or type(self.language_version) is not int or not 1<=self.language_version<=6 or self.quantity_mode not in ('fixed','cash','percent_of_equity') or type(self.commission) is not CommissionPolicy or self.commission_percent!='0':raise ValueError('Invalid economics policy.')
        if Decimal(self.default_quantity)<0 or self.quantity_mode=='fixed' and Decimal(self.default_quantity)<=0 or self.quantity_mode=='percent_of_equity' and Decimal(self.default_quantity)>100:raise ValueError('Invalid default sizing value.')
    @classmethod
    def from_payload(cls,v):
        if type(v) is not dict or v.keys()!=set(cls.__dataclass_fields__):raise ValueError('Invalid economic policy fields.')
        return cls(**{**v,'settings':BrokerSettings.from_payload(v['settings']),'commission':CommissionPolicy.from_payload(v['commission'])})

def economics_apis(v):
    return [('strategy',OPERATION,[('title','string','const',None),('overlay','bool','const',False),('pyramiding','int','const',0),
       ('default_qty_type','string','const','fixed'),('default_qty_value','float','const',1.),('initial_capital','float','const',float(strategy_policy(v)['initial_capital'])),
       ('slippage','int','const',0),('commission_type','string','const','percent'),('commission_value','float','const',0.),('close_entries_rule','string','const','FIFO')])]

def economics_policy(v):return {'language_version':v,'profile_id':f'openpine.economics.v{v}.1',
 'basis':'explicit_reconstruction' if v<3 else 'artifact_names_with_explicit_economic_model',
 'quantity_modes':['fixed','cash','percent_of_equity'],'quantity_basis':'confirmed_creation_close_before_new_commands',
 'explicit_qty':'non_na_overrides_defaults_strict_step_no_silent_rounding','derived_qty':'floor_to_pinned_step',
 'cash_budget':'default_value_in_quote_account_currency','percent_budget':'creation_equity_times_value_over_100_without_fee_reservation',
 'percent_range':'0_to_100','pending':'frozen_units_gap_changes_notional_oca_reduces_remaining_units',
 'zero_or_minimum':'skip_entry_order_with_reason_no_resizing','minimum_checks':'entry_order_creation_and_actual_fill_exits_exempt',
 'fees':['percent','cash_per_contract','cash_per_order'],'flat_fee':'first_actual_fill_once_per_order_id_no_future_reallocation',
 'reversal_fee':'shared_order_id_close_fill_first_open_fill_next','fee_state':'cumulative_and_retired_counts_plus_only_live_paid_order_ids',
 'metadata':'versioned_settings_or_pinned_snapshot_rules_never_inferred_from_ohlc','accounting':'linear_quote_pointvalue_one_no_margin_fx_or_funding',
 'header_defaults':'fixed_and_one_in_new_header_legacy_headers_unchanged','replay':'confirmed_history_only','external_execution_verified':False}

def validate_rules(v):
    expected={'schema_version','tick_size','quantity_step','minimum_quantity','minimum_notional'}
    if type(v) is not dict or v.keys()!=expected or v['schema_version']!='openpine.trading-rules.1':raise ValueError('Invalid pinned trading rules.')
    EconomicsSettings(v['tick_size'],v['quantity_step'],minimum_quantity=v['minimum_quantity'],minimum_notional=v['minimum_notional'])
    return dict(v)
