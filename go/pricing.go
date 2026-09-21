// Package tokenlens computes LLM call costs from a versioned price book and
// attributes spend across dimensions (feature / tenant / model).
//
// Pricing is metering's second half: you count usage, then you rate it against a
// price book. Providers don't publish a live feed for token prices, so the
// pattern is a versioned, effective-dated price book behind a small interface:
//
//   - StaticPricing  - the embedded default table (always available).
//   - FilePricing    - a versioned JSON price book; each entry carries an
//     EffectiveFrom so a usage record is rated against the price in effect at
//     its own timestamp (point-in-time rating).
//   - ChainedPricing - try providers in order (e.g. a remote catalog first) and
//     fall back to the embedded table, so rating never hard-fails.
//
// Prices are per 1M tokens (USD), matching how providers publish them.
package tokenlens

import (
	"encoding/json"
	"fmt"
	"math"
	"os"
	"sort"
)

// ModelPrice is a per-1M-token price pair in USD.
type ModelPrice struct {
	InputPerMillion  float64
	OutputPerMillion float64
}

// Prices holds illustrative list prices (USD / 1M tokens). Update as vendors change them.
var Prices = map[string]ModelPrice{
	// OpenAI
	"gpt-4o":       {2.5, 10.0},
	"gpt-4o-mini":  {0.15, 0.6},
	"gpt-4.1":      {2.0, 8.0},
	"gpt-4.1-mini": {0.4, 1.6},
	"gpt-4.1-nano": {0.1, 0.4},
	"o3":           {2.0, 8.0},
	"o3-mini":      {1.1, 4.4},
	"o4-mini":      {1.1, 4.4},
	// Anthropic
	"claude-opus-4":     {15.0, 75.0},
	"claude-sonnet-4":   {3.0, 15.0},
	"claude-3.7-sonnet": {3.0, 15.0},
	"claude-3.5-sonnet": {3.0, 15.0},
	"claude-3.5-haiku":  {0.8, 4.0},
	"claude-3-haiku":    {0.25, 1.25},
	// Google
	"gemini-2.5-pro":   {1.25, 10.0},
	"gemini-2.5-flash": {0.3, 2.5},
	"gemini-2.0-flash": {0.1, 0.4},
	"gemini-1.5-pro":   {1.25, 5.0},
	"gemini-1.5-flash": {0.075, 0.3},
	// Meta Llama
	"llama-3.3-70b":  {0.2, 0.2},
	"llama-3.1-405b": {3.5, 3.5},
	"llama-3.1-8b":   {0.05, 0.05},
	// Mistral
	"mistral-large": {2.0, 6.0},
	"mistral-small": {0.2, 0.6},
	// DeepSeek
	"deepseek-chat":     {0.27, 1.1},
	"deepseek-reasoner": {0.55, 2.19},
	// xAI
	"grok-2": {2.0, 10.0},
}

// UnknownModelError is returned when no provider can price a model.
type UnknownModelError struct{ Model string }

func (e *UnknownModelError) Error() string {
	return fmt.Sprintf("unknown model %q", e.Model)
}

// Round6 rounds to six decimal places, matching the other language ports.
func Round6(v float64) float64 {
	return math.Round(v*1_000_000.0) / 1_000_000.0
}

func roundCost(p ModelPrice, inputTokens, outputTokens int64) float64 {
	return Round6(
		float64(inputTokens)/1_000_000.0*p.InputPerMillion +
			float64(outputTokens)/1_000_000.0*p.OutputPerMillion)
}

// PricingProvider resolves a model (optionally at a point in time) to a ModelPrice.
type PricingProvider interface {
	// PriceFor returns the price for a model. A nil `at` means "current".
	PriceFor(model string, at *float64) (ModelPrice, error)
}

// Cost rates a usage record against a provider.
func Cost(p PricingProvider, model string, inputTokens, outputTokens int64, at *float64) (float64, error) {
	price, err := p.PriceFor(model, at)
	if err != nil {
		return 0, err
	}
	return roundCost(price, inputTokens, outputTokens), nil
}

// StaticPricing is a flat, always-current table. This is the default provider.
type StaticPricing struct{ prices map[string]ModelPrice }

