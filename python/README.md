# TokenLens (Python)

**Where does your LLM spend actually go?**

`token-lens` takes a usage log and tells you — attributing cost and latency to the
**feature**, **tenant**, or **model** that caused it, and flagging budget breaches,
cost anomalies, and rising-cost "creep" over time. Pure logic, **zero required
dependencies**. The same core also ships in C# and Java.

```bash
pip install token-lens
```

## Library

```python
from token_lens import UsageRecord, build_report, detect_creep

records = [
    UsageRecord("gpt-4o", input_tokens=1200, output_tokens=300,
                latency_ms=850, feature="search", tenant="acme"),
    UsageRecord("gpt-4o-mini", input_tokens=400, output_tokens=90,
                latency_ms=210, feature="autocomplete", tenant="acme"),
]

report = build_report(records, dimension="feature", budget=5.00)
print(f"total ${report.total_cost:.4f} over {report.total_calls} calls")
for stat in report.by_dimension.values():
    print(stat.key, stat.cost, stat.calls)

if report.budget_exceeded:
    raise SystemExit("over budget")
```

## CLI (use it as a CI cost gate)

```bash
token-lens usage.jsonl --dimension feature --budget 5.00
# exits non-zero if the budget is exceeded
```

Each line of `usage.jsonl` is a JSON object:
`{model, input_tokens, output_tokens, latency_ms, feature, tenant}`.

## What it does

- **Cost math** from a per-model price table (USD per 1M tokens), with a
  point-in-time price book (`FilePricing`) and a fallback chain (`ChainedPricing`).
- **Attribution** — aggregate cost/latency/tokens by `feature`, `tenant`, or `model`.
- **Budget gate** — set a ceiling; the CLI exits non-zero when you cross it.
- **Anomaly detection** — flag a dimension that is expensive versus its peers.
- **Creep detection** — flag a dimension whose cost *rate* is rising over time.
- **OpenTelemetry adapter** — turn `gen_ai.*` GenAI spans into the usage log
  (optional extra: `pip install token-lens[otel]`).

## License

MIT — see the repository: <https://github.com/parag-labs/token-lens>
