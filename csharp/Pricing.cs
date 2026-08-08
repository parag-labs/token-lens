using System.Text.Json;

namespace TokenLens;

public readonly record struct ModelPrice(double InputPerMillion, double OutputPerMillion);

public sealed class UnknownModelException(string model)
    : Exception($"unknown model '{model}'");

/// <summary>
/// Resolves a model (optionally at a point in time) to a <see cref="ModelPrice"/>.
/// Rating is metering's second half: count usage, then price it against a book.
/// </summary>
public interface IPricingProvider
{
    ModelPrice PriceFor(string model, double? at = null);
}

/// <summary>Cost = price × tokens, rounded like the other languages.</summary>
public static class PricingProviderExtensions
{
    public static double Cost(this IPricingProvider provider, string model,
        long inputTokens, long outputTokens, double? at = null)
    {
        var price = provider.PriceFor(model, at);
        var cost = inputTokens / 1_000_000.0 * price.InputPerMillion
                   + outputTokens / 1_000_000.0 * price.OutputPerMillion;
        return Math.Round(cost, 6);
    }
}

/// <summary>A flat, always-current table. This is the default provider.</summary>
public sealed class StaticPricing : IPricingProvider
{
    private readonly IReadOnlyDictionary<string, ModelPrice> _prices;

    public StaticPricing(IReadOnlyDictionary<string, ModelPrice>? prices = null)
    {
        _prices = prices ?? Pricing.Prices;
    }

    public ModelPrice PriceFor(string model, double? at = null)
    {
        if (!_prices.TryGetValue(model, out var price))
        {
            throw new UnknownModelException(model);
        }

        return price;
    }
}

/// <summary>One dated price row. <c>EffectiveFrom</c> is unix seconds; 0 = "since forever".</summary>
public readonly record struct PriceEntry(string Model, ModelPrice Price, double EffectiveFrom = 0.0);

/// <summary>
/// A versioned price book. Each model may carry several dated entries; a lookup
/// returns the newest entry whose <c>EffectiveFrom</c> is at or before the usage
/// timestamp, so back-dated recomputes stay correct.
/// </summary>
public sealed class FilePricing : IPricingProvider
{
    private readonly IReadOnlyDictionary<string, List<PriceEntry>> _byModel;

    public FilePricing(IEnumerable<PriceEntry> entries)
    {
        var byModel = new Dictionary<string, List<PriceEntry>>();
        foreach (var e in entries)
        {
            if (!byModel.TryGetValue(e.Model, out var list))
            {
                list = new List<PriceEntry>();
                byModel[e.Model] = list;
            }

            list.Add(e);
        }

        foreach (var list in byModel.Values)
        {
            list.Sort((a, b) => a.EffectiveFrom.CompareTo(b.EffectiveFrom));
        }

        _byModel = byModel;
    }

    public static FilePricing FromJson(string text)
    {
        using var doc = JsonDocument.Parse(text);
        var entries = new List<PriceEntry>();
        foreach (var row in doc.RootElement.GetProperty("prices").EnumerateArray())
        {
            var effectiveFrom = row.TryGetProperty("effective_from", out var ef) ? ef.GetDouble() : 0.0;
            entries.Add(new PriceEntry(
                row.GetProperty("model").GetString()!,
                new ModelPrice(
                    row.GetProperty("input_per_million").GetDouble(),
                    row.GetProperty("output_per_million").GetDouble()),
                effectiveFrom));
        }

        return new FilePricing(entries);
    }

    public static FilePricing FromFile(string path) => FromJson(File.ReadAllText(path));

    public ModelPrice PriceFor(string model, double? at = null)
    {
        if (!_byModel.TryGetValue(model, out var history) || history.Count == 0)
        {
            throw new UnknownModelException(model);
        }

        if (at is null)
        {
            return history[^1].Price; // newest
        }

        ModelPrice? chosen = null;
        foreach (var e in history)
        {
            if (e.EffectiveFrom <= at.Value)
            {
                chosen = e.Price;
            }
        }

        return chosen ?? history[0].Price;
    }
}

/// <summary>
/// Try each provider in order, fall back to the next on an unknown model. Put a
/// remote catalog first and the embedded <see cref="StaticPricing"/> last so a
/// remote miss (or a provider that failed to load) still rates against defaults.
/// </summary>
public sealed class ChainedPricing : IPricingProvider
{
    private readonly IReadOnlyList<IPricingProvider> _providers;

    public ChainedPricing(params IPricingProvider[] providers)
    {
        if (providers.Length == 0)
        {
            throw new ArgumentException("ChainedPricing needs at least one provider", nameof(providers));
        }

        _providers = providers;
    }

    public ModelPrice PriceFor(string model, double? at = null)
    {
        foreach (var provider in _providers)
        {
            try
            {
                return provider.PriceFor(model, at);
            }
            catch (UnknownModelException)
            {
                // try the next provider
            }
        }

        throw new UnknownModelException(model);
    }
}

/// <summary>Model pricing (USD per 1M tokens) and per-call cost computation.</summary>
public static class Pricing
{
    public static readonly IReadOnlyDictionary<string, ModelPrice> Prices =
        new Dictionary<string, ModelPrice>
        {
            ["gpt-4o"] = new(2.50, 10.00),
            ["gpt-4o-mini"] = new(0.15, 0.60),
            ["o3-mini"] = new(1.10, 4.40),
            ["claude-3.7-sonnet"] = new(3.00, 15.00),
            ["claude-3.5-haiku"] = new(0.80, 4.00),
            ["llama-3.3-70b"] = new(0.20, 0.20),
        };

    /// <summary>The default provider used by <see cref="CostOf"/>.</summary>
    public static readonly IPricingProvider Default = new StaticPricing();

    public static double CostOf(string model, long inputTokens, long outputTokens,
        double? at = null, IPricingProvider? provider = null)
        => (provider ?? Default).Cost(model, inputTokens, outputTokens, at);
}
