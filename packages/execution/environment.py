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
    from packages.contracts.strategy_ir import strategy_nodes
    needs_tick=any(n.operation=='simulation.exit_extended' for n in strategy_nodes(program))
    if uses_environment(program) or needs_tick:
        environment=source_environment(source)
        if environment.instrument is not None and type(selected) is BrokerSettings and selected.tick_size is None:
            selected=replace(selected,tick_size=environment.instrument.mintick)
        return broker_environment(environment,broker_policy(program,selected))
    return broker_policy(program,selected)
