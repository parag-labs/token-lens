package com.tokenlens;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Random;

import org.junit.jupiter.api.Test;

import com.tokenlens.Tracer.Anomaly;
import com.tokenlens.Tracer.Creep;
import com.tokenlens.Tracer.DimensionStat;
import com.tokenlens.Tracer.Report;
import com.tokenlens.Tracer.UsageRecord;

/**
 * Stress suite: prove token-lens's scaling properties in the Java port -
 * aggregation memory bounded by dimension cardinality (not record count), exact
 * high-volume totals, and order-independent creep detection.
 */
class TokenLensStressTest {

    private static UsageRecord rec(String feature, long outTokens, double ts) {
        return new UsageRecord("gpt-4o-mini", 0, outTokens, 10.0, feature, "t", ts);
    }

    @Test
    void aggregationMemoryIsBoundedByCardinalityNotVolume() {
        Random rng = new Random(0);
        String[] features = new String[10];
        for (int i = 0; i < 10; i++) {
            features[i] = "feature-" + i;
        }
        List<UsageRecord> records = new ArrayList<>(1_000_000);
        for (int i = 0; i < 1_000_000; i++) {
            records.add(rec(features[rng.nextInt(10)], rng.nextInt(500) + 1, 0));
        }
        var stats = Tracer.aggregate(records, "feature");
        assertEquals(10, stats.size());
        long calls = stats.values().stream().mapToLong(s -> s.calls).sum();
        assertEquals(1_000_000, calls);
    }

    @Test
    void highVolumeTotalsAreExact() {
        List<UsageRecord> records = new ArrayList<>();
        for (int i = 0; i < 100_000; i++) {
            records.add(rec("f", 100, 0));
        }
        Report report = Tracer.buildReport(records, "feature", null, 3.0);
        assertEquals(100_000, report.totalCalls());
        assertEquals(100L * 100_000, report.byDimension().get("f").outputTokens);
    }

    @Test
    void creepDetectionIsOrderIndependent() {
        List<UsageRecord> records = new ArrayList<>();
        for (int t = 0; t < 100; t += 10) {
            records.add(rec("steady", 1000, t));
            records.add(rec("rising", 1000, t));
        }
        for (int t = 100; t < 200; t += 10) {
            records.add(rec("steady", 1000, t));
            records.add(rec("rising", 6000, t));
        }

        List<String> ordered = keys(Tracer.detectCreep(records, "feature", 100.0, 2.0));

        List<UsageRecord> shuffled = new ArrayList<>(records);
        Collections.shuffle(shuffled, new Random(123));
        List<String> fromShuffled = keys(Tracer.detectCreep(shuffled, "feature", 100.0, 2.0));

        assertEquals(ordered, fromShuffled);
        assertTrue(ordered.contains("rising"));
        assertTrue(!ordered.contains("steady"));
    }

    @Test
    void anomalyDetectionStableUnderLargeCardinality() {
        List<UsageRecord> records = new ArrayList<>();
        for (int i = 0; i < 5000; i++) {
            records.add(rec("cheap-" + i, 10, 0));
        }
        for (int i = 0; i < 200; i++) {
            records.add(rec("whale", 100_000, 0));
        }
        var stats = Tracer.aggregate(records, "feature");
        List<Anomaly> anomalies = Tracer.detectAnomalies(stats, 3.0);
        assertTrue(anomalies.stream().anyMatch(a -> a.key().equals("whale")));
    }

    private static List<String> keys(List<Creep> creeps) {
        List<String> out = new ArrayList<>();
        for (Creep c : creeps) {
            out.add(c.key());
        }
        Collections.sort(out);
        return out;
    }
}
