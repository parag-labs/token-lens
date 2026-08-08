namespace TokenLens;

public sealed record UsageRecord(
    string Model,
    long InputTokens,
    long OutputTokens,
    double LatencyMs,
    string Feature = "unknown",
    string Tenant = "unknown",
    double Timestamp = 0.0)  // epoch seconds; used by the rolling-window creep detector
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

/// <summary>
/// A dimension whose cost <i>rate</i> is rising over time. The median detector only
/// catches something expensive relative to its peers right now; comparing a recent
/// window against an earlier baseline window catches a slow upward trend.
/// </summary>
public sealed record Creep(string Key, double BaselineRate, double RecentRate, double Factor);

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

    /// <summary>
    /// Flag dimensions whose cost rate rose from a baseline window to a recent one.
    /// Records with Timestamp &lt; splitTime form the baseline; the rest form the
    /// recent window. Cost is normalized by each window's duration (cost/second) so
    /// uneven windows compare fairly. When splitTime is null, the midpoint of the
    /// observed timestamp range is used.
    /// </summary>
    public static List<Creep> DetectCreep(
        List<UsageRecord> records, string dimension = "feature",
        double? splitTime = null, double factor = 2.0)
    {
        var timed = records.Where(r => r.Timestamp > 0).ToList();
        if (timed.Count < 2)
        {
            return [];
        }

        var lo = timed.Min(r => r.Timestamp);
        var hi = timed.Max(r => r.Timestamp);
        if (hi == lo)
        {
            return [];
        }

        var split = splitTime ?? (lo + hi) / 2;
        var baseDur = Math.Max(split - lo, 1e-9);
        var recentDur = Math.Max(hi - split, 1e-9);

        var baseCost = new Dictionary<string, double>();
        var recentCost = new Dictionary<string, double>();
        foreach (var r in timed)
        {
            var key = Dimension(r, dimension);
            if (r.Timestamp < split)
            {
                baseCost[key] = baseCost.GetValueOrDefault(key) + r.Cost;
            }
            else
            {
                recentCost[key] = recentCost.GetValueOrDefault(key) + r.Cost;
            }
        }

        var outp = new List<Creep>();
        foreach (var (key, rc) in recentCost)
        {
            var baseRate = baseCost.GetValueOrDefault(key) / baseDur;
            var recentRate = rc / recentDur;
            if (baseRate <= 0)
            {
                continue; // no baseline to compare against; new, not creep
            }

            if (recentRate > factor * baseRate)
            {
                outp.Add(new Creep(key, Math.Round(baseRate, 9), Math.Round(recentRate, 9),
                    Math.Round(recentRate / baseRate, 2)));
            }
        }

        return outp.OrderByDescending(c => c.Factor).ToList();
    }

    private static string Dimension(UsageRecord r, string dimension) => dimension switch
    {
        "feature" => r.Feature,
        "tenant" => r.Tenant,
        "model" => r.Model,
        _ => throw new ArgumentException($"unknown dimension '{dimension}'"),
    };
}
