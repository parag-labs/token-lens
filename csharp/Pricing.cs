namespace TokenLens;

public readonly record struct ModelPrice(double InputPerMillion, double OutputPerMillion);

public sealed class UnknownModelException(string model)
    : Exception($"unknown model '{model}'");

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

    public static double CostOf(string model, long inputTokens, long outputTokens)
    {
        if (!Prices.TryGetValue(model, out var price))
        {
            throw new UnknownModelException(model);
        }

        var cost = inputTokens / 1_000_000.0 * price.InputPerMillion
                   + outputTokens / 1_000_000.0 * price.OutputPerMillion;
        return Math.Round(cost, 6);
    }
}
