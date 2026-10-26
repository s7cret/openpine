"""Closed, versioned contracts of the bounded historical market broker."""

from __future__ import annotations

from dataclasses import dataclass,field
from decimal import Decimal,InvalidOperation,localcontext
from .model import RunResult


STRATEGY_DECLARATIONS=frozenset(('simulation.strategy','simulation.strategy_lots','simulation.strategy_economics','simulation.strategy_intrabar'))


def decimal_text(value,*,positive=False):
    if type(value) not in (str,int,float):raise ValueError("Expected a finite decimal scalar.")
    try:number=Decimal(str(value))
    except InvalidOperation as error:raise ValueError("Invalid decimal scalar.") from error
    if not number.is_finite() or abs(number)>Decimal('1e24') or number.as_tuple().exponent < -16 or (positive and number<=0):
        raise ValueError("Decimal scalar exceeds the bounded broker range/precision.")
    return format(number.normalize(),'f') if number else '0'


@dataclass(frozen=True)
class BrokerSettings:
    tick_size: str | None = None
    quantity_step: str = '0.00000001'
    money_decimals: int = 8
    schema_version: str = 'openpine.broker-settings.1'

    def __post_init__(self):
        if (self.schema_version!='openpine.broker-settings.1' or type(self.money_decimals) is not int
                or not 0<=self.money_decimals<=12):raise ValueError("Invalid broker settings.")
        for name in ('tick_size','quantity_step'):
            value=getattr(self,name)
            if name=='tick_size' and value is None:continue
            if type(value) is not str or decimal_text(value,positive=True)!=value:raise ValueError("Broker steps require canonical positive decimal strings.")

    @classmethod
    def from_payload(cls,value):
        if cls is BrokerSettings and type(value) is dict and value.get('schema_version')=='openpine.broker-settings.2':
            from .economics import EconomicsSettings
            return EconomicsSettings.from_payload(value)
        if type(value) is not dict or value.keys()!=set(cls.__dataclass_fields__):raise ValueError("Invalid broker settings fields.")
        return cls(**value)


