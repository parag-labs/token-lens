package com.tokenlens;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Aggregates LLM usage by a dimension and flags budget breaches + cost anomalies. */
public final class Tracer {

    public record UsageRecord(
            String model, long inputTokens, long outputTokens,
            double latencyMs, String feature, String tenant) {

        public double cost() {
            return Pricing.costOf(model, inputTokens, outputTokens);
        }

        public long totalTokens() {
            return inputTokens + outputTokens;
        }
    }

    public static final class DimensionStat {
        public final String key;
        public int calls;
        public long inputTokens;
        public long outputTokens;
        public double cost;
        public double latencySum;

        public DimensionStat(String key) {
            this.key = key;
        }

        public double avgLatencyMs() {
            return calls == 0 ? 0.0 : Math.round(latencySum / calls * 100.0) / 100.0;
        }

        public long totalTokens() {
            return inputTokens + outputTokens;
        }
    }

    public record Anomaly(String key, String metric, double value, double baseline, double factor) {
    }

    public record Report(
            double totalCost, int totalCalls,
            Map<String, DimensionStat> byDimension,
            boolean budgetExceeded, List<Anomaly> anomalies) {
    }

    public static Map<String, DimensionStat> aggregate(List<UsageRecord> records, String dimension) {
        Map<String, DimensionStat> stats = new LinkedHashMap<>();
        for (UsageRecord r : records) {
            String key = dimensionOf(r, dimension);
            DimensionStat s = stats.computeIfAbsent(key, DimensionStat::new);
            s.calls++;
            s.inputTokens += r.inputTokens();
            s.outputTokens += r.outputTokens();
            s.cost = Pricing.round6(s.cost + r.cost());
            s.latencySum += r.latencyMs();
        }
        return stats;
    }

    public static List<Anomaly> detectAnomalies(Map<String, DimensionStat> stats, double factor) {
        List<Double> costs = new ArrayList<>();
        for (DimensionStat s : stats.values()) {
            costs.add(s.cost);
        }
        if (costs.size() < 3) {
            return List.of();
        }
        costs.sort(Comparator.naturalOrder());
        double median = costs.get(costs.size() / 2);
        if (median <= 0) {
            return List.of();
        }
        List<Anomaly> out = new ArrayList<>();
        for (DimensionStat s : stats.values()) {
            if (s.cost > factor * median) {
                out.add(new Anomaly(s.key, "cost", s.cost, median,
                        Math.round(s.cost / median * 100.0) / 100.0));
            }
        }
        out.sort(Comparator.comparingDouble(Anomaly::factor).reversed());
        return out;
    }

    public static Report buildReport(List<UsageRecord> records, String dimension,
                                     Double budget, double anomalyFactor) {
        Map<String, DimensionStat> stats = aggregate(records, dimension);
        double totalCost = 0.0;
        int totalCalls = 0;
        for (DimensionStat s : stats.values()) {
            totalCost += s.cost;
            totalCalls += s.calls;
        }
        totalCost = Pricing.round6(totalCost);
        boolean exceeded = budget != null && totalCost > budget;
        return new Report(totalCost, totalCalls, stats, exceeded,
                detectAnomalies(stats, anomalyFactor));
    }

    private static String dimensionOf(UsageRecord r, String dimension) {
        return switch (dimension) {
            case "feature" -> r.feature();
            case "tenant" -> r.tenant();
            case "model" -> r.model();
            default -> throw new IllegalArgumentException("unknown dimension '" + dimension + "'");
        };
    }

    private Tracer() {
    }
}
