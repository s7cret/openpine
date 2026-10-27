"""State owned by one evaluation, partitioned by written call and context."""

from dataclasses import dataclass

from packages.runtime.series import EMAState, SMASeedEMAState


@dataclass(frozen=True)
class KernelContext:
    call_site_id: str
    context_id: str
    ema_states: dict[tuple[str, str], EMAState]
    budget: object = None
    language_version: int = 6
    result_base: str = "float"
    scalar_budget: object = None

    def ema(self, sma_seed=False) -> EMAState:
        key = (self.call_site_id, self.context_id)
        if key not in self.ema_states:
            self.ema_states[key] = SMASeedEMAState() if sma_seed else EMAState()
        return self.ema_states[key]

    def state(self, kind, factory):
        key = (self.call_site_id, self.context_id, kind)
        if key not in self.ema_states:
            self.ema_states[key] = factory()
        return self.ema_states[key]
