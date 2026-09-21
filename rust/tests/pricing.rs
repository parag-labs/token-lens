use std::collections::HashMap;

use token_lens::{
    cost_of, round6, ChainedPricing, FilePricing, ModelPrice, PricingProvider, StaticPricing,
};

fn approx(got: f64, want: f64) {
    assert!((got - want).abs() < 1e-9, "got {got} want {want}");
}

#[test]
fn cost_of_known_model() {
    // 1M in + 1M out on gpt-4o = 2.5 + 10.0
    approx(cost_of("gpt-4o", 1_000_000, 1_000_000), 12.5);
}

#[test]
fn cost_scales_linearly() {
    approx(cost_of("gpt-4o-mini", 1_000_000, 0), 0.15);
    approx(cost_of("gpt-4o-mini", 500_000, 0), 0.075);
}

#[test]
fn cost_of_zero_tokens() {
    approx(cost_of("gpt-4o", 0, 0), 0.0);
}

#[test]
fn cost_of_unknown_model_is_zero() {
    approx(cost_of("not-a-real-model", 100, 100), 0.0);
}

#[test]
fn static_pricing_unknown_model_errors() {
    let p = StaticPricing::default();
    let err = p.price_for("nope", None).unwrap_err();
    assert_eq!(err.0, "nope");
}

#[test]
fn static_pricing_custom_table() {
    let mut table = HashMap::new();
    table.insert("toy".to_string(), ModelPrice::new(1.0, 2.0));
    let p = StaticPricing::new(Some(table));
    approx(p.cost("toy", 1_000_000, 1_000_000, None).unwrap(), 3.0);
    // a custom table does not fall back to the embedded defaults
    assert!(p.price_for("gpt-4o", None).is_err());
}

const PRICE_BOOK: &str = r#"{
  "prices": [
    {"model": "toy", "input_per_million": 1.0, "output_per_million": 2.0, "effective_from": 0},
    {"model": "toy", "input_per_million": 4.0, "output_per_million": 8.0, "effective_from": 1000}
  ]
}"#;

#[test]
fn file_pricing_point_in_time() {
    let f = FilePricing::from_json(PRICE_BOOK).unwrap();
    // before the price change -> old price
    approx(
        f.price_for("toy", Some(500.0)).unwrap().input_per_million,
        1.0,
    );
    // after the change -> new price
    approx(
        f.price_for("toy", Some(2000.0)).unwrap().input_per_million,
        4.0,
    );
}

#[test]
fn file_pricing_none_uses_newest() {
    let f = FilePricing::from_json(PRICE_BOOK).unwrap();
    approx(f.price_for("toy", None).unwrap().input_per_million, 4.0);
}

#[test]
fn file_pricing_before_any_entry_uses_oldest() {
    let book = r#"{"prices":[{"model":"toy","input_per_million":5.0,"output_per_million":5.0,"effective_from":900}]}"#;
    let f = FilePricing::from_json(book).unwrap();
    approx(
        f.price_for("toy", Some(10.0)).unwrap().input_per_million,
        5.0,
    );
}

#[test]
fn file_pricing_unknown_model() {
    let f = FilePricing::from_json(PRICE_BOOK).unwrap();
    assert!(f.price_for("ghost", None).is_err());
}

#[test]
fn file_pricing_sorts_out_of_order_entries() {
    let book = r#"{"prices":[
      {"model":"toy","input_per_million":4.0,"output_per_million":8.0,"effective_from":1000},
      {"model":"toy","input_per_million":1.0,"output_per_million":2.0,"effective_from":0}
    ]}"#;
    let f = FilePricing::from_json(book).unwrap();
    approx(
        f.price_for("toy", Some(500.0)).unwrap().input_per_million,
        1.0,
    );
}

#[test]
fn file_pricing_rejects_malformed_json() {
    assert!(FilePricing::from_json("{ not json").is_err());
}

#[test]
fn chained_pricing_falls_back() {
    let mut remote = HashMap::new();
    remote.insert("custom".to_string(), ModelPrice::new(9.0, 9.0));
    let chain = ChainedPricing::new(vec![
        Box::new(StaticPricing::new(Some(remote))),
        Box::new(StaticPricing::default()),
    ]);
    // resolved by the first provider
    approx(
        chain.price_for("custom", None).unwrap().input_per_million,
        9.0,
    );
    // missing from the first, found in the embedded fallback
    approx(
        chain.price_for("gpt-4o", None).unwrap().input_per_million,
        2.5,
    );
}

#[test]
fn chained_pricing_all_miss() {
    let chain = ChainedPricing::new(vec![Box::new(StaticPricing::new(Some(HashMap::new())))]);
    assert!(chain.price_for("ghost", None).is_err());
}

#[test]
#[should_panic(expected = "at least one provider")]
fn chained_pricing_requires_a_provider() {
    ChainedPricing::new(vec![]);
}

#[test]
fn round6_rounds_to_six_places() {
    approx(round6(0.1234564), 0.123456);
    approx(round6(1.0 / 3.0), 0.333333);
}