// NewStaticPricing copies the given table, or the embedded defaults when nil.
func NewStaticPricing(prices map[string]ModelPrice) *StaticPricing {
	src := prices
	if src == nil {
		src = Prices
	}
	cp := make(map[string]ModelPrice, len(src))
	for k, v := range src {
		cp[k] = v
	}
	return &StaticPricing{prices: cp}
}

// PriceFor implements PricingProvider.
func (s *StaticPricing) PriceFor(model string, _ *float64) (ModelPrice, error) {
	price, ok := s.prices[model]
	if !ok {
		return ModelPrice{}, &UnknownModelError{Model: model}
	}
	return price, nil
}

// PriceEntry is one dated row in a versioned price book.
type PriceEntry struct {
	Model         string
	Price         ModelPrice
	EffectiveFrom float64 // unix seconds; 0 = "since forever"
}

// FilePricing is a versioned price book. Each model may carry several dated
// entries; a lookup returns the newest entry whose EffectiveFrom is at or before
// the usage timestamp, so back-dated recomputes stay correct.
type FilePricing struct{ byModel map[string][]PriceEntry }

// NewFilePricing indexes entries by model, oldest first.
func NewFilePricing(entries []PriceEntry) *FilePricing {
	byModel := make(map[string][]PriceEntry)
	for _, e := range entries {
		byModel[e.Model] = append(byModel[e.Model], e)
	}
	for _, list := range byModel {
		sort.SliceStable(list, func(i, j int) bool {
			return list[i].EffectiveFrom < list[j].EffectiveFrom
		})
	}
	return &FilePricing{byModel: byModel}
}

type priceDoc struct {
	Prices []struct {
		Model            string  `json:"model"`
		InputPerMillion  float64 `json:"input_per_million"`
		OutputPerMillion float64 `json:"output_per_million"`
		EffectiveFrom    float64 `json:"effective_from"`
	} `json:"prices"`
}

// FilePricingFromJSON parses a price book document.
func FilePricingFromJSON(text []byte) (*FilePricing, error) {
	var doc priceDoc
	if err := json.Unmarshal(text, &doc); err != nil {
		return nil, err
	}
	entries := make([]PriceEntry, 0, len(doc.Prices))
	for _, row := range doc.Prices {
		entries = append(entries, PriceEntry{
			Model:         row.Model,
			Price:         ModelPrice{row.InputPerMillion, row.OutputPerMillion},
			EffectiveFrom: row.EffectiveFrom,
		})
	}
	return NewFilePricing(entries), nil
}

// FilePricingFromFile loads a price book from disk.
func FilePricingFromFile(path string) (*FilePricing, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return FilePricingFromJSON(data)
}

// PriceFor implements PricingProvider with point-in-time rating.
func (f *FilePricing) PriceFor(model string, at *float64) (ModelPrice, error) {
	history, ok := f.byModel[model]
	if !ok || len(history) == 0 {
		return ModelPrice{}, &UnknownModelError{Model: model}
	}
	if at == nil {
		return history[len(history)-1].Price, nil // newest
	}
	chosen := history[0]
	for _, e := range history {
		if e.EffectiveFrom <= *at {
			chosen = e
		}
	}
	return chosen.Price, nil
}

// ChainedPricing tries each provider in order, falling back on an unknown model.
//
// This is how you wire a remote catalog in production without risking billing:
// put the remote provider first and the embedded StaticPricing last, so a remote
// miss (or a provider that failed to load) still rates against defaults.
type ChainedPricing struct{ providers []PricingProvider }

// NewChainedPricing requires at least one provider.
func NewChainedPricing(providers ...PricingProvider) *ChainedPricing {
	if len(providers) == 0 {
		panic("ChainedPricing needs at least one provider")
	}
	return &ChainedPricing{providers: providers}
}

// PriceFor implements PricingProvider.
func (c *ChainedPricing) PriceFor(model string, at *float64) (ModelPrice, error) {
	for _, p := range c.providers {
		if price, err := p.PriceFor(model, at); err == nil {
			return price, nil
		}
	}
	return ModelPrice{}, &UnknownModelError{Model: model}
}

// DefaultProvider is used by CostOf.
var DefaultProvider PricingProvider = NewStaticPricing(nil)

// CostOf rates a call against the default provider, returning 0 for an unknown model.
func CostOf(model string, inputTokens, outputTokens int64) float64 {
	cost, err := Cost(DefaultProvider, model, inputTokens, outputTokens, nil)
	if err != nil {
		return 0
	}
	return cost
}
