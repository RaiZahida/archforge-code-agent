"""Token and cost accounting with a hard budget guard."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import Price, price_for


def is_peak(now: datetime | None = None) -> bool:
    """DeepSeek peak hours: 01-04 and 06-10 UTC, Monday to Friday."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if now.weekday() >= 5:
        return False
    return 1 <= now.hour < 4 or 6 <= now.hour < 10


class BudgetExceeded(RuntimeError):
    pass


@dataclass
class CostTracker:
    model: str
    budget_usd: float = 1.0
    calls: int = 0
    cache_hit_tokens: int = 0
    cache_miss_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    history: list[dict] = field(default_factory=list)

    @property
    def price(self) -> Price:
        return price_for(self.model)

    def add(self, usage: dict, now: datetime | None = None) -> float:
        """Record one API response's `usage` block and return its cost in USD."""
        prompt = int(usage.get("prompt_tokens", 0))
        hit = int(usage.get("prompt_cache_hit_tokens", 0))
        miss = int(usage.get("prompt_cache_miss_tokens", prompt - hit))
        out = int(usage.get("completion_tokens", 0))
        factor = 1.0 if is_peak(now) else 0.5
        p = self.price
        cost = factor * (hit * p.cache_hit + miss * p.cache_miss + out * p.output) / 1_000_000
        self.calls += 1
        self.cache_hit_tokens += hit
        self.cache_miss_tokens += miss
        self.output_tokens += out
        self.cost_usd += cost
        self.history.append({"call": self.calls, "hit": hit, "miss": miss, "out": out,
                             "cost": round(cost, 6), "peak": factor == 1.0})
        return cost

    def check(self) -> None:
        if self.cost_usd >= self.budget_usd:
            raise BudgetExceeded(f"budget ${self.budget_usd:.2f} reached (spent ${self.cost_usd:.4f})")

    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.cost_usd)

    def summary(self) -> dict:
        total_in = self.cache_hit_tokens + self.cache_miss_tokens
        return {
            "model": self.model,
            "api_calls": self.calls,
            "input_tokens": total_in,
            "cache_hit_tokens": self.cache_hit_tokens,
            "cache_hit_rate": round(self.cache_hit_tokens / total_in, 3) if total_in else 0.0,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 4),
            "budget_usd": self.budget_usd,
        }
