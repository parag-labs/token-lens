"""TokenLens - attribute LLM cost and latency to feature/tenant/model, with budget and anomaly gates.

Give TokenLens a usage log and it tells you where your LLM spend actually goes:
which feature, tenant, or model drove the cost, plus budget breaches, cost
anomalies, and rising-cost "creep" over time. Pure logic, no required
dependencies - the same core also ships in C# and Java.

Quick start::

    from token_lens import UsageRecord, build_report

    records = [
        UsageRecord("gpt-4o", input_tokens=1200, output_tokens=300,
                    latency_ms=850, feature="search", tenant="acme"),
    ]
    report = build_report(records, dimension="feature", budget=5.00)
    print(report.total_cost, report.budget_exceeded)
"""

from __future__ import annotations

from .pricing import (
    ChainedPricing,
    FilePricing,
    ModelPrice,
    PriceEntry,
    PricingProvider,
    StaticPricing,
    UnknownModelError,
    cost_of,
)
from .tracer import (
    Anomaly,
    Creep,
    DimensionStat,
    Report,
    UsageRecord,
    aggregate,
    build_report,
    detect_anomalies,
    detect_creep,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # usage + reporting
    "UsageRecord",
    "DimensionStat",
    "Anomaly",
    "Creep",
    "Report",
    "aggregate",
    "detect_anomalies",
    "detect_creep",
    "build_report",
    # pricing
    "ModelPrice",
    "PriceEntry",
    "PricingProvider",
    "StaticPricing",
    "FilePricing",
    "ChainedPricing",
    "UnknownModelError",
    "cost_of",
]