@dataclass(frozen=True)
class BrokerPolicy:
    initial_capital: str
    default_quantity: str
    commission_percent: str
    slippage_ticks: int
    settings: BrokerSettings = field(default_factory=BrokerSettings)
    profile_id: str = 'openpine.market-broker.1'

    def __post_init__(self):
        if self.profile_id=='openpine.economics-broker.1':
            from .economics import EconomicsPolicy
            if type(self) is not EconomicsPolicy:raise ValueError('Economic policy requires complete fields.')
        elif self.profile_id=='openpine.trailing-broker.1':
            from .trailing import TrailingPolicy
            if type(self) is not TrailingPolicy:raise ValueError('Trailing policy requires complete versioned fields.')
        elif self.profile_id=='openpine.order-group-broker.1':
            from .order_groups import OrderGroupPolicy
            if type(self) is not OrderGroupPolicy:raise ValueError('Grouped policy requires its complete versioned fields.')
        elif self.profile_id=='openpine.lot-broker.1':
            from .lots import LotBrokerPolicy
            if type(self) is not LotBrokerPolicy:raise ValueError('Lot policy requires its complete versioned fields.')
        elif self.profile_id not in ('openpine.market-broker.1','openpine.protective-broker.1','openpine.partial-broker.1','openpine.pending-entry-broker.1'):raise ValueError("Unknown broker policy.")
        from .economics import EconomicsSettings
        if type(self.settings) is not BrokerSettings and not (self.profile_id=='openpine.economics-broker.1' and type(self.settings) is EconomicsSettings):raise ValueError('Unknown broker settings.')
        for name in ('initial_capital','default_quantity','commission_percent'):
            value=getattr(self,name)
            if type(value) is not str or decimal_text(value,positive=name!='commission_percent' and not (self.profile_id=='openpine.economics-broker.1' and name=='default_quantity' and self.quantity_mode!='fixed'))!=value:raise ValueError("Policy decimals must be canonical.")
        with localcontext() as context:
            context.prec=80
            aligned=self.profile_id=='openpine.economics-broker.1' or Decimal(self.default_quantity)%Decimal(self.settings.quantity_step)==0
        if (not 0<=Decimal(self.commission_percent)<=100 or type(self.slippage_ticks) is not int
                or not 0<=self.slippage_ticks<=1_000_000 or self.slippage_ticks and self.settings.tick_size is None
                or not aligned):
            raise ValueError("Invalid commission, slippage tick metadata or quantity step.")

    @classmethod
    def from_payload(cls,value):
        if cls is BrokerPolicy and type(value) is dict and value.get('profile_id')=='openpine.performance-broker.1':
            from .performance import PerformancePolicy
            return PerformancePolicy.from_payload(value)
        if cls is BrokerPolicy and type(value) is dict and value.get('profile_id')=='openpine.risk-broker.1':
            from .risk import RiskPolicy
            return RiskPolicy.from_payload(value)
        if cls is BrokerPolicy and type(value) is dict and value.get('profile_id')=='openpine.economics-broker.1':
            from .economics import EconomicsPolicy
            return EconomicsPolicy.from_payload(value)
        if cls is BrokerPolicy and type(value) is dict and value.get('profile_id')=='openpine.trailing-broker.1':
            from .trailing import TrailingPolicy
            return TrailingPolicy.from_payload(value)
        if cls is BrokerPolicy and type(value) is dict and value.get('profile_id')=='openpine.order-group-broker.1':
            from .order_groups import OrderGroupPolicy
            return OrderGroupPolicy.from_payload(value)
        if cls is BrokerPolicy and type(value) is dict and value.get('profile_id')=='openpine.lot-broker.1':
            from .lots import LotBrokerPolicy
            return LotBrokerPolicy.from_payload(value)
        if type(value) is not dict or value.keys()!=set(cls.__dataclass_fields__):raise ValueError("Invalid broker policy fields.")
        return cls(**{**value,'settings':BrokerSettings.from_payload(value['settings'])})


@dataclass(frozen=True)
class OrderIntent:
    call_site_id: str
    kind: str
    entry_id: str
    direction: int
    quantity: str | None


@dataclass(frozen=True)
class StrategyDeclaration:
    title: str
    overlay: bool
    shorttitle: str = field(default='',init=False)
    kind: str = field(default='strategy',init=False)


@dataclass(frozen=True)
class MarketOrder:
    order_id: str
    sequence: int
    created_bar: int
    created_time: int
    intent: OrderIntent


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    entry_id: str
    direction: int
    quantity: str
    price: str
    commission: str
    bar_index: int
    time: int


@dataclass(frozen=True)
class Trade:
    trade_id: str
    entry_fill_id: str
    exit_fill_id: str
    entry_id: str
    direction: int
    quantity: str
    entry_price: str
    exit_price: str
    gross_profit: str
    commission: str
    net_profit: str
    entry_bar: int
    exit_bar: int


@dataclass(frozen=True,kw_only=True)
class StrategyRunResult(RunResult):
    simulation: dict
    primary_context: object = None
    contexts: tuple = ()
    requests: tuple = ()
    schema_version: str = field(default='openpine.run-result.4',init=False)


def strategy_policy(version):
    return {'language_version':version,'profile_id':f'openpine.strategy.v{version}.1',
        'direction_parameter':'long' if version<5 else 'direction','direction_type':'bool' if version<5 else 'named_constant',
        'when':version<6,'basis':'reconstructed' if version<3 else 'artifact_defined',
        'initial_capital':1000000 if version==5 else 100000,'require_explicit_fixed':version==6,
        'calculation':'confirmed_bar_close','fill':'next_available_primary_bar_open',
        'orders':'market_entry_and_full_close','position':'one_entry_no_pyramiding_reverse_opposite',
        'accounting':'quote_linear_signed_cash_mark_to_market','money_rounding':'decimal_half_even_per_fee_and_realized_profit',
        'last_bar':'retain_pending_and_open_position','intrabar':'none','max_commands_per_bar':16}
