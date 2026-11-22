export type Finish = "nonfoil" | "foil" | "etched";
export type MatchStatus = "matched" | "ambiguous" | "unmatched";
export type Condition = "NM" | "LP" | "MP" | "HP";
export type MinCondition = "NM" | "LP" | "MP";
export type FinishMode = "cheapest" | "match_deck";
export type UnmatchedReason = "unmatched" | "out_of_stock";
export type PriceSource = "snapshot" | "live";
export type StoreId = "card_kingdom" | "mana_pool" | "card_trader";

export const STORE_IDS = ["card_kingdom", "mana_pool", "card_trader"] as const satisfies readonly StoreId[];

export const STORE_LABELS: Record<StoreId, string> = {
  card_kingdom: "Card Kingdom",
  mana_pool: "Mana Pool",
  card_trader: "CardTrader Zero",
};

export const MIN_CONDITIONS = ["NM", "LP", "MP"] as const satisfies readonly MinCondition[];

export const MIN_CONDITION_TITLES: Record<MinCondition, string> = {
  NM: "Near Mint",
  LP: "Lightly Played",
  MP: "Moderately Played",
};

export interface ParsedDeckItem {
  raw_line: string;
  quantity: number;
  name: string;
  set_code: string | null;
  collector_number: string | null;
  scryfall_id: string | null;
  oracle_id: string | null;
  finish: Finish;
  printing_locked: boolean;
  is_basic_land: boolean;
  match_status: MatchStatus;
  image_uri: string | null;
  available_printings: number;
}

export interface OptimizerSettings {
  min_condition: MinCondition;
  finish_mode: FinishMode;
  exclude_basic_lands: boolean;
  enabled_stores: StoreId[];
  package_penalty_cents: number;
}

export interface OptimizeItem {
  oracle_id: string | null;
  scryfall_id: string | null;
  quantity: number;
  finish: Finish;
  printing_locked: boolean;
}

export interface OptimizeRequest {
  items: OptimizeItem[];
  min_condition: MinCondition;
  finish_mode: FinishMode;
  exclude_basic_lands: boolean;
  enabled_stores: StoreId[];
  package_penalty_cents: number;
}

export interface BucketCard {
  name: string;
  quantity: number;
  scryfall_id: string;
  oracle_id: string;
  set_code: string;
  collector_number: string;
  finish: Finish;
  condition: Condition;
  unit_price_cents: number;
  line_total_cents: number;
  store_sku: string | null;
  image_uri: string | null;
}

export interface StoreBucket {
  store_id: string;
  store_name: string;
  cards: BucketCard[];
  subtotal_cents: number;
  shipping_cents: number;
  estimated_packages: number;
  free_shipping_delta_cents: number | null;
  checkout_url: string;
  mass_entry_text: string;
  price_source: PriceSource;
  notice: string | null;
}

export interface SavingsSummary {
  optimized_total_cents: number;
  baseline_store_id: string | null;
  baseline_store_name: string | null;
  baseline_total_cents: number | null;
  savings_cents: number | null;
  message: string | null;
}

export interface UnmatchedCard {
  oracle_id: string | null;
  scryfall_id: string | null;
  name: string | null;
  quantity: number;
  finish: Finish;
  reason: UnmatchedReason;
}

export interface OptimizeResponse {
  buckets: StoreBucket[];
  savings: SavingsSummary;
  unmatched: UnmatchedCard[];
  notices: string[];
  alternatives: StoreBucket[];
}

export interface VendorMedian {
  store_id: string;
  store_name: string;
  median_cents: number | null;
}

export interface FinishPrices {
  finish: "nonfoil" | "foil";
  vendors: VendorMedian[];
}

export interface CardPrinting {
  scryfall_id: string;
  oracle_id: string;
  name: string;
  set_code: string;
  collector_number: string;
  image_uri: string | null;
  finishes: string[];
  prices: FinishPrices[];
}

export interface PrintingsResponse {
  oracle_id: string;
  name: string;
  printings: CardPrinting[];
}

export const DEFAULT_SETTINGS: OptimizerSettings = {
  min_condition: "LP",
  finish_mode: "cheapest",
  exclude_basic_lands: true,
  enabled_stores: ["card_kingdom", "mana_pool", "card_trader"],
  package_penalty_cents: 300,
};
