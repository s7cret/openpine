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
