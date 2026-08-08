"""TokenLens tests: cost math, attribution, budget gate, anomaly detection."""

import pytest

from pricing import UnknownModelError, cost_of
from tracer import DimensionStat, UsageRecord, aggregate, build_report, detect_anomalies


def test_cost_of_known_model():
    # 1M input @ 2.50 + 1M output @ 10.00 = 12.50
    assert cost_of("gpt-4o", 1_000_000, 1_000_000) == 12.5


def test_cost_of_partial_tokens():
    assert cost_of("gpt-4o-mini", 1000, 2000) == pytest.approx(0.15 / 1000 + 0.60 / 1000 * 2, abs=1e-9)


def test_unknown_model_raises():
    with pytest.raises(UnknownModelError):
        cost_of("does-not-exist", 10, 10)


def test_aggregation_by_feature():
    records = [
        UsageRecord("gpt-4o-mini", 1000, 500, 120, feature="search"),
        UsageRecord("gpt-4o-mini", 2000, 1000, 200, feature="search"),
        UsageRecord("gpt-4o", 500, 500, 80, feature="summarize"),
    ]
    stats = aggregate(records, "feature")
    assert stats["search"].calls == 2
    assert stats["search"].input_tokens == 3000
    assert stats["summarize"].calls == 1


def test_budget_gate():
    records = [UsageRecord("gpt-4o", 1_000_000, 1_000_000, 100, feature="x")]
    report = build_report(records, budget=5.0)
    assert report.total_cost == 12.5
    assert report.budget_exceeded is True

    report_ok = build_report(records, budget=20.0)
    assert report_ok.budget_exceeded is False


def test_anomaly_detection_flags_expensive_dimension():
    stats = {
        "a": DimensionStat("a", calls=1, cost=1.0),
        "b": DimensionStat("b", calls=1, cost=1.0),
        "c": DimensionStat("c", calls=1, cost=1.0),
        "runaway": DimensionStat("runaway", calls=1, cost=10.0),
    }
    anomalies = detect_anomalies(stats, factor=3.0)
    assert any(a.key == "runaway" for a in anomalies)


def test_no_anomalies_when_uniform():
    stats = {k: DimensionStat(k, calls=1, cost=1.0) for k in ("a", "b", "c", "d")}
    assert detect_anomalies(stats, factor=3.0) == []


def test_avg_latency():
    records = [
        UsageRecord("gpt-4o-mini", 10, 10, 100, feature="f"),
        UsageRecord("gpt-4o-mini", 10, 10, 300, feature="f"),
    ]
    stats = aggregate(records, "feature")
    assert stats["f"].avg_latency_ms == 200.0
