namespace TokenLens;

public sealed record UsageRecord(
    string Model,
    long InputTokens,
    long OutputTokens,
    double LatencyMs,
    string Feature = "unknown",
    string Tenant = "unknown")
{
    public double Cost => Pricing.CostOf(Model, InputTokens, OutputTokens);
    public long TotalTokens => InputTokens + OutputTokens;
}

public sealed class DimensionStat(string key)
{
    public string Key { get; } = key;
    public int Calls { get; set; }
    public long InputTokens { get; set; }
    public long OutputTokens { get; set; }
    public double Cost { get; set; }
    public double LatencySum { get; set; }

    public double AvgLatencyMs => Calls == 0 ? 0.0 : Math.Round(LatencySum / Calls, 2);
    public long TotalTokens => InputTokens + OutputTokens;
}

public sealed record Anomaly(string Key, string Metric, double Value, double Baseline, double Factor);

public sealed class Report
{
    public double TotalCost { get; init; }
    public int TotalCalls { get; init; }
    public Dictionary<string, DimensionStat> ByDimension { get; init; } = new();
    public bool BudgetExceeded { get; init; }
    public List<Anomaly> Anomalies { get; init; } = [];
}

/// <summary>Aggregates usage by a dimension and flags budget breaches + cost anomalies.</summary>
public static class Tracer
{
    public static Dictionary<string, DimensionStat> Aggregate(
        IEnumerable<UsageRecord> records, string dimension = "feature")
    {
        var stats = new Dictionary<string, DimensionStat>();
        foreach (var r in records)
        {
            var key = Dimension(r, dimension);
            if (!stats.TryGetValue(key, out var s))
            {
                s = new DimensionStat(key);
                stats[key] = s;
            }

            s.Calls++;
            s.InputTokens += r.InputTokens;
            s.OutputTokens += r.OutputTokens;
            s.Cost = Math.Round(s.Cost + r.Cost, 6);
            s.LatencySum += r.LatencyMs;
        }

        return stats;
    }

    public static List<Anomaly> DetectAnomalies(Dictionary<string, DimensionStat> stats, double factor = 3.0)
    {
        var costs = stats.Values.Select(s => s.Cost).OrderBy(c => c).ToList();
        if (costs.Count < 3)
        {
            return [];
        }

        var median = costs[costs.Count / 2];
        if (median <= 0)
        {
            return [];
        }

        return stats.Values
            .Where(s => s.Cost > factor * median)
            .Select(s => new Anomaly(s.Key, "cost", s.Cost, median, Math.Round(s.Cost / median, 2)))
            .OrderByDescending(a => a.Factor)
            .ToList();
    }

    public static Report BuildReport(
        List<UsageRecord> records, string dimension = "feature",
        double? budget = null, double anomalyFactor = 3.0)
    {
        var stats = Aggregate(records, dimension);
        var totalCost = Math.Round(stats.Values.Sum(s => s.Cost), 6);
        return new Report
        {
            TotalCost = totalCost,
            TotalCalls = stats.Values.Sum(s => s.Calls),
            ByDimension = stats,
            BudgetExceeded = budget is not null && totalCost > budget,
            Anomalies = DetectAnomalies(stats, anomalyFactor),
        };
    }

    private static string Dimension(UsageRecord r, string dimension) => dimension switch
    {
        "feature" => r.Feature,
        "tenant" => r.Tenant,
        "model" => r.Model,
        _ => throw new ArgumentException($"unknown dimension '{dimension}'"),
    };
}
