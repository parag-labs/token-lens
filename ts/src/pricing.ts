/**
 * Model pricing and per-call cost computation.
 *
 * Pricing is metering's second half: you *count* usage, then you *rate* it
 * against a price book. Providers don't publish a live market feed for tokens -
 * list prices change a few times a year - so the industry pattern isn't a
 * ticker, it's a versioned, effective-dated price book behind a small provider
 * interface:
 *
 * - `StaticPricing`  - the embedded default table (always available).
 * - `FilePricing`    - a versioned JSON price book, with each entry carrying an
 *   `effectiveFrom` so a usage record is rated against the price that was in
 *   effect at *its* timestamp (point-in-time rating).
 * - `ChainedPricing` - try providers in order (e.g. a remote catalog first) and
 *   fall back to the embedded table, so rating never hard-fails.
 *
 * Prices are per 1M tokens (USD), matching how providers publish them.
 */

export interface ModelPrice {
  inputPerMillion: number;
  outputPerMillion: number;
}

/** Illustrative list prices (USD / 1M tokens). Update as vendors change them. */
export const PRICES: Record<string, ModelPrice> = {
  // OpenAI
  "gpt-4o": { inputPerMillion: 2.5, outputPerMillion: 10.0 },
  "gpt-4o-mini": { inputPerMillion: 0.15, outputPerMillion: 0.6 },
  "gpt-4.1": { inputPerMillion: 2.0, outputPerMillion: 8.0 },
  "gpt-4.1-mini": { inputPerMillion: 0.4, outputPerMillion: 1.6 },
  "gpt-4.1-nano": { inputPerMillion: 0.1, outputPerMillion: 0.4 },
  o3: { inputPerMillion: 2.0, outputPerMillion: 8.0 },
  "o3-mini": { inputPerMillion: 1.1, outputPerMillion: 4.4 },
  "o4-mini": { inputPerMillion: 1.1, outputPerMillion: 4.4 },
  // Anthropic
  "claude-opus-4": { inputPerMillion: 15.0, outputPerMillion: 75.0 },
  "claude-sonnet-4": { inputPerMillion: 3.0, outputPerMillion: 15.0 },
  "claude-3.7-sonnet": { inputPerMillion: 3.0, outputPerMillion: 15.0 },
  "claude-3.5-sonnet": { inputPerMillion: 3.0, outputPerMillion: 15.0 },
  "claude-3.5-haiku": { inputPerMillion: 0.8, outputPerMillion: 4.0 },
  "claude-3-haiku": { inputPerMillion: 0.25, outputPerMillion: 1.25 },
  // Google
  "gemini-2.5-pro": { inputPerMillion: 1.25, outputPerMillion: 10.0 },
  "gemini-2.5-flash": { inputPerMillion: 0.3, outputPerMillion: 2.5 },
  "gemini-2.0-flash": { inputPerMillion: 0.1, outputPerMillion: 0.4 },
  "gemini-1.5-pro": { inputPerMillion: 1.25, outputPerMillion: 5.0 },
  "gemini-1.5-flash": { inputPerMillion: 0.075, outputPerMillion: 0.3 },
  // Meta Llama
  "llama-3.3-70b": { inputPerMillion: 0.2, outputPerMillion: 0.2 },
  "llama-3.1-405b": { inputPerMillion: 3.5, outputPerMillion: 3.5 },
  "llama-3.1-8b": { inputPerMillion: 0.05, outputPerMillion: 0.05 },
  // Mistral
  "mistral-large": { inputPerMillion: 2.0, outputPerMillion: 6.0 },
  "mistral-small": { inputPerMillion: 0.2, outputPerMillion: 0.6 },
  // DeepSeek
  "deepseek-chat": { inputPerMillion: 0.27, outputPerMillion: 1.1 },
  "deepseek-reasoner": { inputPerMillion: 0.55, outputPerMillion: 2.19 },
  // xAI
  "grok-2": { inputPerMillion: 2.0, outputPerMillion: 10.0 },
};

/** Thrown when no provider can price a model. */
export class UnknownModelError extends Error {
  constructor(public readonly model: string) {
    super(`unknown model "${model}"`);
    this.name = "UnknownModelError";
  }
}

/** Rounds to six decimal places, matching the other language ports. */
export function round6(v: number): number {
  return Math.round(v * 1_000_000) / 1_000_000;
}

