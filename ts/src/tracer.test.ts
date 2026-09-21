import { describe, expect, it } from "vitest";
import {
  aggregate,
  avgLatencyMs,
  buildReport,
  detectAnomalies,
  detectCreep,
  recordCost,
  recordTotalTokens,
  statTotalTokens,
  type DimensionStat,
  type UsageRecord,
} from "./tracer";

function rec(
  model: string,
  feature: string,
  inputTokens: number,
  outputTokens: number,
  latencyMs: number,
): UsageRecord {
  return { model, inputTokens, outputTokens, latencyMs, feature, tenant: "acme" };
}

function timed(
  feature: string,
  inputTokens: number,
  outputTokens: number,
  timestamp: number,
): UsageRecord {
  return {
    model: "gpt-4o",
    inputTokens,
    outputTokens,
    latencyMs: 0,
    feature,
    tenant: "acme",
    timestamp,
  };
}

function statsOf(pairs: Array<[string, number]>): Record<string, DimensionStat> {
  const out: Record<string, DimensionStat> = {};
  for (const [key, cost] of pairs) {
    out[key] = { key, calls: 1, inputTokens: 0, outputTokens: 0, cost, latencySum: 0 };
  }
  return out;
}

describe("aggregate", () => {
  it("groups by feature", () => {
    const stats = aggregate(
      [
        rec("gpt-4o", "search", 1000, 500, 100),
        rec("gpt-4o", "search", 2000, 1000, 200),
        rec("gpt-4o-mini", "chat", 1000, 1000, 50),
      ],
      "feature",
    );
    expect(Object.keys(stats)).toHaveLength(2);
    const search = stats.search;
    expect(search.calls).toBe(2);
    expect(search.inputTokens).toBe(3000);
    expect(search.outputTokens).toBe(1500);
    expect(statTotalTokens(search)).toBe(4500);
    expect(avgLatencyMs(search)).toBe(150);
  });

  it("groups by tenant and model", () => {
    const records: UsageRecord[] = [
      { model: "gpt-4o", inputTokens: 100, outputTokens: 100, latencyMs: 0, feature: "a", tenant: "t1" },
      { model: "gpt-4o-mini", inputTokens: 100, outputTokens: 100, latencyMs: 0, feature: "b", tenant: "t2" },
    ];
    expect(Object.keys(aggregate(records, "tenant"))).toHaveLength(2);
    expect(aggregate(records, "tenant").t1.calls).toBe(1);
    expect(aggregate(records, "model")["gpt-4o"].calls).toBe(1);
  });

  it("defaults a missing feature or tenant to 'unknown'", () => {
    const stats = aggregate([{ model: "gpt-4o", inputTokens: 1, outputTokens: 1, latencyMs: 0 }]);
    expect(stats.unknown.calls).toBe(1);
  });

  it("returns nothing for no records", () => {
    expect(Object.keys(aggregate([]))).toHaveLength(0);
  });
});

describe("avgLatencyMs", () => {
  it("is zero when there are no calls", () => {
    expect(
      avgLatencyMs({ key: "x", calls: 0, inputTokens: 0, outputTokens: 0, cost: 0, latencySum: 0 }),
    ).toBe(0);
  });
});

