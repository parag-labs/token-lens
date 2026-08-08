using Xunit;

namespace TokenLens.Tests;

public class TokenLensTests
{
    [Fact]
    public void CostOfKnownModel()
    {
        // 1M input @ 2.50 + 1M output @ 10.00 = 12.50
        Assert.Equal(12.5, Pricing.CostOf("gpt-4o", 1_000_000, 1_000_000));
    }

    [Fact]
    public void UnknownModelThrows()
    {
        Assert.Throws<UnknownModelException>(() => Pricing.CostOf("nope", 10, 10));
    }

    [Fact]
    public void AggregationByFeature()
    {
        var records = new List<UsageRecord>
        {
            new("gpt-4o-mini", 1000, 500, 120, "search"),
            new("gpt-4o-mini", 2000, 1000, 200, "search"),
            new("gpt-4o", 500, 500, 80, "summarize"),
        };
        var stats = Tracer.Aggregate(records, "feature");
        Assert.Equal(2, stats["search"].Calls);
        Assert.Equal(3000, stats["search"].InputTokens);
        Assert.Equal(1, stats["summarize"].Calls);
    }

    [Fact]
    public void BudgetGate()
    {
        var records = new List<UsageRecord> { new("gpt-4o", 1_000_000, 1_000_000, 100, "x") };
        var report = Tracer.BuildReport(records, budget: 5.0);
        Assert.Equal(12.5, report.TotalCost);
        Assert.True(report.BudgetExceeded);

        var ok = Tracer.BuildReport(records, budget: 20.0);
        Assert.False(ok.BudgetExceeded);
    }

    [Fact]
    public void AnomalyDetectionFlagsExpensiveDimension()
    {
        var stats = new Dictionary<string, DimensionStat>
        {
            ["a"] = new("a") { Calls = 1, Cost = 1.0 },
            ["b"] = new("b") { Calls = 1, Cost = 1.0 },
            ["c"] = new("c") { Calls = 1, Cost = 1.0 },
            ["runaway"] = new("runaway") { Calls = 1, Cost = 10.0 },
        };
        var anomalies = Tracer.DetectAnomalies(stats, 3.0);
        Assert.Contains(anomalies, a => a.Key == "runaway");
    }

    [Fact]
    public void NoAnomaliesWhenUniform()
    {
        var stats = new[] { "a", "b", "c", "d" }
            .ToDictionary(k => k, k => new DimensionStat(k) { Calls = 1, Cost = 1.0 });
        Assert.Empty(Tracer.DetectAnomalies(stats, 3.0));
    }

    [Fact]
    public void AvgLatency()
    {
        var records = new List<UsageRecord>
        {
            new("gpt-4o-mini", 10, 10, 100, "f"),
            new("gpt-4o-mini", 10, 10, 300, "f"),
        };
        var stats = Tracer.Aggregate(records, "feature");
        Assert.Equal(200.0, stats["f"].AvgLatencyMs);
    }
}
