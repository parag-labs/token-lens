use std::collections::BTreeMap;

use token_lens::{
    aggregate, build_report, detect_anomalies, detect_creep, Dimension, DimensionStat, UsageRecord,
};

fn approx(got: f64, want: f64) {
    assert!((got - want).abs() < 1e-9, "got {got} want {want}");
}

fn rec(model: &str, feature: &str, input: i64, output: i64, latency: f64) -> UsageRecord {
    UsageRecord::new(model, input, output, latency, feature, "acme", 0.0)
}

fn timed(feature: &str, input: i64, output: i64, ts: f64) -> UsageRecord {
    UsageRecord::new("gpt-4o", input, output, 0.0, feature, "acme", ts)
}

fn stats_of(pairs: &[(&str, f64)]) -> BTreeMap<String, DimensionStat> {
    pairs
        .iter()
        .map(|(k, c)| {
            let mut s = DimensionStat::new(k);
            s.cost = *c;
            (k.to_string(), s)
        })
        .collect()
}

#[test]
fn aggregate_groups_by_feature() {
    let records = vec![
        rec("gpt-4o", "search", 1000, 500, 100.0),
        rec("gpt-4o", "search", 2000, 1000, 200.0),
        rec("gpt-4o-mini", "chat", 1000, 1000, 50.0),
    ];
    let stats = aggregate(&records, Dimension::Feature);
    assert_eq!(stats.len(), 2);
    let search = &stats["search"];
    assert_eq!(search.calls, 2);
    assert_eq!(search.input_tokens, 3000);
    assert_eq!(search.output_tokens, 1500);
    assert_eq!(search.total_tokens(), 4500);
    approx(search.avg_latency_ms(), 150.0);
}

#[test]
fn aggregate_by_tenant_and_model() {
    let records = vec![
        UsageRecord::new("gpt-4o", 100, 100, 0.0, "a", "t1", 0.0),
        UsageRecord::new("gpt-4o-mini", 100, 100, 0.0, "b", "t2", 0.0),
    ];
    let by_tenant = aggregate(&records, Dimension::Tenant);
    assert_eq!(by_tenant.len(), 2);
    assert_eq!(by_tenant["t1"].calls, 1);

    let by_model = aggregate(&records, Dimension::Model);
    assert_eq!(by_model["gpt-4o"].calls, 1);
}

#[test]
fn aggregate_empty() {
    assert!(aggregate(&[], Dimension::Feature).is_empty());
}

#[test]
fn dimension_parse() {
    assert_eq!(Dimension::parse("feature"), Some(Dimension::Feature));
    assert_eq!(Dimension::parse("tenant"), Some(Dimension::Tenant));
    assert_eq!(Dimension::parse("model"), Some(Dimension::Model));
    assert_eq!(Dimension::parse("nope"), None);
}

#[test]
fn avg_latency_with_zero_calls() {
    approx(DimensionStat::new("x").avg_latency_ms(), 0.0);
}

#[test]
fn anomalies_need_three_dimensions() {
    let stats = stats_of(&[("a", 1.0), ("b", 100.0)]);
    assert!(detect_anomalies(&stats, 3.0).is_empty());
}

#[test]
fn anomalies_flag_outlier() {
    let stats = stats_of(&[("a", 1.0), ("b", 1.0), ("c", 50.0)]);
    let got = detect_anomalies(&stats, 3.0);
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].key, "c");
    assert_eq!(got[0].metric, "cost");
    approx(got[0].baseline, 1.0);
    approx(got[0].factor, 50.0);
}

#[test]
fn anomalies_zero_median_yields_none() {
    let stats = stats_of(&[("a", 0.0), ("b", 0.0), ("c", 5.0)]);
    assert!(detect_anomalies(&stats, 3.0).is_empty());
}

#[test]
fn anomalies_sorted_by_factor_desc() {
    // median of [1,1,1,10,40] is 1, so both 10 and 40 clear the 3x threshold
    let stats = stats_of(&[("a", 1.0), ("b", 1.0), ("c", 1.0), ("d", 10.0), ("e", 40.0)]);
    let got = detect_anomalies(&stats, 3.0);
    assert_eq!(got.len(), 2);
    assert_eq!(got[0].key, "e");
    assert_eq!(got[1].key, "d");
}

