//! Model pricing and per-call cost computation.
//!
//! Pricing is metering's second half: you *count* usage, then you *rate* it
//! against a price book. Providers don't publish a live market feed for tokens -
//! list prices change a few times a year - so the industry pattern isn't a
//! ticker, it's a versioned, effective-dated price book behind a small provider
//! interface:
//!
//! - [`StaticPricing`]  - the embedded default table (always available).
//! - [`FilePricing`]    - a versioned JSON price book, with each entry carrying
//!   an `effective_from` so a usage record is rated against the price that was
//!   in effect at *its* timestamp (point-in-time rating).
//! - [`ChainedPricing`] - try providers in order (e.g. a remote catalog first)
//!   and fall back to the embedded table, so rating never hard-fails.
//!
//! Prices are per 1M tokens (USD), matching how providers publish them.

use std::collections::HashMap;
use std::fmt;
use std::path::Path;

/// A per-1M-token price pair in USD.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ModelPrice {
    pub input_per_million: f64,
    pub output_per_million: f64,
}

impl ModelPrice {
    pub fn new(input_per_million: f64, output_per_million: f64) -> Self {
        ModelPrice {
            input_per_million,
            output_per_million,
        }
    }
}

/// Returned when no provider can price a model.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UnknownModelError(pub String);

impl fmt::Display for UnknownModelError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "unknown model \"{}\"", self.0)
    }
}

impl std::error::Error for UnknownModelError {}

/// Rounds to six decimal places, matching the other language ports.
pub fn round6(v: f64) -> f64 {
    (v * 1_000_000.0).round() / 1_000_000.0
}

fn round_cost(p: ModelPrice, input_tokens: i64, output_tokens: i64) -> f64 {
    round6(
        input_tokens as f64 / 1_000_000.0 * p.input_per_million
            + output_tokens as f64 / 1_000_000.0 * p.output_per_million,
    )
}

/// Illustrative list prices (USD / 1M tokens). Update as vendors change them.
pub fn default_prices() -> HashMap<String, ModelPrice> {
    let rows: &[(&str, f64, f64)] = &[
        // OpenAI
        ("gpt-4o", 2.5, 10.0),
        ("gpt-4o-mini", 0.15, 0.6),
        ("gpt-4.1", 2.0, 8.0),
        ("gpt-4.1-mini", 0.4, 1.6),
        ("gpt-4.1-nano", 0.1, 0.4),
        ("o3", 2.0, 8.0),
        ("o3-mini", 1.1, 4.4),
        ("o4-mini", 1.1, 4.4),
        // Anthropic
        ("claude-opus-4", 15.0, 75.0),
        ("claude-sonnet-4", 3.0, 15.0),
        ("claude-3.7-sonnet", 3.0, 15.0),
        ("claude-3.5-sonnet", 3.0, 15.0),
        ("claude-3.5-haiku", 0.8, 4.0),
        ("claude-3-haiku", 0.25, 1.25),
        // Google
        ("gemini-2.5-pro", 1.25, 10.0),
        ("gemini-2.5-flash", 0.3, 2.5),
        ("gemini-2.0-flash", 0.1, 0.4),
        ("gemini-1.5-pro", 1.25, 5.0),
        ("gemini-1.5-flash", 0.075, 0.3),
        // Meta Llama
        ("llama-3.3-70b", 0.2, 0.2),
        ("llama-3.1-405b", 3.5, 3.5),
        ("llama-3.1-8b", 0.05, 0.05),
        // Mistral
        ("mistral-large", 2.0, 6.0),
        ("mistral-small", 0.2, 0.6),
        // DeepSeek
        ("deepseek-chat", 0.27, 1.1),
        ("deepseek-reasoner", 0.55, 2.19),
        // xAI
        ("grok-2", 2.0, 10.0),
    ];
    rows.iter()
        .map(|(m, i, o)| ((*m).to_string(), ModelPrice::new(*i, *o)))
        .collect()
}

/// Resolves a model (optionally at a point in time) to a [`ModelPrice`].
pub trait PricingProvider {
    /// `at` is a unix timestamp; `None` means "current".
    fn price_for(&self, model: &str, at: Option<f64>) -> Result<ModelPrice, UnknownModelError>;

    /// Rates a call against this provider.
    fn cost(
        &self,
        model: &str,
        input_tokens: i64,
        output_tokens: i64,
        at: Option<f64>,
    ) -> Result<f64, UnknownModelError> {
        Ok(round_cost(
            self.price_for(model, at)?,
            input_tokens,
            output_tokens,
        ))
    }
}

/// A flat, always-current table. This is the default provider.
pub struct StaticPricing {
    prices: HashMap<String, ModelPrice>,
}

