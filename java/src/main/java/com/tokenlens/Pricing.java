package com.tokenlens;

import java.util.Map;

/** Model pricing (USD per 1M tokens) and per-call cost computation. */
public final class Pricing {

    public record ModelPrice(double inputPerMillion, double outputPerMillion) {
    }

    public static final Map<String, ModelPrice> PRICES = Map.of(
            "gpt-4o", new ModelPrice(2.50, 10.00),
            "gpt-4o-mini", new ModelPrice(0.15, 0.60),
            "o3-mini", new ModelPrice(1.10, 4.40),
            "claude-3.7-sonnet", new ModelPrice(3.00, 15.00),
            "claude-3.5-haiku", new ModelPrice(0.80, 4.00),
            "llama-3.3-70b", new ModelPrice(0.20, 0.20));

    public static double costOf(String model, long inputTokens, long outputTokens) {
        ModelPrice price = PRICES.get(model);
        if (price == null) {
            throw new UnknownModelException(model);
        }
        double cost = inputTokens / 1_000_000.0 * price.inputPerMillion()
                + outputTokens / 1_000_000.0 * price.outputPerMillion();
        return round6(cost);
    }

    static double round6(double v) {
        return Math.round(v * 1_000_000.0) / 1_000_000.0;
    }

    private Pricing() {
    }

    public static final class UnknownModelException extends RuntimeException {
        public UnknownModelException(String model) {
            super("unknown model '" + model + "'");
        }
    }
}
