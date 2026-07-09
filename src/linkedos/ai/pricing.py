"""Model tiers and the price list.

═══════════════════════════════════════════════════════════════════════════════
 UPDATE FROM CURRENT PRICING — https://platform.claude.com/docs/en/pricing
 Prices below were correct as of 2026-06-24. They are not fetched at runtime.
 A stale number here means a silently wrong ledger, not a crash.
═══════════════════════════════════════════════════════════════════════════════

Cost is computed in `Decimal`, not `float`. Multiplying a token count by a per-million
rate in binary floating point accumulates error that shows up as fractions of a cent
across a month of calls, and a ledger that disagrees with the invoice is worse than no
ledger. Callers get a `float` back only at the persistence boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from linkedos.core.errors import ConfigError

PRICING_AS_OF = "2026-06-24"

#: Ledger stores six decimal places — a tenth of a cent per thousand calls.
_CENTS = Decimal("0.000001")
_PER_MILLION = Decimal(1_000_000)


class Tier(StrEnum):
    """What a task is worth spending on.

    Callers pick a tier, never a model id. Swapping the model behind a tier is then a
    one-line change here rather than a grep across the services layer.
    """

    #: Draft generation. High volume, human reviews every output before it ships.
    DRAFT = "draft"
    #: Reserved for later milestones: rewrite-on-critique, engagement analysis.
    HIGH_STAKES = "high_stakes"


TIER_MODELS: dict[Tier, str] = {
    Tier.DRAFT: "claude-haiku-4-5",
    Tier.HIGH_STAKES: "claude-sonnet-5",
}


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """USD per million tokens."""

    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal


MODEL_PRICING: dict[str, ModelPricing] = {
    # Anthropic. UPDATE FROM CURRENT PRICING.
    "claude-haiku-4-5": ModelPricing(Decimal("1.00"), Decimal("5.00")),
    "claude-sonnet-5": ModelPricing(Decimal("3.00"), Decimal("15.00")),
    "claude-opus-4-8": ModelPricing(Decimal("5.00"), Decimal("25.00")),
    # Local models run on the user's own hardware. Free in dollars, not in watts.
    "nomic-embed-text": ModelPricing(Decimal(0), Decimal(0)),
    "fake-embed": ModelPricing(Decimal(0), Decimal(0)),
}


def model_for_tier(tier: Tier) -> str:
    return TIER_MODELS[tier]


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Exact cost of one call, rounded half-up to six decimal places.

    Raises:
        ConfigError: if `model` is absent from `MODEL_PRICING`. Loud on purpose — an
            unpriced model would otherwise log a $0.00 call and quietly break the budget
            cap. Adding a model means adding its price.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ConfigError(f"negative token count for {model!r}")

    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        raise ConfigError(f"no price for model {model!r}; add it to ai.pricing.MODEL_PRICING")

    total = (
        Decimal(input_tokens) * pricing.input_usd_per_mtok
        + Decimal(output_tokens) * pricing.output_usd_per_mtok
    ) / _PER_MILLION

    return float(total.quantize(_CENTS, rounding=ROUND_HALF_UP))
