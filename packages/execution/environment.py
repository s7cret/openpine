"""Bind the broker to the same frozen instrument facts visible to Pine."""

from dataclasses import replace
from packages.contracts import Diagnostic,OpenPineError


def broker_environment(environment,policy):
    if policy is None or environment.instrument is None:return policy
    instrument=environment.instrument;tick=instrument.mintick
    if instrument.pointvalue!='1' or policy.settings.tick_size not in (None,tick):
        raise OpenPineError(Diagnostic('INPUT_INVALID','ENVIRONMENT_BROKER',
            'Broker settings conflict with the pinned instrument; this broker requires pointvalue=1 and one matching tick size.'))
    return replace(policy,settings=replace(policy.settings,tick_size=tick))


def source_broker(program,source,policy):
    from packages.contracts.environment import uses_environment,source_environment
    return broker_environment(source_environment(source),policy) if uses_environment(program) else policy


def source_policy(program,source,settings=None):
    """Bind an instrument tick before constructing a slippage-bearing policy."""
    from packages.contracts.environment import uses_environment,source_environment
    from packages.contracts.strategy_ir import broker_policy
    from packages.contracts.trading import BrokerSettings
    selected=settings or BrokerSettings()
    from packages.contracts.economics import OPERATION,EconomicsSettings,validate_rules
    from packages.contracts.risk import program_rules,RiskPolicy
    risk=program_rules(program)
    from packages.contracts.performance import PerformancePolicy,uses_performance
    economic=uses_performance(program) or bool(risk) or bool(program.statements and getattr(program.statements[0],'operation',None) in (OPERATION,'simulation.strategy_cycle')) or type(selected) is EconomicsSettings
    rules=getattr(source,'manifest',{}).get('provenance',{}).get('trading_rules') if economic else None
    if rules is not None:
        try:
            rules=validate_rules(rules)
            if settings is not None and selected.quantity_step!=rules['quantity_step']:raise ValueError('Quantity step conflicts with pinned snapshot rules.')
            if selected.tick_size is not None and rules['tick_size'] is not None and selected.tick_size!=rules['tick_size']:raise ValueError('Tick size conflicts with pinned snapshot rules.')
            minima={}
            for n in ('minimum_quantity','minimum_notional'):
                explicit=getattr(selected,n,None);pinned=rules[n]
                if explicit is not None and pinned is not None and explicit!=pinned:raise ValueError('Minimum constraint conflicts with pinned snapshot rules.')
                minima[n]=pinned if pinned is not None else explicit
            selected=EconomicsSettings(selected.tick_size or rules['tick_size'],rules['quantity_step'],selected.money_decimals,**minima)
        except ValueError as error:raise OpenPineError(Diagnostic('INPUT_INVALID','ECONOMICS_METADATA',str(error),language_version=program.identity.language_version)) from error
    from packages.contracts.strategy_ir import strategy_nodes
    def finish(policy):
        if type(policy) in (RiskPolicy,PerformancePolicy):
            try:return replace(policy,timeframe=source.identity.timeframe)
            except ValueError as error:raise OpenPineError(Diagnostic('PROFILE_UNCOVERED','RISK_TIMEFRAME',str(error),language_version=program.identity.language_version)) from error
        return policy
    needs_tick=any(n.operation=='simulation.exit_extended' for n in strategy_nodes(program))
    if uses_environment(program) or needs_tick or economic:
        environment=source_environment(source)
        if environment.instrument is not None and isinstance(selected,BrokerSettings) and selected.tick_size is None:
            selected=replace(selected,tick_size=environment.instrument.mintick)
        return finish(broker_environment(environment,broker_policy(program,selected)))
    return finish(broker_policy(program,selected))
