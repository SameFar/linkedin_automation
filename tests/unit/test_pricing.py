"""Cost math. If this file is wrong, the ledger lies and the budget cap is decorative."""

from __future__ import annotations

import pytest

from linkedos.ai.pricing import MODEL_PRICING, TIER_MODELS, Tier, cost_usd, model_for_tier
from linkedos.core.errors import ConfigError


def test_one_million_input_tokens_costs_the_input_rate() -> None:
    assert cost_usd("claude-haiku-4-5", 1_000_000, 0) == 1.00
    assert cost_usd("claude-sonnet-5", 1_000_000, 0) == 3.00


def test_one_million_output_tokens_costs_the_output_rate() -> None:
    assert cost_usd("claude-haiku-4-5", 0, 1_000_000) == 5.00
    assert cost_usd("claude-sonnet-5", 0, 1_000_000) == 15.00


def test_input_and_output_are_priced_separately_and_summed() -> None:
    # 1000 * $1/Mtok = $0.001; 500 * $5/Mtok = $0.0025
    assert cost_usd("claude-haiku-4-5", 1_000, 500) == 0.0035

    # 1234 * $3/Mtok = $0.003702; 567 * $15/Mtok = $0.008505
    assert cost_usd("claude-sonnet-5", 1_234, 567) == 0.012207


def test_a_realistic_draft_run_of_three_variants() -> None:
    per_variant = cost_usd("claude-haiku-4-5", 1_800, 260)
    assert per_variant == 0.003100
    assert round(per_variant * 3, 6) == 0.0093


def test_zero_tokens_costs_nothing() -> None:
    assert cost_usd("claude-haiku-4-5", 0, 0) == 0.0


def test_local_models_are_priced_at_zero_not_missing() -> None:
    # A missing price raises; an explicit zero does not. Local embeddings must land
    # in the ledger as $0 rows, not as an exception.
    assert cost_usd("nomic-embed-text", 10_000, 0) == 0.0
    assert cost_usd("fake-embed", 10_000, 0) == 0.0


def test_smallest_representable_cost_survives_rounding() -> None:
    # One input token on Haiku is $0.000001 — exactly the ledger's resolution.
    assert cost_usd("claude-haiku-4-5", 1, 0) == 0.000001


def test_sub_resolution_cost_rounds_half_up_not_to_zero() -> None:
    # 1 token * $5/Mtok = $0.000005, which is representable. Two tokens of a
    # hypothetical half-price model would not be, so assert the quantum directly.
    assert cost_usd("claude-haiku-4-5", 0, 1) == 0.000005


def test_unknown_model_raises_rather_than_billing_zero() -> None:
    with pytest.raises(ConfigError, match="no price for model"):
        cost_usd("claude-does-not-exist", 100, 100)


def test_negative_tokens_are_rejected() -> None:
    with pytest.raises(ConfigError, match="negative token count"):
        cost_usd("claude-haiku-4-5", -1, 0)


def test_draft_tier_routes_to_a_haiku_class_model() -> None:
    assert model_for_tier(Tier.DRAFT) == "claude-haiku-4-5"


def test_high_stakes_tier_routes_to_a_sonnet_class_model() -> None:
    assert model_for_tier(Tier.HIGH_STAKES) == "claude-sonnet-5"


def test_every_routable_model_has_a_price() -> None:
    # Guards the failure where someone repoints a tier at a model nobody priced.
    for model in TIER_MODELS.values():
        assert model in MODEL_PRICING, f"{model} is routable but unpriced"
