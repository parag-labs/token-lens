# TokenLens

**Where does your LLM spend actually go?**

TokenLens takes a usage log and tells you - attributing cost and latency to the feature, tenant, or model that caused it, flagging budget breaches and cost anomalies. Same core logic in **Python, C#, and Java**.

## The problem

Once you have more than one feature calling an LLM, your bill becomes a black box. Which feature 3×'d its spend this week? Which tenant is unprofitable? Nobody knows until finance asks. TokenLens makes spend attributable and puts a **budget gate** in CI.

## What it does

- **Cost math** from a per-model price table (USD / 1M tokens).
- **Attribution** - aggregate by `feature`, `tenant`, or `model`.
- **Budget gate** - non-zero exit if total cost exceeds a budget (drop into CI).
- **Anomaly detection** - flag any dimension whose cost exceeds N× the median (expensive relative to its peers right now).
- **Creep detection** - compare a recent time window against an earlier baseline (cost rate, $/sec) to catch a dimension that's slowly ramping up even while it's still cheaper than the noisy ones.
- **OpenTelemetry ingest** - map real `gen_ai.*` GenAI spans into the JSONL usage format, so you feed live traces in instead of hand-writing the log.

## Run it (Python)

```bash
cd python
python src/cli.py sample-usage.jsonl --dimension feature --budget 0.50
```

```
TokenLens report  (by feature)
  total: $0.3201 over 5 calls
    report-gen       $0.2100  1 calls  38000 tok  1400.0ms avg
    summarize        $0.1075  2 calls  26500 tok  790.0ms avg
    search           $0.0026  2 calls  11200 tok  200.0ms avg
```

Add `--creep-factor 2.0` to also flag dimensions whose recent cost rate is rising over time (needs a `timestamp` on each record).

## Feeding real traces (OpenTelemetry)

If your app emits OpenTelemetry GenAI spans, wire the exporter into your tracer and it appends usage records straight to the JSONL log:

```python
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from otel_exporter import TokenLensSpanExporter

provider.add_span_processor(BatchSpanProcessor(TokenLensSpanExporter("usage.jsonl")))
```

It reads the standard `gen_ai.request.model` / `gen_ai.usage.*` attributes (plus `prompt`/`completion` aliases) and derives latency and timestamp from the span. No OTel SDK is required to use the mapping helpers in tests.

## Three languages, one behavior

| Language | Tests | Run |
|----------|:-----:|-----|
| Python | 23 | `cd python && pytest -q` |
| C# (.NET 10) | 15 | `cd csharp && dotnet test` |
| Java (17+) | 10 | `cd java && mvn test` |

The core cost/aggregation logic - including creep detection and the pricing
providers below - is pure and identical across all three; the OTel adapter is
Python-side.

## Pricing providers

Costs aren't rated against a live market feed - there isn't one for tokens; list
prices change a few times a year. token-lens follows the metering pattern real
billing systems use: count usage, then rate it against a versioned *price book*
behind a small provider interface.

- `StaticPricing` - the embedded default table (always available).
- `FilePricing` - a versioned JSON price book (see `prices.sample.json`) where
  each entry carries an `effective_from`, so a usage record is rated against the
  price in effect at *its* `timestamp` (point-in-time rating for back-dated
  recomputes).
- `ChainedPricing` - try providers in order and fall back to the embedded table,
  so wiring a remote catalog never risks a hard billing failure.

`cost_of(model, in, out)` still works unchanged and uses `StaticPricing` by
default. The provider model is mirrored across all three languages.

Part of [parag-labs](https://github.com/parag-labs) - small, focused tools for building AI systems you can trust.
