package com.tokenlens;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

import com.tokenlens.Pricing.ChainedPricing;
import com.tokenlens.Pricing.FilePricing;
import com.tokenlens.Pricing.ModelPrice;
import com.tokenlens.Pricing.PriceEntry;
import com.tokenlens.Pricing.StaticPricing;
import com.tokenlens.Tracer.Creep;
import com.tokenlens.Tracer.DimensionStat;
import com.tokenlens.Tracer.UsageRecord;

class TokenLensTest {

    @Test
    void costOfKnownModel() {
        // 1M input @ 2.50 + 1M output @ 10.00 = 12.50
        assertEquals(12.5, Pricing.costOf("gpt-4o", 1_000_000, 1_000_000));
    }

    @Test
    void unknownModelThrows() {
        assertThrows(Pricing.UnknownModelException.class, () -> Pricing.costOf("nope", 10, 10));
    }

    @Test
    void aggregationByFeature() {
        List<UsageRecord> records = List.of(
                new UsageRecord("gpt-4o-mini", 1000, 500, 120, "search", "acme"),
                new UsageRecord("gpt-4o-mini", 2000, 1000, 200, "search", "acme"),
                new UsageRecord("gpt-4o", 500, 500, 80, "summarize", "acme"));
        Map<String, DimensionStat> stats = Tracer.aggregate(records, "feature");
        assertEquals(2, stats.get("search").calls);
        assertEquals(3000, stats.get("search").inputTokens);
        assertEquals(1, stats.get("summarize").calls);
    }

    @Test
    void budgetGate() {
        List<UsageRecord> records = List.of(
                new UsageRecord("gpt-4o", 1_000_000, 1_000_000, 100, "x", "t"));
        Tracer.Report report = Tracer.buildReport(records, "feature", 5.0, 3.0);
        assertEquals(12.5, report.totalCost());
        assertTrue(report.budgetExceeded());

        Tracer.Report ok = Tracer.buildReport(records, "feature", 20.0, 3.0);
        assertFalse(ok.budgetExceeded());
    }

    @Test
    void anomalyDetectionFlagsExpensiveDimension() {
        Map<String, DimensionStat> stats = new LinkedHashMap<>();
        for (String k : List.of("a", "b", "c")) {
            DimensionStat s = new DimensionStat(k);
            s.calls = 1;
            s.cost = 1.0;
            stats.put(k, s);
        }
        DimensionStat runaway = new DimensionStat("runaway");
        runaway.calls = 1;
        runaway.cost = 10.0;
        stats.put("runaway", runaway);

        List<Tracer.Anomaly> anomalies = Tracer.detectAnomalies(stats, 3.0);
        assertTrue(anomalies.stream().anyMatch(a -> a.key().equals("runaway")));
    }

    @Test
    void noAnomaliesWhenUniform() {
        Map<String, DimensionStat> stats = new LinkedHashMap<>();
        for (String k : List.of("a", "b", "c", "d")) {
            DimensionStat s = new DimensionStat(k);
            s.calls = 1;
            s.cost = 1.0;
            stats.put(k, s);
        }
        assertTrue(Tracer.detectAnomalies(stats, 3.0).isEmpty());
    }

    @Test
    void avgLatency() {
        List<UsageRecord> records = List.of(
                new UsageRecord("gpt-4o-mini", 10, 10, 100, "f", "t"),
                new UsageRecord("gpt-4o-mini", 10, 10, 300, "f", "t"));
        Map<String, DimensionStat> stats = Tracer.aggregate(records, "feature");
        assertEquals(200.0, stats.get("f").avgLatencyMs());
    }

    private static UsageRecord rec(String feature, long outTokens, double ts) {
        return new UsageRecord("gpt-4o-mini", 0, outTokens, 10.0, feature, "t", ts);
    }

