"""Benchmark harness for token-lens.

Measures the two scaling properties that decide whether a cost/observability tool
survives a real firehose:

1. Aggregation throughput - records processed per second, and that it stays linear
   as the record count grows (no quadratic surprise).
2. Memory vs cardinality - peak memory depends on the number of distinct dimension
   values, not the number of records. A million records over ten features costs the
   same as a thousand records over ten features.

Run: python bench/benchmark.py
"""

from __future__ import annotations

import gc
import json
import random
import sys
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python" / "src"))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from tracer import UsageRecord, aggregate

RESULTS = Path(__file__).resolve().parent / "results"
RESULTS.mkdir(exist_ok=True)


def make_records(n: int, cardinality: int, seed: int = 0) -> list[UsageRecord]:
    rng = random.Random(seed)
    feats = [f"feature-{i}" for i in range(cardinality)]
    return [
        UsageRecord("gpt-4o-mini", 0, rng.randint(1, 500), 10.0, feature=rng.choice(feats))
        for _ in range(n)
    ]


def bench_throughput() -> dict:
    volumes = [50_000, 100_000, 250_000, 500_000, 1_000_000]
    times, rates = [], []
    for n in volumes:
        records = make_records(n, cardinality=50)
        gc.disable()
        start = time.perf_counter()
        aggregate(records, "feature")
        elapsed = time.perf_counter() - start
        gc.enable()
        times.append(elapsed)
        rates.append(n / elapsed)

    # Plot throughput (records/sec) vs volume. Aggregation does O(1) work per record
    # algorithmically, but measured per-record cost rises modestly at very high
    # volume as the working set outgrows CPU cache - so this is throughput, presented
    # honestly, not a "perfectly flat" claim.
    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot([v / 1000 for v in volumes], [r / 1000 for r in rates], "o-", color="tab:blue")
    ax.set_xlabel("records aggregated (thousands)")
    ax.set_ylabel("throughput (thousand records/sec)")
    ax.set_title("token-lens: aggregation throughput (single-threaded Python)")
    ax.set_ylim(bottom=0)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "throughput.png", dpi=110)
    plt.close(fig)

    return {
        "volumes": volumes,
        "elapsed_ms": [round(t * 1000, 2) for t in times],
        "records_per_sec": [int(r) for r in rates],
    }


def _peak_kb(records: list[UsageRecord]) -> float:
    gc.collect()
    tracemalloc.start()
    stats = aggregate(records, "feature")  # noqa: F841 - keep alive while measured
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak / 1024


def bench_memory_vs_cardinality() -> dict:
    # Hold the record count fixed; vary how many distinct features they map to.
    n = 200_000
    cardinalities = [10, 50, 100, 500, 1000, 5000]
    mem = [_peak_kb(make_records(n, c)) for c in cardinalities]

    fig, ax = plt.subplots(figsize=(6.5, 4))
    ax.plot(cardinalities, mem, "o-", color="tab:purple")
    ax.set_xlabel("distinct dimension values (cardinality)")
    ax.set_ylabel("peak aggregate memory (KB)")
    ax.set_title(f"token-lens: memory tracks cardinality, not volume ({n:,} records)")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "memory_vs_cardinality.png", dpi=110)
    plt.close(fig)

    return {
        "records": n,
        "cardinalities": cardinalities,
        "peak_kb": [round(x, 1) for x in mem],
    }


def main() -> None:
    summary = {
        "throughput": bench_throughput(),
        "memory": bench_memory_vs_cardinality(),
    }
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
