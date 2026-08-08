package com.tokenlens;

import java.io.IOException;
import java.io.UncheckedIOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/**
 * Model pricing (USD per 1M tokens) and per-call cost computation.
 *
 * <p>Rating is metering's second half: count usage, then price it against a book.
 * There is no live market feed for tokens - list prices change a few times a year -
 * so the industry pattern is a versioned, effective-dated price book behind a small
 * provider interface: {@link StaticPricing} (embedded default), {@link FilePricing}
 * (a versioned JSON book with point-in-time rating), and {@link ChainedPricing}
 * (try a remote catalog first, fall back to the embedded table).
 */
public final class Pricing {

    public record ModelPrice(double inputPerMillion, double outputPerMillion) {
    }

    /** Resolves a model (optionally at a point in time) to a {@link ModelPrice}. */
    public interface PricingProvider {
        ModelPrice priceFor(String model, Double at);

        default double cost(String model, long inputTokens, long outputTokens, Double at) {
            ModelPrice price = priceFor(model, at);
            double cost = inputTokens / 1_000_000.0 * price.inputPerMillion()
                    + outputTokens / 1_000_000.0 * price.outputPerMillion();
            return round6(cost);
        }

        default double cost(String model, long inputTokens, long outputTokens) {
            return cost(model, inputTokens, outputTokens, null);
        }
    }

    /** A flat, always-current table. This is the default provider. */
    public static final class StaticPricing implements PricingProvider {
        private final Map<String, ModelPrice> prices;

        public StaticPricing() {
            this(PRICES);
        }

        public StaticPricing(Map<String, ModelPrice> prices) {
            this.prices = Map.copyOf(prices);
        }

        @Override
        public ModelPrice priceFor(String model, Double at) {
            ModelPrice price = prices.get(model);
            if (price == null) {
                throw new UnknownModelException(model);
            }
            return price;
        }
    }

    /** One dated price row. {@code effectiveFrom} is unix seconds; 0 = "since forever". */
    public record PriceEntry(String model, ModelPrice price, double effectiveFrom) {
    }

    /**
     * A versioned price book. Each model may carry several dated entries; a lookup
     * returns the newest entry whose {@code effectiveFrom} is at or before the usage
     * timestamp, so back-dated recomputes stay correct.
     */
    public static final class FilePricing implements PricingProvider {
        private static final ObjectMapper MAPPER = new ObjectMapper();

        private final Map<String, List<PriceEntry>> byModel;

        public FilePricing(List<PriceEntry> entries) {
            Map<String, List<PriceEntry>> map = new LinkedHashMap<>();
            for (PriceEntry e : entries) {
                map.computeIfAbsent(e.model(), k -> new ArrayList<>()).add(e);
            }
            for (List<PriceEntry> list : map.values()) {
                list.sort(Comparator.comparingDouble(PriceEntry::effectiveFrom));
            }
            this.byModel = map;
        }

        public static FilePricing fromJson(String text) {
            try {
                JsonNode root = MAPPER.readTree(text);
                List<PriceEntry> entries = new ArrayList<>();
                for (JsonNode row : root.get("prices")) {
                    double effectiveFrom = row.has("effective_from") ? row.get("effective_from").asDouble() : 0.0;
                    entries.add(new PriceEntry(
                            row.get("model").asText(),
                            new ModelPrice(
                                    row.get("input_per_million").asDouble(),
                                    row.get("output_per_million").asDouble()),
                            effectiveFrom));
                }
                return new FilePricing(entries);
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }

        public static FilePricing fromFile(Path path) {
            try {
                return fromJson(Files.readString(path));
            } catch (IOException e) {
                throw new UncheckedIOException(e);
            }
        }

        @Override
        public ModelPrice priceFor(String model, Double at) {
            List<PriceEntry> history = byModel.get(model);
            if (history == null || history.isEmpty()) {
                throw new UnknownModelException(model);
            }
            if (at == null) {
                return history.get(history.size() - 1).price(); // newest
            }
            ModelPrice chosen = null;
            for (PriceEntry e : history) {
                if (e.effectiveFrom() <= at) {
                    chosen = e.price();
                }
            }
            return chosen != null ? chosen : history.get(0).price();
        }
    }

