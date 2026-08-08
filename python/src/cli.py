"""TokenLens CLI: summarize LLM spend from a JSONL usage log.

    python -m cli usage.jsonl --dimension feature --budget 5.00

Each line is a JSON object: {model, input_tokens, output_tokens, latency_ms, feature, tenant}.
Exits non-zero if a budget is set and exceeded -- so it works as a CI cost gate.
"""

from __future__ import annotations

import argparse
import json
import sys

from tracer import UsageRecord, build_report


def load_records(path: str) -> list[UsageRecord]:
    records = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            records.append(
                UsageRecord(
                    model=d["model"],
                    input_tokens=int(d["input_tokens"]),
                    output_tokens=int(d["output_tokens"]),
                    latency_ms=float(d.get("latency_ms", 0.0)),
                    feature=d.get("feature", "unknown"),
                    tenant=d.get("tenant", "unknown"),
                )
            )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tokenlens")
    parser.add_argument("usage_log")
    parser.add_argument("--dimension", default="feature", choices=["feature", "tenant", "model"])
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument("--anomaly-factor", type=float, default=3.0)
    args = parser.parse_args(argv)

    report = build_report(load_records(args.usage_log), args.dimension, args.budget, args.anomaly_factor)

    print(f"TokenLens report  (by {args.dimension})")
    print(f"  total: ${report.total_cost:.4f} over {report.total_calls} calls")
    for s in sorted(report.by_dimension.values(), key=lambda x: x.cost, reverse=True):
        print(f"    {s.key:<16} ${s.cost:.4f}  {s.calls} calls  {s.total_tokens} tok  {s.avg_latency_ms}ms avg")
    if report.anomalies:
        print("  anomalies:")
        for a in report.anomalies:
            print(f"    ! {a.key}: {a.metric} ${a.value:.4f} = {a.factor}x median")
    if report.budget_exceeded:
        print(f"  BUDGET EXCEEDED: ${report.total_cost:.4f} > ${args.budget:.4f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
