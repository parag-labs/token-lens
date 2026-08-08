"""Tests for the pricing provider abstraction: static default, versioned
file-based point-in-time rating, and chained fallback."""

from __future__ import annotations

import json

import pytest

from pricing import (
    ChainedPricing,
    FilePricing,
    ModelPrice,
    StaticPricing,
    UnknownModelError,
    cost_of,
)


def test_static_is_the_default_and_matches_helper():
    provider = StaticPricing()
    # 1M input + 1M output on gpt-4o = 2.50 + 10.00
    assert provider.cost("gpt-4o", 1_000_000, 1_000_000) == 12.5
    assert cost_of("gpt-4o", 1_000_000, 1_000_000) == 12.5


def test_static_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        StaticPricing().price_for("does-not-exist")


def test_file_pricing_loads_a_versioned_book():
    book = json.dumps(
        {
            "currency": "USD",
            "prices": [
                {"model": "gpt-4o", "input_per_million": 2.5, "output_per_million": 10.0},
            ],
        }
    )
    provider = FilePricing.from_json(book)
    assert provider.cost("gpt-4o", 1_000_000, 0) == 2.5


def test_point_in_time_rating_picks_the_effective_version():
    # gpt-4o dropped from 2.50 -> 2.00 input at t=1000.
    book = json.dumps(
        {
            "prices": [
                {
                    "model": "gpt-4o",
                    "input_per_million": 2.5,
                    "output_per_million": 10.0,
                    "effective_from": 0,
                },
                {
                    "model": "gpt-4o",
                    "input_per_million": 2.0,
                    "output_per_million": 8.0,
                    "effective_from": 1000,
                },
            ]
        }
    )
    provider = FilePricing.from_json(book)
    # A usage event before the change rates at the old price...
    assert provider.cost("gpt-4o", 1_000_000, 0, at=500) == 2.5
    # ...and one after rates at the new price.
    assert provider.cost("gpt-4o", 1_000_000, 0, at=1500) == 2.0
    # No timestamp -> newest price.
    assert provider.cost("gpt-4o", 1_000_000, 0) == 2.0


def test_file_pricing_reads_from_disk(tmp_path):
    p = tmp_path / "prices.json"
    p.write_text(
        json.dumps({"prices": [{"model": "m", "input_per_million": 1.0, "output_per_million": 2.0}]}),
        encoding="utf-8",
    )
    provider = FilePricing.from_file(p)
    assert provider.cost("m", 1_000_000, 1_000_000) == 3.0


def test_chained_falls_back_to_the_embedded_table():
    # A "remote" book that only knows one custom model.
    remote = FilePricing([])  # empty -> knows nothing, like a failed load
    negotiated = StaticPricing({"acme-1": ModelPrice(1.0, 1.0)})
    chain = ChainedPricing(remote, negotiated, StaticPricing())

    # Custom rate card wins for its model...
    assert chain.cost("acme-1", 1_000_000, 0) == 1.0
    # ...and a standard model still resolves via the embedded default.
    assert chain.cost("gpt-4o", 1_000_000, 1_000_000) == 12.5


def test_chained_raises_when_no_provider_knows_the_model():
    chain = ChainedPricing(FilePricing([]), StaticPricing())
    with pytest.raises(UnknownModelError):
        chain.price_for("mystery-model")
