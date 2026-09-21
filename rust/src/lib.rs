//! token-lens: LLM cost attribution.
//!
//! Count usage, rate it against a versioned price book, then attribute spend
//! across a dimension (feature / tenant / model) and surface budget breaches,
//! cost anomalies, and gradual cost creep.
//!
//! ```
//! use token_lens::{build_report, Dimension, UsageRecord};
//!
//! let records = vec![
//!     UsageRecord::new("gpt-4o", 1_000_000, 1_000_000, 120.0, "search", "acme", 0.0),
//! ];
//! let report = build_report(&records, Dimension::Feature, Some(10.0), 3.0);
//! assert_eq!(report.total_cost, 12.5);
//! assert!(report.budget_exceeded);
//! ```

pub mod pricing;
pub mod tracer;

pub use pricing::{
    cost_of, round6, ChainedPricing, FilePricing, ModelPrice, PriceEntry, PricingProvider,
    StaticPricing, UnknownModelError,
};
pub use tracer::{
    aggregate, build_report, detect_anomalies, detect_creep, Anomaly, Creep, Dimension,
    DimensionStat, Report, UsageRecord,
};
