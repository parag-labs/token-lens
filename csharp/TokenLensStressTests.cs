using Xunit;

namespace TokenLens.Tests;

// Stress suite: prove token-lens's scaling properties in the C# port - aggregation
// memory bounded by dimension cardinality (not record count), exact high-volume
// totals, and order-independent creep detection.
public class TokenLensStressTests
{
    private static UsageRecord Rec(string feature, long outTokens, double ts = 0.0)
        => new("gpt-4o-mini", 0, outTokens, 10.0, feature, "t", ts);

    [Fact]
    public void AggregationMemoryIsBoundedByCardinalityNotVolume()
    {
        var rng = new Random(0);
        var features = Enumerable.Range(0, 10).Select(i => $"feature-{i}").ToArray();
        var records = new List<UsageRecord>(1_000_000);
        for (var i = 0; i < 1_000_000; i++)
        {
            records.Add(Rec(features[rng.Next(features.Length)], rng.Next(1, 500)));
        }

        var stats = Tracer.Aggregate(records, "feature");
        Assert.Equal(10, stats.Count);
        Assert.Equal(1_000_000, stats.Values.Sum(s => s.Calls));
    }

    [Fact]
    public void HighVolumeTotalsAreExact()
    {
        var records = Enumerable.Range(0, 100_000).Select(_ => Rec("f", 100)).ToList();
        var report = Tracer.BuildReport(records, dimension: "feature");
        Assert.Equal(100_000, report.TotalCalls);
        Assert.Equal(100L * 100_000, report.ByDimension["f"].OutputTokens);
    }

    [Fact]
    public void CreepDetectionIsOrderIndependent()
    {
        var records = new List<UsageRecord>();
        for (var t = 0; t < 100; t += 10)
        {
            records.Add(Rec("steady", 1000, t));
            records.Add(Rec("rising", 1000, t));
        }

        for (var t = 100; t < 200; t += 10)
        {
            records.Add(Rec("steady", 1000, t));
            records.Add(Rec("rising", 6000, t));
        }

        var ordered = Tracer.DetectCreep(records, "feature", 100, 2.0).Select(c => c.Key).OrderBy(k => k).ToList();

        var shuffled = records.OrderBy(_ => Guid.NewGuid()).ToList();
        var fromShuffled = Tracer.DetectCreep(shuffled, "feature", 100, 2.0).Select(c => c.Key).OrderBy(k => k).ToList();

        Assert.Equal(ordered, fromShuffled);
        Assert.Contains("rising", ordered);
        Assert.DoesNotContain("steady", ordered);
    }

    [Fact]
    public void AnomalyDetectionStableUnderLargeCardinality()
    {
        var records = new List<UsageRecord>();
        for (var i = 0; i < 5000; i++)
        {
            records.Add(Rec($"cheap-{i}", 10));
        }

        for (var i = 0; i < 200; i++)
        {
            records.Add(Rec("whale", 100_000));
        }

        var stats = Tracer.Aggregate(records, "feature");
        var anomalies = Tracer.DetectAnomalies(stats, 3.0);
        Assert.Contains(anomalies, a => a.Key == "whale");
    }
}
