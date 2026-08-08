"""Model pricing and per-call cost computation.

Prices are per 1M tokens (USD), matching how providers publish them. Keep the
table small and explicit; it's config, not logic.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPrice:
    input_per_million: float
    output_per_million: float


# Illustrative list prices (USD / 1M tokens). Update as vendors change them.
PRICES: dict[str, ModelPrice] = {
    "gpt-4o": ModelPrice(2.50, 10.00),
    "gpt-4o-mini": ModelPrice(0.15, 0.60),
    "o3-mini": ModelPrice(1.10, 4.40),
    "claude-3.7-sonnet": ModelPrice(3.00, 15.00),
    "claude-3.5-haiku": ModelPrice(0.80, 4.00),
    "llama-3.3-70b": ModelPrice(0.20, 0.20),
}


class UnknownModelError(KeyError):
    pass


def cost_of(model: str, input_tokens: int, output_tokens: int) -> float:
    price = PRICES.get(model)
    if price is None:
        raise UnknownModelError(model)
    return round(
        input_tokens / 1_000_000 * price.input_per_million
        + output_tokens / 1_000_000 * price.output_per_million,
        6,
    )
