//! Usage records and cost/latency aggregation with budget + anomaly detection.
//!
//! Core value: attribute LLM spend to a dimension (feature / tenant / model) and
//! surface budget breaches and cost anomalies - the numbers a FinOps/eng lead
//! asks for. Pure logic; no I/O.

use std::collections::BTreeMap;

use crate::pricing::{cost_of, round6};

/// Which field a rollup groups by.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Dimension {
    Feature,
    Tenant,
    Model,
}

impl Dimension {
    /// Parses a dimension name, mirroring the string API of the other ports.
    pub fn parse(name: &str) -> Option<Dimension> {
        match name {
            "feature" => Some(Dimension::Feature),
            "tenant" => Some(Dimension::Tenant),
            "model" => Some(Dimension::Model),
            _ => None,
        }
    }
}

/// One LLM call: what it cost, how long it took, and who it was for.
#[derive(Debug, Clone)]
pub struct UsageRecord {
    pub model: String,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub latency_ms: f64,
    pub feature: String,
    pub tenant: String,
    /// epoch seconds; used by the rolling-window creep detector. 0 = untimed.
    pub timestamp: f64,
}

impl UsageRecord {
    pub fn new(
        model: &str,
        input_tokens: i64,
        output_tokens: i64,
        latency_ms: f64,
        feature: &str,
        tenant: &str,
        timestamp: f64,
    ) -> Self {
        UsageRecord {
            model: model.to_string(),
            input_tokens,
            output_tokens,
            latency_ms,
            feature: feature.to_string(),
            tenant: tenant.to_string(),
            timestamp,
        }
    }

    /// Rates the record against the embedded default price book.
    pub fn cost(&self) -> f64 {
        cost_of(&self.model, self.input_tokens, self.output_tokens)
    }

    pub fn total_tokens(&self) -> i64 {
        self.input_tokens + self.output_tokens
    }

    fn key(&self, d: Dimension) -> &str {
        match d {
            Dimension::Feature => &self.feature,
            Dimension::Tenant => &self.tenant,
            Dimension::Model => &self.model,
        }
    }
}

/// Accumulated usage for one value of a dimension.
#[derive(Debug, Clone, PartialEq)]
pub struct DimensionStat {
    pub key: String,
    pub calls: i64,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cost: f64,
    pub latency_sum: f64,
}

impl DimensionStat {
    pub fn new(key: &str) -> Self {
        DimensionStat {
            key: key.to_string(),
            calls: 0,
            input_tokens: 0,
            output_tokens: 0,
            cost: 0.0,
            latency_sum: 0.0,
        }
    }

    pub fn avg_latency_ms(&self) -> f64 {
        if self.calls == 0 {
            return 0.0;
        }
        (self.latency_sum / self.calls as f64 * 100.0).round() / 100.0
    }

    pub fn total_tokens(&self) -> i64 {
        self.input_tokens + self.output_tokens
    }
}

/// A dimension that is expensive relative to its peers right now.
#[derive(Debug, Clone, PartialEq)]
pub struct Anomaly {
    pub key: String,
    pub metric: String,
    pub value: f64,
    pub baseline: f64,
    pub factor: f64,
}

/// A dimension whose cost *rate* is rising over time (gradual creep).
///
/// The plain median detector only catches a dimension that is expensive relative
/// to its peers right now. It misses one that is slowly growing but still
/// cheaper than the noisy ones. Comparing a recent window against an earlier
/// baseline window catches that trend.
#[derive(Debug, Clone, PartialEq)]
pub struct Creep {
    pub key: String,
    /// cost per second over the baseline window
    pub baseline_rate: f64,
    /// cost per second over the recent window
    pub recent_rate: f64,
    pub factor: f64,
}

/// Spend by dimension, budget state, and anomalies.
#[derive(Debug, Clone)]
pub struct Report {
    pub total_cost: f64,
    pub total_calls: i64,
    pub by_dimension: BTreeMap<String, DimensionStat>,
    pub budget_exceeded: bool,
    pub anomalies: Vec<Anomaly>,
}

/// Rolls records up by the given dimension.
pub fn aggregate(records: &[UsageRecord], dimension: Dimension) -> BTreeMap<String, DimensionStat> {
    let mut stats: BTreeMap<String, DimensionStat> = BTreeMap::new();
    for r in records {
        let key = r.key(dimension).to_string();
        let s = stats
            .entry(key.clone())
            .or_insert_with(|| DimensionStat::new(&key));
        s.calls += 1;
        s.input_tokens += r.input_tokens;
        s.output_tokens += r.output_tokens;
        s.cost = round6(s.cost + r.cost());
        s.latency_sum += r.latency_ms;
    }
    stats
}

