from __future__ import annotations

from sceneforge.schema.config import BudgetConfig
from sceneforge.schema.rates import RateTable
from sceneforge.schema.responses_api import TokenUsage


class PromptBudget:
    def __init__(self, config: BudgetConfig, rates: RateTable):
        self._config = config
        self._rates = rates
        self._reserve = {(entry.model, entry.max_output_tokens): entry.reserve for entry in config.reserve_output}
        self.calls = 0
        self.credits = 0.0
        self._reserved_calls = 0
        self._reserved_credits = 0.0

    @property
    def _credit_limit(self) -> float:
        return self._rates.credits_per_prompt * (1 - self._config.safety)

    def worst(self, model: str, text_chars: int, images: int, max_output_tokens: int) -> float:
        usage = TokenUsage(input_tokens=int(text_chars / self._config.chars_per_token)
                           + images * self._config.tokens_per_image,
                           output_tokens=self._reserve.get((model, max_output_tokens), max_output_tokens))
        return self._rates.call_credits(model, usage)

    def allows(self, estimates: list[float]) -> bool:
        calls = self.calls + self._reserved_calls + len(estimates)
        credits = self.credits + self._reserved_credits + sum(estimates)
        return calls <= self._rates.calls_per_prompt and credits <= self._credit_limit

    def reserve(self, estimate: float) -> bool:
        if not self.allows([estimate]):
            return False
        self._reserved_calls += 1
        self._reserved_credits += estimate
        return True

    def settle(self, estimate: float, model: str, usage: TokenUsage | None) -> None:
        self._reserved_calls -= 1
        self._reserved_credits -= estimate
        self.calls += 1
        self.credits += estimate if usage is None else self._rates.call_credits(model, usage)