#[test]
fn anomalies_median_is_upper_middle() {
    // with an even count the median is index len/2: median of [1,1,10,40] is 10
    let stats = stats_of(&[("a", 1.0), ("b", 1.0), ("c", 10.0), ("d", 40.0)]);
    let got = detect_anomalies(&stats, 3.0);
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].key, "d");
    approx(got[0].baseline, 10.0);
    approx(got[0].factor, 4.0);
}

#[test]
fn report_totals_and_budget() {
    let records = vec![
        rec("gpt-4o", "search", 1_000_000, 1_000_000, 10.0),
        rec("gpt-4o", "chat", 1_000_000, 1_000_000, 20.0),
    ];
    let report = build_report(&records, Dimension::Feature, Some(20.0), 3.0);
    approx(report.total_cost, 25.0);
    assert_eq!(report.total_calls, 2);
    assert!(report.budget_exceeded);
}

#[test]
fn report_without_budget_never_breaches() {
    let records = vec![rec("gpt-4o", "search", 1_000_000, 1_000_000, 10.0)];
    let report = build_report(&records, Dimension::Feature, None, 3.0);
    assert!(!report.budget_exceeded);
}

#[test]
fn report_budget_exactly_met_is_not_exceeded() {
    let records = vec![rec("gpt-4o", "search", 1_000_000, 1_000_000, 10.0)];
    let report = build_report(&records, Dimension::Feature, Some(12.5), 3.0);
    assert!(!report.budget_exceeded);
}

#[test]
fn creep_flags_rising_rate() {
    let records = vec![
        timed("search", 1000, 1000, 10.0),
        timed("search", 1_000_000, 1_000_000, 150.0),
        timed("search", 1_000_000, 1_000_000, 180.0),
        timed("search", 1_000_000, 1_000_000, 199.0),
    ];
    let got = detect_creep(&records, Dimension::Feature, Some(100.0), 2.0);
    assert_eq!(got.len(), 1);
    assert_eq!(got[0].key, "search");
    assert!(got[0].recent_rate > got[0].baseline_rate);
}

#[test]
fn creep_ignores_new_dimensions() {
    // "chat" only appears in the recent window -> new, not creep
    let records = vec![
        timed("search", 1000, 1000, 10.0),
        timed("chat", 1_000_000, 1_000_000, 150.0),
    ];
    let got = detect_creep(&records, Dimension::Feature, Some(100.0), 2.0);
    assert!(got.iter().all(|c| c.key != "chat"));
}

#[test]
fn creep_needs_two_timed_records() {
    let records = vec![timed("a", 1, 1, 10.0)];
    assert!(detect_creep(&records, Dimension::Feature, None, 2.0).is_empty());
}

#[test]
fn creep_ignores_untimed_records() {
    let records = vec![
        rec("gpt-4o", "a", 1, 1, 1.0), // timestamp 0 -> untimed
        rec("gpt-4o", "a", 1, 1, 1.0),
    ];
    assert!(detect_creep(&records, Dimension::Feature, None, 2.0).is_empty());
}

#[test]
fn creep_identical_timestamps() {
    let records = vec![timed("a", 1, 1, 50.0), timed("a", 1, 1, 50.0)];
    assert!(detect_creep(&records, Dimension::Feature, None, 2.0).is_empty());
}

#[test]
fn creep_default_split_is_midpoint() {
    let records = vec![
        timed("search", 1000, 1000, 10.0),
        timed("search", 1_000_000, 1_000_000, 100.0),
    ];
    let got = detect_creep(&records, Dimension::Feature, None, 2.0);
    assert_eq!(got.len(), 1);
    approx(got[0].factor, 1000.0);
}

#[test]
fn usage_record_helpers() {
    let r = rec("gpt-4o", "search", 1_000_000, 1_000_000, 10.0);
    approx(r.cost(), 12.5);
    assert_eq!(r.total_tokens(), 2_000_000);
}