/// Flags dimensions whose cost exceeds `factor` x the median dimension cost.
pub fn detect_anomalies(stats: &BTreeMap<String, DimensionStat>, factor: f64) -> Vec<Anomaly> {
    let mut costs: Vec<f64> = stats.values().map(|s| s.cost).collect();
    if costs.len() < 3 {
        return Vec::new();
    }
    costs.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let median = costs[costs.len() / 2];
    if median <= 0.0 {
        return Vec::new();
    }
    let mut out: Vec<Anomaly> = stats
        .values()
        .filter(|s| s.cost > factor * median)
        .map(|s| Anomaly {
            key: s.key.clone(),
            metric: "cost".to_string(),
            value: s.cost,
            baseline: median,
            factor: (s.cost / median * 100.0).round() / 100.0,
        })
        .collect();
    out.sort_by(|a, b| {
        b.factor
            .partial_cmp(&a.factor)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    out
}

/// Flags dimensions whose cost *rate* rose from a baseline window to a recent one.
///
/// Records with `timestamp < split_time` form the baseline window; the rest form
/// the recent window. Cost is normalized by each window's duration to get a rate
/// (cost/second), so uneven window lengths compare fairly. A dimension is
/// flagged when its recent rate exceeds `factor` times its baseline rate.
///
/// When `split_time` is `None`, the midpoint of the observed timestamp range is
/// used. Records with a timestamp of 0 are treated as untimed and ignored.
pub fn detect_creep(
    records: &[UsageRecord],
    dimension: Dimension,
    split_time: Option<f64>,
    factor: f64,
) -> Vec<Creep> {
    let timed: Vec<&UsageRecord> = records.iter().filter(|r| r.timestamp > 0.0).collect();
    if timed.len() < 2 {
        return Vec::new();
    }

    let lo = timed
        .iter()
        .map(|r| r.timestamp)
        .fold(f64::INFINITY, f64::min);
    let hi = timed
        .iter()
        .map(|r| r.timestamp)
        .fold(f64::NEG_INFINITY, f64::max);
    if hi == lo {
        return Vec::new();
    }

    let split = split_time.unwrap_or((lo + hi) / 2.0);
    let base_dur = (split - lo).max(1e-9);
    let recent_dur = (hi - split).max(1e-9);

    let mut base_cost: BTreeMap<String, f64> = BTreeMap::new();
    let mut recent_cost: BTreeMap<String, f64> = BTreeMap::new();
    for r in &timed {
        let key = r.key(dimension).to_string();
        if r.timestamp < split {
            *base_cost.entry(key).or_insert(0.0) += r.cost();
        } else {
            *recent_cost.entry(key).or_insert(0.0) += r.cost();
        }
    }

    let mut out: Vec<Creep> = Vec::new();
    for (key, rc) in &recent_cost {
        let base_rate = base_cost.get(key).copied().unwrap_or(0.0) / base_dur;
        let recent_rate = rc / recent_dur;
        if base_rate <= 0.0 {
            continue; // no baseline to compare against; new, not creep
        }
        if recent_rate > factor * base_rate {
            out.push(Creep {
                key: key.clone(),
                baseline_rate: (base_rate * 1e9).round() / 1e9,
                recent_rate: (recent_rate * 1e9).round() / 1e9,
                factor: (recent_rate / base_rate * 100.0).round() / 100.0,
            });
        }
    }
    out.sort_by(|a, b| {
        b.factor
            .partial_cmp(&a.factor)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    out
}

/// Aggregates records and evaluates budget + anomalies in one pass.
pub fn build_report(
    records: &[UsageRecord],
    dimension: Dimension,
    budget: Option<f64>,
    anomaly_factor: f64,
) -> Report {
    let stats = aggregate(records, dimension);
    let total_cost = round6(stats.values().map(|s| s.cost).sum());
    let total_calls = stats.values().map(|s| s.calls).sum();
    let anomalies = detect_anomalies(&stats, anomaly_factor);
    Report {
        total_cost,
        total_calls,
        by_dimension: stats,
        budget_exceeded: matches!(budget, Some(b) if total_cost > b),
        anomalies,
    }
}