describe("detectAnomalies", () => {
  it("needs at least three dimensions", () => {
    expect(detectAnomalies(statsOf([["a", 1], ["b", 100]]), 3.0)).toHaveLength(0);
  });

  it("flags an outlier", () => {
    const got = detectAnomalies(statsOf([["a", 1], ["b", 1], ["c", 50]]), 3.0);
    expect(got).toHaveLength(1);
    expect(got[0].key).toBe("c");
    expect(got[0].metric).toBe("cost");
    expect(got[0].baseline).toBe(1);
    expect(got[0].factor).toBe(50);
  });

  it("returns nothing when the median is zero", () => {
    expect(detectAnomalies(statsOf([["a", 0], ["b", 0], ["c", 5]]), 3.0)).toHaveLength(0);
  });

  it("sorts by factor descending", () => {
    // median of [1,1,1,10,40] is 1, so both 10 and 40 clear the 3x threshold
    const got = detectAnomalies(
      statsOf([["a", 1], ["b", 1], ["c", 1], ["d", 10], ["e", 40]]),
      3.0,
    );
    expect(got.map((a) => a.key)).toEqual(["e", "d"]);
  });

  it("uses the upper-middle element as the median", () => {
    // with an even count the median is index len/2: median of [1,1,10,40] is 10
    const got = detectAnomalies(statsOf([["a", 1], ["b", 1], ["c", 10], ["d", 40]]), 3.0);
    expect(got).toHaveLength(1);
    expect(got[0].key).toBe("d");
    expect(got[0].baseline).toBe(10);
    expect(got[0].factor).toBe(4);
  });
});

describe("buildReport", () => {
  it("totals cost and flags a budget breach", () => {
    const report = buildReport(
      [
        rec("gpt-4o", "search", 1_000_000, 1_000_000, 10),
        rec("gpt-4o", "chat", 1_000_000, 1_000_000, 20),
      ],
      "feature",
      20,
    );
    expect(report.totalCost).toBeCloseTo(25.0, 9);
    expect(report.totalCalls).toBe(2);
    expect(report.budgetExceeded).toBe(true);
  });

  it("never breaches when no budget is configured", () => {
    const report = buildReport([rec("gpt-4o", "search", 1_000_000, 1_000_000, 10)]);
    expect(report.budgetExceeded).toBe(false);
  });

  it("treats a budget exactly met as within budget", () => {
    const report = buildReport([rec("gpt-4o", "search", 1_000_000, 1_000_000, 10)], "feature", 12.5);
    expect(report.budgetExceeded).toBe(false);
  });
});

describe("detectCreep", () => {
  it("flags a rising cost rate", () => {
    const got = detectCreep(
      [
        timed("search", 1000, 1000, 10),
        timed("search", 1_000_000, 1_000_000, 150),
        timed("search", 1_000_000, 1_000_000, 180),
        timed("search", 1_000_000, 1_000_000, 199),
      ],
      "feature",
      100,
      2.0,
    );
    expect(got).toHaveLength(1);
    expect(got[0].key).toBe("search");
    expect(got[0].recentRate).toBeGreaterThan(got[0].baselineRate);
  });

  it("ignores dimensions with no baseline", () => {
    // "chat" only appears in the recent window -> new, not creep
    const got = detectCreep(
      [timed("search", 1000, 1000, 10), timed("chat", 1_000_000, 1_000_000, 150)],
      "feature",
      100,
      2.0,
    );
    expect(got.some((c) => c.key === "chat")).toBe(false);
  });

  it("needs at least two timed records", () => {
    expect(detectCreep([timed("a", 1, 1, 10)])).toHaveLength(0);
  });

  it("ignores untimed records", () => {
    expect(detectCreep([rec("gpt-4o", "a", 1, 1, 1), rec("gpt-4o", "a", 1, 1, 1)])).toHaveLength(0);
  });

  it("returns nothing for a zero-width window", () => {
    expect(detectCreep([timed("a", 1, 1, 50), timed("a", 1, 1, 50)])).toHaveLength(0);
  });

  it("defaults the split to the midpoint", () => {
    const got = detectCreep([
      timed("search", 1000, 1000, 10),
      timed("search", 1_000_000, 1_000_000, 100),
    ]);
    expect(got).toHaveLength(1);
    expect(got[0].factor).toBeCloseTo(1000, 6);
  });
});

describe("record helpers", () => {
  it("computes cost and total tokens", () => {
    const r = rec("gpt-4o", "search", 1_000_000, 1_000_000, 10);
    expect(recordCost(r)).toBeCloseTo(12.5, 9);
    expect(recordTotalTokens(r)).toBe(2_000_000);
  });
});
