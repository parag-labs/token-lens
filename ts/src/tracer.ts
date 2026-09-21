/**
 * Usage records and cost/latency aggregation with budget + anomaly detection.
 *
 * Core value: attribute LLM spend to a dimension (feature / tenant / model) and
 * surface budget breaches and cost anomalies - the numbers a FinOps/eng lead
 * asks for. Pure logic; no I/O.
 */

import { costOf, round6 } from "./pricing";

export type Dimension = "feature" | "tenant" | "model";

/** One LLM call: what it cost, how long it took, and who it was for. */
export interface UsageRecord {
  model: string;
  inputTokens: number;
  outputTokens: number;
  latencyMs: number;
  feature?: string;
  tenant?: string;
  /** epoch seconds; used by the rolling-window creep detector. 0 = untimed. */
  timestamp?: number;
}

/** Rates a record against the default price book. */
export function recordCost(r: UsageRecord): number {
  return costOf(r.model, r.inputTokens, r.outputTokens);
}

/** Input plus output tokens. */
export function recordTotalTokens(r: UsageRecord): number {
  return r.inputTokens + r.outputTokens;
}

/** Accumulated usage for one value of a dimension. */
export interface DimensionStat {
  key: string;
  calls: number;
  inputTokens: number;
  outputTokens: number;
  cost: number;
  latencySum: number;
}

/** The mean latency across a dimension's calls. */
export function avgLatencyMs(s: DimensionStat): number {
  return s.calls === 0 ? 0 : Math.round((s.latencySum / s.calls) * 100) / 100;
}

/** Input plus output tokens for a dimension. */
export function statTotalTokens(s: DimensionStat): number {
  return s.inputTokens + s.outputTokens;
}

/** A dimension that is expensive relative to its peers right now. */
export interface Anomaly {
  key: string;
  metric: string;
  value: number;
  baseline: number;
  factor: number;
}

/**
 * A dimension whose cost *rate* is rising over time (gradual creep).
 *
 * The plain median detector only catches a dimension that is expensive relative
 * to its peers right now. It misses one that is slowly growing but still
 * cheaper than the noisy ones. Comparing a recent window against an earlier
 * baseline window catches that trend.
 */
export interface Creep {
  key: string;
  /** cost per second over the baseline window */
  baselineRate: number;
  /** cost per second over the recent window */
  recentRate: number;
  factor: number;
}

/** Spend by dimension, budget state, and anomalies. */
export interface Report {
  totalCost: number;
  totalCalls: number;
  byDimension: Record<string, DimensionStat>;
  budgetExceeded: boolean;
  anomalies: Anomaly[];
}

function keyOf(r: UsageRecord, dimension: Dimension): string {
  switch (dimension) {
    case "feature":
      return r.feature ?? "unknown";
    case "tenant":
      return r.tenant ?? "unknown";
    case "model":
      return r.model;
    default:
      throw new Error(`unknown dimension '${dimension as string}'`);
  }
}

/** Rolls records up by the given dimension. */
export function aggregate(
  records: UsageRecord[],
  dimension: Dimension = "feature",
): Record<string, DimensionStat> {
  const stats: Record<string, DimensionStat> = {};
  for (const r of records) {
    const key = keyOf(r, dimension);
    let s = stats[key];
    if (!s) {
      s = { key, calls: 0, inputTokens: 0, outputTokens: 0, cost: 0, latencySum: 0 };
      stats[key] = s;
    }
    s.calls += 1;
    s.inputTokens += r.inputTokens;
    s.outputTokens += r.outputTokens;
    s.cost = round6(s.cost + recordCost(r));
    s.latencySum += r.latencyMs;
  }
  return stats;
}

/** Flags dimensions whose cost exceeds `factor` x the median dimension cost. */
export function detectAnomalies(
  stats: Record<string, DimensionStat>,
  factor = 3.0,
): Anomaly[] {
  const values = Object.values(stats);
  const costs = values.map((s) => s.cost).sort((a, b) => a - b);
  if (costs.length < 3) return [];
  const median = costs[Math.floor(costs.length / 2)];
  if (median <= 0) return [];
  return values
    .filter((s) => s.cost > factor * median)
    .map((s) => ({
      key: s.key,
      metric: "cost",
      value: s.cost,
      baseline: median,
      factor: Math.round((s.cost / median) * 100) / 100,
    }))
    .sort((a, b) => b.factor - a.factor);
}

/**
 * Flags dimensions whose cost *rate* rose from a baseline window to a recent one.
 *
 * Records with `timestamp < splitTime` form the baseline window; the rest form
 * the recent window. Cost is normalized by each window's duration to get a rate
 * (cost/second), so uneven window lengths compare fairly. A dimension is
 * flagged when its recent rate exceeds `factor` times its baseline rate.
 *
 * When `splitTime` is undefined, the midpoint of the observed timestamp range
 * is used. Records with a timestamp of 0 are treated as untimed and ignored.
 */
export function detectCreep(
  records: UsageRecord[],
  dimension: Dimension = "feature",
  splitTime?: number,
  factor = 2.0,
): Creep[] {
  const timed = records.filter((r) => (r.timestamp ?? 0) > 0);
  if (timed.length < 2) return [];

  const times = timed.map((r) => r.timestamp as number);
  const lo = Math.min(...times);
  const hi = Math.max(...times);
  if (hi === lo) return [];

  const split = splitTime ?? (lo + hi) / 2;
  const baseDur = Math.max(split - lo, 1e-9);
  const recentDur = Math.max(hi - split, 1e-9);

  const baseCost = new Map<string, number>();
  const recentCost = new Map<string, number>();
  for (const r of timed) {
    const key = keyOf(r, dimension);
    const bucket = (r.timestamp as number) < split ? baseCost : recentCost;
    bucket.set(key, (bucket.get(key) ?? 0) + recordCost(r));
  }

  const out: Creep[] = [];
  for (const [key, rc] of recentCost) {
    const baseRate = (baseCost.get(key) ?? 0) / baseDur;
    const recentRate = rc / recentDur;
    if (baseRate <= 0) continue; // no baseline to compare against; new, not creep
    if (recentRate > factor * baseRate) {
      out.push({
        key,
        baselineRate: Math.round(baseRate * 1e9) / 1e9,
        recentRate: Math.round(recentRate * 1e9) / 1e9,
        factor: Math.round((recentRate / baseRate) * 100) / 100,
      });
    }
  }
  return out.sort((a, b) => b.factor - a.factor);
}

/** Aggregates records and evaluates budget + anomalies in one pass. */
export function buildReport(
  records: UsageRecord[],
  dimension: Dimension = "feature",
  budget?: number,
  anomalyFactor = 3.0,
): Report {
  const stats = aggregate(records, dimension);
  const values = Object.values(stats);
  const totalCost = round6(values.reduce((acc, s) => acc + s.cost, 0));
  return {
    totalCost,
    totalCalls: values.reduce((acc, s) => acc + s.calls, 0),
    byDimension: stats,
    budgetExceeded: budget !== undefined && totalCost > budget,
    anomalies: detectAnomalies(stats, anomalyFactor),
  };
}
