"""Model pricing and per-call cost computation.

Pricing is metering's second half: you *count* usage, then you *rate* it against
a price book. Providers don't publish a live market feed for tokens - list prices
change a few times a year - so the industry pattern isn't a ticker, it's a
versioned, effective-dated price book behind a small provider interface:

- ``StaticPricing``  - the embedded default table (always available).
- ``FilePricing``    - a versioned JSON price book loaded from disk, with each
  entry carrying an ``effective_from`` so a usage record is rated against the
  price that was in effect at *its* timestamp (point-in-time rating).
- ``ChainedPricing`` - try providers in order (e.g. a remote catalog first) and
  fall back to the embedded table, so rating never hard-fails.

Prices are per 1M tokens (USD), matching how providers publish them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


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


def _round_cost(price: ModelPrice, input_tokens: int, output_tokens: int) -> float:
    return round(
        input_tokens / 1_000_000 * price.input_per_million
        + output_tokens / 1_000_000 * price.output_per_million,
        6,
    )


class PricingProvider:
    """Resolves a model (optionally at a point in time) to a ``ModelPrice``."""

    def price_for(self, model: str, at: float | None = None) -> ModelPrice:
        raise NotImplementedError

    def cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        at: float | None = None,
    ) -> float:
        return _round_cost(self.price_for(model, at), input_tokens, output_tokens)


class StaticPricing(PricingProvider):
    """A flat, always-current table. This is the default provider."""

    def __init__(self, prices: dict[str, ModelPrice] | None = None) -> None:
        self._prices = dict(prices if prices is not None else PRICES)

    def price_for(self, model: str, at: float | None = None) -> ModelPrice:
        price = self._prices.get(model)
        if price is None:
            raise UnknownModelError(model)
        return price


@dataclass(frozen=True)
class PriceEntry:
    model: str
    price: ModelPrice
    effective_from: float = 0.0  # unix seconds; 0 = "since forever"


class FilePricing(PricingProvider):
    """A versioned price book. Each model may carry several dated entries; a
    lookup returns the newest entry whose ``effective_from`` is at or before the
    usage timestamp, so back-dated recomputes stay correct."""

    def __init__(self, entries: list[PriceEntry]) -> None:
        by_model: dict[str, list[PriceEntry]] = {}
        for e in entries:
            by_model.setdefault(e.model, []).append(e)
        for lst in by_model.values():
            lst.sort(key=lambda e: e.effective_from)
        self._by_model = by_model

    @classmethod
    def from_json(cls, text: str) -> FilePricing:
        doc = json.loads(text)
        entries = [
            PriceEntry(
                model=row["model"],
                price=ModelPrice(
                    float(row["input_per_million"]),
                    float(row["output_per_million"]),
                ),
                effective_from=float(row.get("effective_from", 0.0)),
            )
            for row in doc["prices"]
        ]
        return cls(entries)

    @classmethod
    def from_file(cls, path: str | Path) -> FilePricing:
        return cls.from_json(Path(path).read_text(encoding="utf-8"))

    def price_for(self, model: str, at: float | None = None) -> ModelPrice:
        history = self._by_model.get(model)
        if not history:
            raise UnknownModelError(model)
        if at is None:
            return history[-1].price  # newest
        eligible = [e for e in history if e.effective_from <= at]
        chosen = eligible[-1] if eligible else history[0]
        return chosen.price


class ChainedPricing(PricingProvider):
    """Try each provider in order, fall back to the next on an unknown model.

    This is how you wire a remote catalog in production without risking billing:
    put the remote provider first and the embedded ``StaticPricing`` last, so a
    remote miss (or a provider that failed to load) still rates against defaults.
    """

    def __init__(self, *providers: PricingProvider) -> None:
        if not providers:
            raise ValueError("ChainedPricing needs at least one provider")
        self._providers = providers

    def price_for(self, model: str, at: float | None = None) -> ModelPrice:
        for provider in self._providers:
            try:
                return provider.price_for(model, at)
            except UnknownModelError:
                continue
        raise UnknownModelError(model)


# The default provider used by the module-level helper below.
DEFAULT_PROVIDER: PricingProvider = StaticPricing()


def cost_of(
    model: str,
    input_tokens: int,
    output_tokens: int,
    at: float | None = None,
    provider: PricingProvider | None = None,
) -> float:
    return (provider or DEFAULT_PROVIDER).cost(model, input_tokens, output_tokens, at)
