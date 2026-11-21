import { loadWorkspace, saveWorkspace, type WorkspaceSnapshot } from "@/lib/storage";
import { DEFAULT_SETTINGS, type OptimizerSettings, type ParsedDeckItem } from "@/types/deck";

const emptySnapshot: WorkspaceSnapshot = {
  items: [],
  settings: DEFAULT_SETTINGS,
};

let snapshot: WorkspaceSnapshot = emptySnapshot;
let hydrated = false;
const listeners = new Set<() => void>();

export function subscribeWorkspace(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getWorkspaceSnapshot(): WorkspaceSnapshot {
  hydrateFromStorage();
  return snapshot;
}

export function getServerWorkspaceSnapshot(): WorkspaceSnapshot {
  return emptySnapshot;
}

export function setWorkspaceItems(items: ParsedDeckItem[]): void {
  commit({ ...getWorkspaceSnapshot(), items });
}

export function mapWorkspaceItems(updater: (items: ParsedDeckItem[]) => ParsedDeckItem[]): void {
  const current = getWorkspaceSnapshot();
  commit({ ...current, items: updater(current.items) });
}

export function setWorkspaceSettings(settings: OptimizerSettings): void {
  commit({ ...getWorkspaceSnapshot(), settings });
}

function commit(next: WorkspaceSnapshot): void {
  snapshot = next;
  saveWorkspace(snapshot);
  for (const listener of listeners) listener();
}

function hydrateFromStorage(): void {
  if (hydrated || typeof window === "undefined") return;
  hydrated = true;
  const saved = loadWorkspace();
  if (!saved || (saved.items.length === 0 && sameSettings(saved.settings, DEFAULT_SETTINGS))) return;
  snapshot = saved;
}

function sameSettings(left: OptimizerSettings, right: OptimizerSettings): boolean {
  return (
    left.min_condition === right.min_condition &&
    left.finish_mode === right.finish_mode &&
    left.exclude_basic_lands === right.exclude_basic_lands &&
    left.package_penalty_cents === right.package_penalty_cents &&
    left.enabled_stores.length === right.enabled_stores.length &&
    left.enabled_stores.every((storeId, index) => storeId === right.enabled_stores[index])
  );
}
