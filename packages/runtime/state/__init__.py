"""State owned by one evaluation, partitioned by written call and context."""

from dataclasses import dataclass

from packages.runtime.series import EMAState


@dataclass(frozen=True)
class KernelContext:
    call_site_id: str
    context_id: str
    ema_states: dict[tuple[str, str], EMAState]

    def ema(self) -> EMAState:
        key = (self.call_site_id, self.context_id)
        if key not in self.ema_states:
            self.ema_states[key] = EMAState()
        return self.ema_states[key]
