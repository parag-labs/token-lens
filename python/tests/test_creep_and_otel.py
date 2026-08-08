"""Tests for the rolling-window creep detector and the OTel -> JSONL exporter."""

import io
import json

from otel_exporter import (
    export_spans_to_jsonl,
    span_to_record,
    spans_to_records,
)
from tracer import UsageRecord, detect_creep

# ---- rolling-window creep detection ----

def _rec(feature, cost_model_tokens, ts):
    # gpt-4o-mini: 0.15 in / 0.60 out per 1M. Use output tokens to dial cost.
    return UsageRecord("gpt-4o-mini", 0, cost_model_tokens, 10.0, feature=feature, timestamp=ts)


def test_creep_flags_a_rising_dimension():
    # 'search' is flat; 'summarize' ramps up in the recent window.
    records = []
    # baseline window t=0..99
    for t in range(0, 100, 10):
        records.append(_rec("search", 1000, t))
        records.append(_rec("summarize", 1000, t))
    # recent window t=100..199: summarize spikes 5x, search flat
    for t in range(100, 200, 10):
        records.append(_rec("search", 1000, t))
        records.append(_rec("summarize", 5000, t))

    creeps = detect_creep(records, dimension="feature", split_time=100, factor=2.0)
    keys = [c.key for c in creeps]
    assert "summarize" in keys
    assert "search" not in keys
    assert creeps[0].factor >= 2.0


def test_creep_ignores_brand_new_dimension():
    # 'new' only appears in the recent window -> no baseline, not "creep".
    records = [_rec("steady", 1000, t) for t in range(0, 100, 10)]
    records += [_rec("steady", 1000, t) for t in range(100, 200, 10)]
    records += [_rec("new", 9000, t) for t in range(100, 200, 10)]
    creeps = detect_creep(records, dimension="feature", split_time=100)
    assert all(c.key != "new" for c in creeps)


def test_creep_needs_timestamps():
    # Records without timestamps can't be windowed.
    records = [UsageRecord("gpt-4o-mini", 10, 10, 5.0, feature="x") for _ in range(5)]
    assert detect_creep(records) == []


def test_creep_rate_normalizes_uneven_windows():
    # Baseline: 5 calls spread over a long window (low rate). Recent: 10 calls
    # packed into a short window (high rate). Rate normalization flags the recent
    # burst even though neither window's raw cost dwarfs the other.
    records = [_rec("f", 1000, t) for t in range(0, 100, 20)]        # 5 calls over 0..80
    records += [_rec("f", 1000, t) for t in range(101, 111)]         # 10 calls over 101..110
    creeps = detect_creep(records, dimension="feature", split_time=100, factor=1.5)
    # baseline rate ~ 5/100 = 0.05/s; recent rate ~ 10/10 = 1.0/s -> ~20x
    assert any(c.key == "f" for c in creeps)


# ---- OTel span -> UsageRecord mapping ----

class FakeSpan:
    def __init__(self, attributes, start_time=None, end_time=None):
        self.attributes = attributes
        self.start_time = start_time
        self.end_time = end_time


def test_span_to_record_maps_genai_attributes():
    span = FakeSpan(
        {
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 1200,
            "gen_ai.usage.output_tokens": 300,
            "tokenlens.feature": "summarize",
            "tokenlens.tenant": "acme",
        },
        start_time=1_000_000_000,          # 1.0s in ns
        end_time=1_000_000_000 + 820_000_000,  # +820ms
    )
    rec = span_to_record(span)
    assert rec.model == "gpt-4o"
    assert rec.input_tokens == 1200
    assert rec.output_tokens == 300
    assert rec.feature == "summarize"
    assert rec.tenant == "acme"
    assert abs(rec.latency_ms - 820.0) < 1e-6
    assert abs(rec.timestamp - 1.0) < 1e-6


def test_span_to_record_supports_prompt_completion_aliases():
    span = FakeSpan({
        "gen_ai.response.model": "claude-3.5-haiku",
        "gen_ai.usage.prompt_tokens": 50,
        "gen_ai.usage.completion_tokens": 20,
    })
    rec = span_to_record(span)
    assert rec.model == "claude-3.5-haiku"
    assert rec.input_tokens == 50
    assert rec.output_tokens == 20


def test_non_genai_span_is_ignored():
    span = FakeSpan({"http.method": "GET"})
    assert span_to_record(span) is None
    assert spans_to_records([span]) == []


def test_write_jsonl_roundtrips_into_the_usage_format():
    spans = [
        FakeSpan({"gen_ai.request.model": "gpt-4o-mini",
                  "gen_ai.usage.input_tokens": 10,
                  "gen_ai.usage.output_tokens": 5,
                  "feature": "search"}),
        FakeSpan({"http.method": "POST"}),  # ignored
    ]
    buf = io.StringIO()
    n = export_spans_to_jsonl(spans, buf)
    assert n == 1
    line = buf.getvalue().strip()
    obj = json.loads(line)
    assert obj["model"] == "gpt-4o-mini"
    assert obj["feature"] == "search"
    assert obj["output_tokens"] == 5
