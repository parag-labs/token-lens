package tokenlens

import (
	"errors"
	"math"
	"testing"
)

func approx(t *testing.T, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-9 {
		t.Fatalf("got %v want %v", got, want)
	}
}

func TestCostOfKnownModel(t *testing.T) {
	// 1M in + 1M out on gpt-4o = 2.5 + 10.0
	approx(t, CostOf("gpt-4o", 1_000_000, 1_000_000), 12.5)
}

func TestCostOfScalesLinearly(t *testing.T) {
	approx(t, CostOf("gpt-4o-mini", 1_000_000, 0), 0.15)
	approx(t, CostOf("gpt-4o-mini", 500_000, 0), 0.075)
}

func TestCostOfZeroTokens(t *testing.T) {
	approx(t, CostOf("gpt-4o", 0, 0), 0)
}

func TestCostOfUnknownModelReturnsZero(t *testing.T) {
	approx(t, CostOf("not-a-real-model", 100, 100), 0)
}

func TestStaticPricingUnknownModelErrors(t *testing.T) {
	p := NewStaticPricing(nil)
	_, err := p.PriceFor("nope", nil)
	var unknown *UnknownModelError
	if !errors.As(err, &unknown) {
		t.Fatalf("expected UnknownModelError, got %v", err)
	}
	if unknown.Model != "nope" {
		t.Fatalf("error carries model %q", unknown.Model)
	}
}

func TestStaticPricingCustomTable(t *testing.T) {
	p := NewStaticPricing(map[string]ModelPrice{"toy": {1.0, 2.0}})
	cost, err := Cost(p, "toy", 1_000_000, 1_000_000, nil)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, cost, 3.0)
	// the embedded table is not consulted when a custom one is supplied
	if _, err := p.PriceFor("gpt-4o", nil); err == nil {
		t.Fatal("custom table should not fall back to defaults")
	}
}

func TestStaticPricingCopiesTable(t *testing.T) {
	src := map[string]ModelPrice{"toy": {1.0, 2.0}}
	p := NewStaticPricing(src)
	delete(src, "toy") // mutating the caller's map must not affect the provider
	if _, err := p.PriceFor("toy", nil); err != nil {
		t.Fatal("provider should hold its own copy")
	}
}

const priceBook = `{
  "prices": [
    {"model": "toy", "input_per_million": 1.0, "output_per_million": 2.0, "effective_from": 0},
    {"model": "toy", "input_per_million": 4.0, "output_per_million": 8.0, "effective_from": 1000}
  ]
}`

func TestFilePricingPointInTime(t *testing.T) {
	f, err := FilePricingFromJSON([]byte(priceBook))
	if err != nil {
		t.Fatal(err)
	}
	before, after := 500.0, 2000.0

	// rated at a time before the price change -> old price
	p, err := f.PriceFor("toy", &before)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, p.InputPerMillion, 1.0)

	// rated after the change -> new price
	p, err = f.PriceFor("toy", &after)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, p.InputPerMillion, 4.0)
}

func TestFilePricingNilAtUsesNewest(t *testing.T) {
	f, _ := FilePricingFromJSON([]byte(priceBook))
	p, err := f.PriceFor("toy", nil)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, p.InputPerMillion, 4.0)
}

func TestFilePricingBeforeAnyEntryUsesOldest(t *testing.T) {
	book := `{"prices":[{"model":"toy","input_per_million":5.0,"output_per_million":5.0,"effective_from":900}]}`
	f, _ := FilePricingFromJSON([]byte(book))
	early := 10.0
	p, err := f.PriceFor("toy", &early)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, p.InputPerMillion, 5.0)
}

func TestFilePricingUnknownModel(t *testing.T) {
	f, _ := FilePricingFromJSON([]byte(priceBook))
	if _, err := f.PriceFor("ghost", nil); err == nil {
		t.Fatal("expected an error for an unknown model")
	}
}

func TestFilePricingOutOfOrderEntriesAreSorted(t *testing.T) {
	book := `{"prices":[
	  {"model":"toy","input_per_million":4.0,"output_per_million":8.0,"effective_from":1000},
	  {"model":"toy","input_per_million":1.0,"output_per_million":2.0,"effective_from":0}
	]}`
	f, _ := FilePricingFromJSON([]byte(book))
	before := 500.0
	p, _ := f.PriceFor("toy", &before)
	approx(t, p.InputPerMillion, 1.0)
}

func TestChainedPricingFallsBack(t *testing.T) {
	remote := NewStaticPricing(map[string]ModelPrice{"custom": {9.0, 9.0}})
	chain := NewChainedPricing(remote, NewStaticPricing(nil))

	// resolved by the first provider
	p, err := chain.PriceFor("custom", nil)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, p.InputPerMillion, 9.0)

	// missing from the first, found in the embedded fallback
	p, err = chain.PriceFor("gpt-4o", nil)
	if err != nil {
		t.Fatal(err)
	}
	approx(t, p.InputPerMillion, 2.5)
}

func TestChainedPricingAllMiss(t *testing.T) {
	chain := NewChainedPricing(NewStaticPricing(map[string]ModelPrice{}))
	if _, err := chain.PriceFor("ghost", nil); err == nil {
		t.Fatal("expected an error when no provider can price the model")
	}
}

func TestChainedPricingRequiresAProvider(t *testing.T) {
	defer func() {
		if recover() == nil {
			t.Fatal("expected a panic with no providers")
		}
	}()
	NewChainedPricing()
}

func TestRound6(t *testing.T) {
	approx(t, Round6(0.1234564), 0.123456)
	approx(t, Round6(1.0/3.0), 0.333333)
}
