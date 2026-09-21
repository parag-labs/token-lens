import { describe, expect, it } from "vitest";
import {
  ChainedPricing,
  FilePricing,
  StaticPricing,
  UnknownModelError,
  costOf,
  round6,
  type ModelPrice,
} from "./pricing";

describe("costOf", () => {
  it("rates a known model", () => {
    // 1M in + 1M out on gpt-4o = 2.5 + 10.0
    expect(costOf("gpt-4o", 1_000_000, 1_000_000)).toBeCloseTo(12.5, 9);
  });

  it("scales linearly with tokens", () => {
    expect(costOf("gpt-4o-mini", 1_000_000, 0)).toBeCloseTo(0.15, 9);
    expect(costOf("gpt-4o-mini", 500_000, 0)).toBeCloseTo(0.075, 9);
  });

  it("is zero for zero tokens", () => {
    expect(costOf("gpt-4o", 0, 0)).toBe(0);
  });

  it("is zero for an unknown model", () => {
    expect(costOf("not-a-real-model", 100, 100)).toBe(0);
  });
});

describe("StaticPricing", () => {
  it("throws UnknownModelError for a missing model", () => {
    const p = new StaticPricing();
    expect(() => p.priceFor("nope")).toThrow(UnknownModelError);
  });

  it("uses a custom table without falling back to defaults", () => {
    const table: Record<string, ModelPrice> = {
      toy: { inputPerMillion: 1.0, outputPerMillion: 2.0 },
    };
    const p = new StaticPricing(table);
    expect(p.cost("toy", 1_000_000, 1_000_000)).toBeCloseTo(3.0, 9);
    expect(() => p.priceFor("gpt-4o")).toThrow(UnknownModelError);
  });

  it("copies the caller's table", () => {
    const table: Record<string, ModelPrice> = {
      toy: { inputPerMillion: 1.0, outputPerMillion: 2.0 },
    };
    const p = new StaticPricing(table);
    delete table.toy; // mutating the caller's object must not affect the provider
    expect(p.priceFor("toy").inputPerMillion).toBe(1.0);
  });
});

const PRICE_BOOK = JSON.stringify({
  prices: [
    { model: "toy", input_per_million: 1.0, output_per_million: 2.0, effective_from: 0 },
    { model: "toy", input_per_million: 4.0, output_per_million: 8.0, effective_from: 1000 },
  ],
});

describe("FilePricing", () => {
  it("rates at a point in time", () => {
    const f = FilePricing.fromJson(PRICE_BOOK);
    expect(f.priceFor("toy", 500).inputPerMillion).toBe(1.0); // before the change
    expect(f.priceFor("toy", 2000).inputPerMillion).toBe(4.0); // after the change
  });

  it("uses the newest entry when no time is given", () => {
    expect(FilePricing.fromJson(PRICE_BOOK).priceFor("toy").inputPerMillion).toBe(4.0);
  });

  it("falls back to the oldest entry for a time before any entry", () => {
    const book = JSON.stringify({
      prices: [
        { model: "toy", input_per_million: 5.0, output_per_million: 5.0, effective_from: 900 },
      ],
    });
    expect(FilePricing.fromJson(book).priceFor("toy", 10).inputPerMillion).toBe(5.0);
  });

  it("throws for an unknown model", () => {
    expect(() => FilePricing.fromJson(PRICE_BOOK).priceFor("ghost")).toThrow(UnknownModelError);
  });

  it("sorts out-of-order entries", () => {
    const book = JSON.stringify({
      prices: [
        { model: "toy", input_per_million: 4.0, output_per_million: 8.0, effective_from: 1000 },
        { model: "toy", input_per_million: 1.0, output_per_million: 2.0, effective_from: 0 },
      ],
    });
    expect(FilePricing.fromJson(book).priceFor("toy", 500).inputPerMillion).toBe(1.0);
  });

  it("defaults a missing effective_from to 0", () => {
    const book = JSON.stringify({
      prices: [{ model: "toy", input_per_million: 2.0, output_per_million: 2.0 }],
    });
    expect(FilePricing.fromJson(book).priceFor("toy", 1).inputPerMillion).toBe(2.0);
  });
});

describe("ChainedPricing", () => {
  it("falls back to the next provider on a miss", () => {
    const remote = new StaticPricing({ custom: { inputPerMillion: 9.0, outputPerMillion: 9.0 } });
    const chain = new ChainedPricing(remote, new StaticPricing());
    expect(chain.priceFor("custom").inputPerMillion).toBe(9.0); // first provider
    expect(chain.priceFor("gpt-4o").inputPerMillion).toBe(2.5); // embedded fallback
  });

  it("throws when every provider misses", () => {
    const chain = new ChainedPricing(new StaticPricing({}));
    expect(() => chain.priceFor("ghost")).toThrow(UnknownModelError);
  });

  it("requires at least one provider", () => {
    expect(() => new ChainedPricing()).toThrow(/at least one provider/);
  });
});

describe("round6", () => {
  it("rounds to six decimal places", () => {
    expect(round6(0.1234564)).toBeCloseTo(0.123456, 9);
    expect(round6(1 / 3)).toBeCloseTo(0.333333, 9);
  });
});