    @Test
    void creepFlagsARisingDimension() {
        List<UsageRecord> records = new java.util.ArrayList<>();
        for (int t = 0; t < 100; t += 10) {
            records.add(rec("search", 1000, t));
            records.add(rec("summarize", 1000, t));
        }
        for (int t = 100; t < 200; t += 10) {
            records.add(rec("search", 1000, t));
            records.add(rec("summarize", 5000, t));
        }
        List<Creep> creeps = Tracer.detectCreep(records, "feature", 100.0, 2.0);
        assertTrue(creeps.stream().anyMatch(c -> c.key().equals("summarize")));
        assertFalse(creeps.stream().anyMatch(c -> c.key().equals("search")));
    }

    @Test
    void creepIgnoresBrandNewDimension() {
        List<UsageRecord> records = new java.util.ArrayList<>();
        for (int t = 0; t < 100; t += 10) {
            records.add(rec("steady", 1000, t));
        }
        for (int t = 100; t < 200; t += 10) {
            records.add(rec("steady", 1000, t));
            records.add(rec("brandnew", 9000, t));
        }
        List<Creep> creeps = Tracer.detectCreep(records, "feature", 100.0, 2.0);
        assertFalse(creeps.stream().anyMatch(c -> c.key().equals("brandnew")));
    }

    @Test
    void creepNeedsTimestamps() {
        List<UsageRecord> records = List.of(
                new UsageRecord("gpt-4o-mini", 10, 10, 5.0, "x", "t"),
                new UsageRecord("gpt-4o-mini", 10, 10, 5.0, "x", "t"));
        assertTrue(Tracer.detectCreep(records, "feature", null, 2.0).isEmpty());
    }

    @Test
    void staticIsTheDefaultAndMatchesHelper() {
        StaticPricing provider = new StaticPricing();
        assertEquals(12.5, provider.cost("gpt-4o", 1_000_000, 1_000_000));
        assertEquals(12.5, Pricing.costOf("gpt-4o", 1_000_000, 1_000_000));
    }

    @Test
    void filePricingLoadsAVersionedBook() {
        String book = "{ \"prices\": [ { \"model\": \"gpt-4o\", "
                + "\"input_per_million\": 2.5, \"output_per_million\": 10.0 } ] }";
        FilePricing provider = FilePricing.fromJson(book);
        assertEquals(2.5, provider.cost("gpt-4o", 1_000_000, 0));
    }

    @Test
    void pointInTimeRatingPicksTheEffectiveVersion() {
        String book = "{ \"prices\": ["
                + "{ \"model\": \"gpt-4o\", \"input_per_million\": 2.5, "
                + "\"output_per_million\": 10.0, \"effective_from\": 0 },"
                + "{ \"model\": \"gpt-4o\", \"input_per_million\": 2.0, "
                + "\"output_per_million\": 8.0, \"effective_from\": 1000 } ] }";
        FilePricing provider = FilePricing.fromJson(book);
        assertEquals(2.5, provider.cost("gpt-4o", 1_000_000, 0, 500.0));
        assertEquals(2.0, provider.cost("gpt-4o", 1_000_000, 0, 1500.0));
        assertEquals(2.0, provider.cost("gpt-4o", 1_000_000, 0)); // newest when no timestamp
    }

    @Test
    void chainedFallsBackToTheEmbeddedTable() {
        FilePricing remote = new FilePricing(new ArrayList<>()); // like a failed load
        StaticPricing negotiated = new StaticPricing(Map.of("acme-1", new ModelPrice(1.0, 1.0)));
        ChainedPricing chain = new ChainedPricing(remote, negotiated, new StaticPricing());

        assertEquals(1.0, chain.cost("acme-1", 1_000_000, 0));
        assertEquals(12.5, chain.cost("gpt-4o", 1_000_000, 1_000_000));
    }

    @Test
    void chainedThrowsWhenNoProviderKnowsTheModel() {
        ChainedPricing chain = new ChainedPricing(new FilePricing(new ArrayList<>()), new StaticPricing());
        assertThrows(Pricing.UnknownModelException.class, () -> chain.priceFor("mystery-model", null));
    }
}
