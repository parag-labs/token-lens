# TokenLens

**Where does your LLM spend actually go?**

TokenLens takes a usage log and tells you - attributing cost and latency to the feature, tenant, or model that caused it, flagging budget breaches and cost anomalies. Same core logic in **Python, C#, and Java**.

## The problem

Once you have more than one feature calling an LLM, your bill becomes a black box. Which feature 3×'d its spend this week? Which tenant is unprofitable? Nobody knows until finance asks. TokenLens makes spend attributable and puts a **budget gate** in CI.

## What it does

- **Cost math** from a per-model price table (USD / 1M tokens).
- **Attribution** - aggregate by `feature`, `tenant`, or `model`.
- **Budget gate** - non-zero exit if total cost exceeds a budget (drop into CI).
- **Anomaly detection** - flag any dimension whose cost exceeds N× the median.

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

## Three languages, one behavior

| Language | Tests | Run |
|----------|:-----:|-----|
| Python | 8 | `cd python && pytest -q` |
| C# (.NET 10) | 7 | `cd csharp && dotnet test` |
| Java (17+) | 7 | `cd java && mvn test` |

The core (pricing, aggregation, anomaly detection) is pure logic, so all three produce identical numbers.

## Known limitations / next

- Prices are a static table - wire to a live pricing feed for production.
- Anomaly detection is median-based; a rolling time-window baseline would catch gradual creep.
- No persistence yet - pipe real traces from an OTel exporter into the JSONL format.

Part of [parag-labs](https://github.com/parag-labs) - small, focused tools for building AI systems you can trust.
