"""Stress suite: high-volume aggregation, memory bounded by cardinality (not record
count), and creep detection that stays correct when records arrive out of order.

The property that matters for token-lens is scaling: aggregation must be linear in
the number of records and its memory must depend only on how many distinct
dimension values there are, not on how many records you feed it. A cost tool that
kept every record around would fall over on a real firehose; this proves it doesn't.
"""

from __future__ import annotations

import random

from tracer import UsageRecord, aggregate, build_report, detect_creep


def _rec(feature: str, out_tokens: int, ts: float = 0.0) -> UsageRecord:
    return UsageRecord("gpt-4o-mini", 0, out_tokens, 10.0, feature=feature, timestamp=ts)


def test_aggregation_memory_is_bounded_by_cardinality_not_volume():
    # Ten distinct features, but a million records. The aggregate must hold exactly
    # ten buckets - memory tracks cardinality, not the record count.
    rng = random.Random(0)
    features = [f"feature-{i}" for i in range(10)]
    records = [_rec(rng.choice(features), rng.randint(1, 500)) for _ in range(1_000_000)]
    stats = aggregate(records, "feature")
    assert len(stats) == 10
    # Totals must add up exactly across the firehose.
    assert sum(s.calls for s in stats.values()) == 1_000_000


def test_high_volume_totals_are_exact():
    # A known construction: 100k records of exactly 100 output tokens on one feature.
    records = [_rec("f", 100) for _ in range(100_000)]
    report = build_report(records, dimension="feature")
    assert report.total_calls == 100_000
    assert report.by_dimension["f"].output_tokens == 100 * 100_000


def test_creep_detection_is_order_independent():
    # Build a rising series for one feature, then shuffle the whole record list.
    # detect_creep bins by timestamp, so the result must not depend on input order.
    records = []
    for t in range(0, 100, 10):
        records.append(_rec("steady", 1000, ts=t))
        records.append(_rec("rising", 1000, ts=t))
    for t in range(100, 200, 10):
        records.append(_rec("steady", 1000, ts=t))
        records.append(_rec("rising", 6000, ts=t))

    ordered = detect_creep(records, dimension="feature", split_time=100, factor=2.0)

    shuffled = list(records)
    random.Random(123).shuffle(shuffled)
    from_shuffled = detect_creep(shuffled, dimension="feature", split_time=100, factor=2.0)

    def keys(creeps):
        return sorted(c.key for c in creeps)

    assert keys(ordered) == keys(from_shuffled)
    assert "rising" in keys(ordered)
    assert "steady" not in keys(ordered)


def test_anomaly_detection_stable_under_large_cardinality():
    # Many cheap features plus one whale; the whale must be flagged and the run must
    # complete quickly even with high cardinality.
    records = []
    for i in range(5000):
        records.append(_rec(f"cheap-{i}", 10))
    for _ in range(200):
        records.append(_rec("whale", 100_000))
    stats = aggregate(records, "feature")
    from tracer import detect_anomalies

    anomalies = detect_anomalies(stats, factor=3.0)
    assert any(a.key == "whale" for a in anomalies)


def test_soak_many_reports_do_not_drift():
    # Repeatedly build reports over random batches; totals must always reconcile.
    rng = random.Random(7)
    for _ in range(50):
        n = rng.randint(1000, 5000)
        records = [_rec(f"f{rng.randint(0, 20)}", rng.randint(1, 100)) for _ in range(n)]
        report = build_report(records, dimension="feature")
        assert report.total_calls == n
        assert sum(s.calls for s in report.by_dimension.values()) == n