impl StaticPricing {
    /// Builds a provider over the given table, or the embedded defaults when `None`.
    pub fn new(prices: Option<HashMap<String, ModelPrice>>) -> Self {
        StaticPricing {
            prices: prices.unwrap_or_else(default_prices),
        }
    }
}

impl Default for StaticPricing {
    fn default() -> Self {
        StaticPricing::new(None)
    }
}

impl PricingProvider for StaticPricing {
    fn price_for(&self, model: &str, _at: Option<f64>) -> Result<ModelPrice, UnknownModelError> {
        self.prices
            .get(model)
            .copied()
            .ok_or_else(|| UnknownModelError(model.to_string()))
    }
}

/// One dated row in a versioned price book.
#[derive(Debug, Clone, PartialEq)]
pub struct PriceEntry {
    pub model: String,
    pub price: ModelPrice,
    /// unix seconds; 0 = "since forever"
    pub effective_from: f64,
}

/// A versioned price book. Each model may carry several dated entries; a lookup
/// returns the newest entry whose `effective_from` is at or before the usage
/// timestamp, so back-dated recomputes stay correct.
pub struct FilePricing {
    by_model: HashMap<String, Vec<PriceEntry>>,
}

impl FilePricing {
    /// Indexes entries by model, oldest first.
    pub fn new(entries: Vec<PriceEntry>) -> Self {
        let mut by_model: HashMap<String, Vec<PriceEntry>> = HashMap::new();
        for e in entries {
            by_model.entry(e.model.clone()).or_default().push(e);
        }
        for list in by_model.values_mut() {
            list.sort_by(|a, b| {
                a.effective_from
                    .partial_cmp(&b.effective_from)
                    .unwrap_or(std::cmp::Ordering::Equal)
            });
        }
        FilePricing { by_model }
    }

    /// Parses a price book document.
    pub fn from_json(text: &str) -> Result<Self, serde_json::Error> {
        let doc: serde_json::Value = serde_json::from_str(text)?;
        let mut entries = Vec::new();
        if let Some(rows) = doc.get("prices").and_then(|p| p.as_array()) {
            for row in rows {
                entries.push(PriceEntry {
                    model: row["model"].as_str().unwrap_or_default().to_string(),
                    price: ModelPrice::new(
                        row["input_per_million"].as_f64().unwrap_or(0.0),
                        row["output_per_million"].as_f64().unwrap_or(0.0),
                    ),
                    effective_from: row
                        .get("effective_from")
                        .and_then(|v| v.as_f64())
                        .unwrap_or(0.0),
                });
            }
        }
        Ok(FilePricing::new(entries))
    }

    /// Loads a price book from disk.
    pub fn from_file<P: AsRef<Path>>(path: P) -> std::io::Result<Self> {
        let text = std::fs::read_to_string(path)?;
        FilePricing::from_json(&text)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
    }
}

impl PricingProvider for FilePricing {
    fn price_for(&self, model: &str, at: Option<f64>) -> Result<ModelPrice, UnknownModelError> {
        let history = self
            .by_model
            .get(model)
            .filter(|h| !h.is_empty())
            .ok_or_else(|| UnknownModelError(model.to_string()))?;
        match at {
            None => Ok(history[history.len() - 1].price), // newest
            Some(t) => {
                let chosen = history
                    .iter()
                    .rfind(|e| e.effective_from <= t)
                    .unwrap_or(&history[0]);
                Ok(chosen.price)
            }
        }
    }
}

/// Try each provider in order, falling back to the next on an unknown model.
///
/// This is how you wire a remote catalog in production without risking billing:
/// put the remote provider first and the embedded [`StaticPricing`] last, so a
/// remote miss (or a provider that failed to load) still rates against defaults.
pub struct ChainedPricing {
    providers: Vec<Box<dyn PricingProvider>>,
}

impl ChainedPricing {
    /// Panics if given no providers.
    pub fn new(providers: Vec<Box<dyn PricingProvider>>) -> Self {
        assert!(
            !providers.is_empty(),
            "ChainedPricing needs at least one provider"
        );
        ChainedPricing { providers }
    }
}

impl PricingProvider for ChainedPricing {
    fn price_for(&self, model: &str, at: Option<f64>) -> Result<ModelPrice, UnknownModelError> {
        for p in &self.providers {
            if let Ok(price) = p.price_for(model, at) {
                return Ok(price);
            }
        }
        Err(UnknownModelError(model.to_string()))
    }
}

/// Rates a call against the embedded default table, returning 0 for an unknown model.
pub fn cost_of(model: &str, input_tokens: i64, output_tokens: i64) -> f64 {
    StaticPricing::default()
        .cost(model, input_tokens, output_tokens, None)
        .unwrap_or(0.0)
}