function roundCost(price: ModelPrice, inputTokens: number, outputTokens: number): number {
  return round6(
    (inputTokens / 1_000_000) * price.inputPerMillion +
      (outputTokens / 1_000_000) * price.outputPerMillion,
  );
}

/** Resolves a model (optionally at a point in time) to a `ModelPrice`. */
export abstract class PricingProvider {
  /** `at` is a unix timestamp; `undefined` means "current". */
  abstract priceFor(model: string, at?: number): ModelPrice;

  /** Rates a call against this provider. */
  cost(model: string, inputTokens: number, outputTokens: number, at?: number): number {
    return roundCost(this.priceFor(model, at), inputTokens, outputTokens);
  }
}

/** A flat, always-current table. This is the default provider. */
export class StaticPricing extends PricingProvider {
  private readonly prices: Record<string, ModelPrice>;

  constructor(prices?: Record<string, ModelPrice>) {
    super();
    this.prices = { ...(prices ?? PRICES) };
  }

  priceFor(model: string, _at?: number): ModelPrice {
    const price = this.prices[model];
    if (price === undefined) throw new UnknownModelError(model);
    return price;
  }
}

/** One dated row in a versioned price book. */
export interface PriceEntry {
  model: string;
  price: ModelPrice;
  /** unix seconds; 0 = "since forever" */
  effectiveFrom: number;
}

/**
 * A versioned price book. Each model may carry several dated entries; a lookup
 * returns the newest entry whose `effectiveFrom` is at or before the usage
 * timestamp, so back-dated recomputes stay correct.
 */
export class FilePricing extends PricingProvider {
  private readonly byModel = new Map<string, PriceEntry[]>();

  constructor(entries: PriceEntry[]) {
    super();
    for (const e of entries) {
      const list = this.byModel.get(e.model);
      if (list) list.push(e);
      else this.byModel.set(e.model, [e]);
    }
    for (const list of this.byModel.values()) {
      list.sort((a, b) => a.effectiveFrom - b.effectiveFrom);
    }
  }

  /** Parses a price book document. */
  static fromJson(text: string): FilePricing {
    const doc = JSON.parse(text) as {
      prices?: Array<{
        model: string;
        input_per_million: number;
        output_per_million: number;
        effective_from?: number;
      }>;
    };
    const entries: PriceEntry[] = (doc.prices ?? []).map((row) => ({
      model: row.model,
      price: {
        inputPerMillion: Number(row.input_per_million),
        outputPerMillion: Number(row.output_per_million),
      },
      effectiveFrom: Number(row.effective_from ?? 0),
    }));
    return new FilePricing(entries);
  }

  priceFor(model: string, at?: number): ModelPrice {
    const history = this.byModel.get(model);
    if (!history || history.length === 0) throw new UnknownModelError(model);
    if (at === undefined) return history[history.length - 1].price; // newest
    let chosen = history[0];
    for (const e of history) {
      if (e.effectiveFrom <= at) chosen = e;
    }
    return chosen.price;
  }
}

/**
 * Try each provider in order, falling back to the next on an unknown model.
 *
 * This is how you wire a remote catalog in production without risking billing:
 * put the remote provider first and the embedded `StaticPricing` last, so a
 * remote miss (or a provider that failed to load) still rates against defaults.
 */
export class ChainedPricing extends PricingProvider {
  private readonly providers: PricingProvider[];

  constructor(...providers: PricingProvider[]) {
    super();
    if (providers.length === 0) throw new Error("ChainedPricing needs at least one provider");
    this.providers = providers;
  }

  priceFor(model: string, at?: number): ModelPrice {
    for (const p of this.providers) {
      try {
        return p.priceFor(model, at);
      } catch (err) {
        if (err instanceof UnknownModelError) continue;
        throw err;
      }
    }
    throw new UnknownModelError(model);
  }
}

/** The default provider used by `costOf`. */
export const DEFAULT_PROVIDER: PricingProvider = new StaticPricing();

/** Rates a call against the default price book, returning 0 for an unknown model. */
export function costOf(model: string, inputTokens: number, outputTokens: number): number {
  try {
    return DEFAULT_PROVIDER.cost(model, inputTokens, outputTokens);
  } catch (err) {
    if (err instanceof UnknownModelError) return 0;
    throw err;
  }
}
