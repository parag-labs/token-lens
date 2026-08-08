"""TokenLens CLI: summarize LLM spend from a JSONL usage log.

    python -m cli usage.jsonl --dimension feature --budget 5.00

Each line is a JSON object: {model, input_tokens, output_tokens, latency_ms, feature, tenant}.
Exits non-zero if a budget is set and exceeded -- so it works as a CI cost gate.
"""

from __future__ import annotations

import argparse
import json
import sys

from tracer import UsageRecord, build_report, detect_creep


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
                    timestamp=float(d.get("timestamp", 0.0)),
                )
            )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tokenlens")
    parser.add_argument("usage_log")
    parser.add_argument("--dimension", default="feature", choices=["feature", "tenant", "model"])
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument("--anomaly-factor", type=float, default=3.0)
    parser.add_argument("--creep-factor", type=float, default=2.0,
                        help="flag a dimension whose recent cost rate exceeds this x its baseline")
    args = parser.parse_args(argv)

    records = load_records(args.usage_log)
    report = build_report(records, args.dimension, args.budget, args.anomaly_factor)

    print(f"TokenLens report  (by {args.dimension})")
    print(f"  total: ${report.total_cost:.4f} over {report.total_calls} calls")
    for s in sorted(report.by_dimension.values(), key=lambda x: x.cost, reverse=True):
        print(f"    {s.key:<16} ${s.cost:.4f}  {s.calls} calls  {s.total_tokens} tok  {s.avg_latency_ms}ms avg")
    if report.anomalies:
        print("  anomalies (expensive vs peers):")
        for a in report.anomalies:
            print(f"    ! {a.key}: {a.metric} ${a.value:.4f} = {a.factor}x median")

    creeps = detect_creep(records, args.dimension, factor=args.creep_factor)
    if creeps:
        print("  creep (cost rate rising over time):")
        for c in creeps:
            print(f"    ^ {c.key}: {c.factor}x baseline rate "
                  f"({c.baseline_rate:.2e} -> {c.recent_rate:.2e} $/s)")

    if report.budget_exceeded:
        print(f"  BUDGET EXCEEDED: ${report.total_cost:.4f} > ${args.budget:.4f}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
