import {
  DEFAULT_SETTINGS,
  STORE_IDS,
  type Finish,
  type FinishMode,
  type MatchStatus,
  type MinCondition,
  type OptimizeRequest,
  type OptimizerSettings,
  type ParsedDeckItem,
} from "@/types/deck";

const STORAGE_KEY = "gitpro.be.workspace";

const FINISHES = ["nonfoil", "foil", "etched"] as const satisfies readonly Finish[];
const MATCHES = ["matched", "ambiguous", "unmatched"] as const satisfies readonly MatchStatus[];
const MIN_CONDITIONS = ["NM", "LP", "MP"] as const satisfies readonly MinCondition[];
const FINISH_MODES = ["cheapest", "match_deck"] as const satisfies readonly FinishMode[];

export interface WorkspaceSnapshot {
  items: ParsedDeckItem[];
  settings: OptimizerSettings;
}

export function loadWorkspace(): WorkspaceSnapshot | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (!isRecord(parsed)) return null;
    return {
      items: Array.isArray(parsed.items) ? parsed.items.filter(isParsedDeckItem) : [],
      settings: readSettings(parsed.settings),
    };
  } catch {
    return null;
  }
}

export function saveWorkspace(snapshot: WorkspaceSnapshot): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snapshot));
  } catch {
    // A full or blocked localStorage should not take down the workspace.
  }
}

function readSettings(value: unknown): OptimizerSettings {
  if (!isRecord(value)) return DEFAULT_SETTINGS;
  const minCondition = value.min_condition;
  const finishMode = value.finish_mode;
  const rawStores = value.enabled_stores;
  const stores = Array.isArray(rawStores)
    ? STORE_IDS.filter((storeId) => rawStores.includes(storeId))
    : [];
  return {
    min_condition: isOneOf(minCondition, MIN_CONDITIONS) ? minCondition : DEFAULT_SETTINGS.min_condition,
    finish_mode: isOneOf(finishMode, FINISH_MODES) ? finishMode : DEFAULT_SETTINGS.finish_mode,
    exclude_basic_lands:
      typeof value.exclude_basic_lands === "boolean"
        ? value.exclude_basic_lands
        : DEFAULT_SETTINGS.exclude_basic_lands,
    enabled_stores: stores.length > 0 ? [...stores] : [...DEFAULT_SETTINGS.enabled_stores],
    package_penalty_cents: readPenalty(value.package_penalty_cents),
  };
}

function readPenalty(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) return DEFAULT_SETTINGS.package_penalty_cents;
  return Math.min(1500, Math.max(0, Math.round(value)));
}

function isParsedDeckItem(value: unknown): value is ParsedDeckItem {
  if (!isRecord(value)) return false;
  return (
    typeof value.raw_line === "string" &&
    typeof value.quantity === "number" &&
    Number.isInteger(value.quantity) &&
    value.quantity >= 1 &&
    typeof value.name === "string" &&
    isNullableString(value.set_code) &&
    isNullableString(value.collector_number) &&
    isNullableString(value.scryfall_id) &&
    isNullableString(value.oracle_id) &&
    isOneOf(value.finish, FINISHES) &&
    typeof value.printing_locked === "boolean" &&
    typeof value.is_basic_land === "boolean" &&
    isOneOf(value.match_status, MATCHES) &&
    isNullableString(value.image_uri) &&
    typeof value.available_printings === "number" &&
    Number.isInteger(value.available_printings) &&
    value.available_printings >= 0
  );
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isOneOf<T extends string>(value: unknown, options: readonly T[]): value is T {
  return typeof value === "string" && (options as readonly string[]).includes(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function buildOptimizeRequest(items: ParsedDeckItem[], settings: OptimizerSettings): OptimizeRequest | null {
  const payload = items.flatMap((item) => {
    if (!item.oracle_id && !item.scryfall_id) return [];
    return [
      {
        oracle_id: item.oracle_id,
        scryfall_id: item.scryfall_id,
        quantity: item.quantity,
        finish: item.finish,
        printing_locked: item.printing_locked && item.scryfall_id !== null,
      },
    ];
  });
  const enabledStores = STORE_IDS.filter((storeId) => settings.enabled_stores.includes(storeId));
  if (payload.length === 0 || enabledStores.length === 0) return null;
  return {
    items: payload,
    min_condition: settings.min_condition,
    finish_mode: settings.finish_mode,
    exclude_basic_lands: settings.exclude_basic_lands,
    enabled_stores: enabledStores,
    package_penalty_cents: settings.package_penalty_cents,
  };
}
