"""Usage records and cost/latency aggregation with budget + anomaly detection.

Core value: attribute LLM spend to a dimension (feature / tenant / model) and
surface budget breaches and cost anomalies -- the numbers a FinOps/eng lead asks
for. Pure logic; no I/O, so it ports cleanly to C# and Java.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pricing import cost_of


@dataclass
class UsageRecord:
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    feature: str = "unknown"
    tenant: str = "unknown"
    timestamp: float = 0.0  # epoch seconds; used by the rolling-window creep detector

    @property
    def cost(self) -> float:
        return cost_of(self.model, self.input_tokens, self.output_tokens)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class DimensionStat:
    key: str
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_sum: float = 0.0

    @property
    def avg_latency_ms(self) -> float:
        return round(self.latency_sum / self.calls, 2) if self.calls else 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass
class Anomaly:
    key: str
    metric: str
    value: float
    baseline: float
    factor: float


@dataclass
class Creep:
    """A dimension whose cost *rate* is rising over time (gradual creep).

    The plain median detector only catches a dimension that is expensive relative
    to its peers right now. It misses one that is slowly growing but still cheaper
    than the noisy ones. Comparing a recent window against an earlier baseline
    window catches that trend.
    """

    key: str
    baseline_rate: float  # cost per second over the baseline window
    recent_rate: float    # cost per second over the recent window
    factor: float


@dataclass
class Report:
    total_cost: float
    total_calls: int
    by_dimension: dict[str, DimensionStat] = field(default_factory=dict)
    budget_exceeded: bool = False
    anomalies: list[Anomaly] = field(default_factory=list)


def aggregate(records: list[UsageRecord], dimension: str = "feature") -> dict[str, DimensionStat]:
    stats: dict[str, DimensionStat] = {}
    for r in records:
        key = getattr(r, dimension)
        s = stats.setdefault(key, DimensionStat(key=key))
        s.calls += 1
        s.input_tokens += r.input_tokens
        s.output_tokens += r.output_tokens
        s.cost = round(s.cost + r.cost, 6)
        s.latency_sum += r.latency_ms
    return stats


def detect_anomalies(stats: dict[str, DimensionStat], factor: float = 3.0) -> list[Anomaly]:
    """Flag dimensions whose cost exceeds `factor` x the median dimension cost."""
    costs = sorted(s.cost for s in stats.values())
    if len(costs) < 3:
        return []
    median = costs[len(costs) // 2]
    if median <= 0:
        return []
    out: list[Anomaly] = []
    for s in stats.values():
        if s.cost > factor * median:
            out.append(
                Anomaly(key=s.key, metric="cost", value=s.cost, baseline=median,
                        factor=round(s.cost / median, 2))
            )
    return sorted(out, key=lambda a: a.factor, reverse=True)


def detect_creep(
    records: list[UsageRecord],
    dimension: str = "feature",
    split_time: float | None = None,
    factor: float = 2.0,
) -> list[Creep]:
    """Flag dimensions whose cost *rate* rose from a baseline window to a recent one.

    Records with ``timestamp < split_time`` form the baseline window; the rest form
    the recent window. Cost is normalized by each window's duration to get a rate
    (cost/second), so uneven window lengths compare fairly. A dimension is flagged
    when its recent rate exceeds ``factor`` times its baseline rate.

    If ``split_time`` is None, the midpoint of the observed timestamp range is used.
    """
    timed = [r for r in records if r.timestamp > 0]
    if len(timed) < 2:
        return []

    lo = min(r.timestamp for r in timed)
    hi = max(r.timestamp for r in timed)
    if hi == lo:
        return []
    if split_time is None:
        split_time = (lo + hi) / 2

    base_dur = max(split_time - lo, 1e-9)
    recent_dur = max(hi - split_time, 1e-9)

    base_cost: dict[str, float] = {}
    recent_cost: dict[str, float] = {}
    for r in timed:
        key = getattr(r, dimension)
        if r.timestamp < split_time:
            base_cost[key] = base_cost.get(key, 0.0) + r.cost
        else:
            recent_cost[key] = recent_cost.get(key, 0.0) + r.cost

    out: list[Creep] = []
    for key, rc in recent_cost.items():
        base_rate = base_cost.get(key, 0.0) / base_dur
        recent_rate = rc / recent_dur
        if base_rate <= 0:
            continue  # no baseline to compare against; not "creep", it's new
        if recent_rate > factor * base_rate:
            out.append(
                Creep(
                    key=key,
                    baseline_rate=round(base_rate, 9),
                    recent_rate=round(recent_rate, 9),
                    factor=round(recent_rate / base_rate, 2),
                )
            )
    return sorted(out, key=lambda c: c.factor, reverse=True)


def build_report(
    records: list[UsageRecord],
    dimension: str = "feature",
    budget: float | None = None,
    anomaly_factor: float = 3.0,
) -> Report:
    stats = aggregate(records, dimension)
    total_cost = round(sum(s.cost for s in stats.values()), 6)
    report = Report(
        total_cost=total_cost,
        total_calls=sum(s.calls for s in stats.values()),
        by_dimension=stats,
        budget_exceeded=(budget is not None and total_cost > budget),
        anomalies=detect_anomalies(stats, anomaly_factor),
    )
    return report