    /**
     * Try each provider in order, fall back to the next on an unknown model. Put a
     * remote catalog first and the embedded {@link StaticPricing} last so a remote
     * miss (or a provider that failed to load) still rates against defaults.
     */
    public static final class ChainedPricing implements PricingProvider {
        private final List<PricingProvider> providers;

        public ChainedPricing(PricingProvider... providers) {
            if (providers.length == 0) {
                throw new IllegalArgumentException("ChainedPricing needs at least one provider");
            }
            this.providers = List.of(providers);
        }

        @Override
        public ModelPrice priceFor(String model, Double at) {
            for (PricingProvider provider : providers) {
                try {
                    return provider.priceFor(model, at);
                } catch (UnknownModelException ignored) {
                    // try the next provider
                }
            }
            throw new UnknownModelException(model);
        }
    }

    // Illustrative list prices (USD / 1M tokens). Update as vendors change them.
    public static final Map<String, ModelPrice> PRICES = Map.ofEntries(
            // OpenAI
            Map.entry("gpt-4o", new ModelPrice(2.5, 10.0)),
            Map.entry("gpt-4o-mini", new ModelPrice(0.15, 0.6)),
            Map.entry("gpt-4.1", new ModelPrice(2.0, 8.0)),
            Map.entry("gpt-4.1-mini", new ModelPrice(0.4, 1.6)),
            Map.entry("gpt-4.1-nano", new ModelPrice(0.1, 0.4)),
            Map.entry("o3", new ModelPrice(2.0, 8.0)),
            Map.entry("o3-mini", new ModelPrice(1.1, 4.4)),
            Map.entry("o4-mini", new ModelPrice(1.1, 4.4)),
            // Anthropic
            Map.entry("claude-opus-4", new ModelPrice(15.0, 75.0)),
            Map.entry("claude-sonnet-4", new ModelPrice(3.0, 15.0)),
            Map.entry("claude-3.7-sonnet", new ModelPrice(3.0, 15.0)),
            Map.entry("claude-3.5-sonnet", new ModelPrice(3.0, 15.0)),
            Map.entry("claude-3.5-haiku", new ModelPrice(0.8, 4.0)),
            Map.entry("claude-3-haiku", new ModelPrice(0.25, 1.25)),
            // Google
            Map.entry("gemini-2.5-pro", new ModelPrice(1.25, 10.0)),
            Map.entry("gemini-2.5-flash", new ModelPrice(0.3, 2.5)),
            Map.entry("gemini-2.0-flash", new ModelPrice(0.1, 0.4)),
            Map.entry("gemini-1.5-pro", new ModelPrice(1.25, 5.0)),
            Map.entry("gemini-1.5-flash", new ModelPrice(0.075, 0.3)),
            // Meta Llama
            Map.entry("llama-3.3-70b", new ModelPrice(0.2, 0.2)),
            Map.entry("llama-3.1-405b", new ModelPrice(3.5, 3.5)),
            Map.entry("llama-3.1-8b", new ModelPrice(0.05, 0.05)),
            // Mistral
            Map.entry("mistral-large", new ModelPrice(2.0, 6.0)),
            Map.entry("mistral-small", new ModelPrice(0.2, 0.6)),
            // DeepSeek
            Map.entry("deepseek-chat", new ModelPrice(0.27, 1.1)),
            Map.entry("deepseek-reasoner", new ModelPrice(0.55, 2.19)),
            // xAI
            Map.entry("grok-2", new ModelPrice(2.0, 10.0)));

    /** The default provider used by {@link #costOf}. */
    public static final PricingProvider DEFAULT = new StaticPricing();

    public static double costOf(String model, long inputTokens, long outputTokens) {
        return DEFAULT.cost(model, inputTokens, outputTokens, null);
    }

    public static double costOf(String model, long inputTokens, long outputTokens,
                                Double at, PricingProvider provider) {
        return (provider != null ? provider : DEFAULT).cost(model, inputTokens, outputTokens, at);
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
