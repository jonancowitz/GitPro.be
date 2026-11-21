"use client";

import { useState, useSyncExternalStore } from "react";

import { ImportBar } from "@/components/ImportBar";
import { OptimizerControls } from "@/components/OptimizerControls";
import { ResultsDashboard } from "@/components/ResultsDashboard";
import { TriageGrid } from "@/components/TriageGrid";
import { errorMessage, optimizeDeck, parseDeckFile, parseDeckText, parseDeckUrl } from "@/lib/api";
import { buildOptimizeRequest } from "@/lib/storage";
import {
  getServerWorkspaceSnapshot,
  getWorkspaceSnapshot,
  mapWorkspaceItems,
  setWorkspaceItems,
  setWorkspaceSettings,
  subscribeWorkspace,
} from "@/lib/workspace-store";
import { STORE_IDS, type OptimizeResponse, type ParsedDeckItem, type StoreId } from "@/types/deck";

export function Workspace() {
  const { items, settings } = useSyncExternalStore(
    subscribeWorkspace,
    getWorkspaceSnapshot,
    getServerWorkspaceSnapshot,
  );
  const [result, setResult] = useState<OptimizeResponse | null>(null);
  const [pricedKey, setPricedKey] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);
  const [optimizeError, setOptimizeError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [storeHint, setStoreHint] = useState<string | null>(null);

  const request = buildOptimizeRequest(items, settings);
  const requestKey = request ? JSON.stringify(request) : null;
  const stale = result !== null && pricedKey !== requestKey;
  const blockedReason =
    items.length > 0 && request === null ? "These cards need a Scryfall match before they can be priced." : null;

  function applyDeck(next: ParsedDeckItem[]) {
    setWorkspaceItems(next);
    setResult(null);
    setPricedKey(null);
    setOptimizeError(null);
  }

  async function runImport(action: () => Promise<ParsedDeckItem[]>) {
    setImporting(true);
    setImportError(null);
    try {
      const next = await action();
      if (next.length === 0) {
        setImportError("That import didn't contain any cards.");
        return;
      }
      applyDeck(next);
    } catch (error) {
      setImportError(errorMessage(error));
    } finally {
      setImporting(false);
    }
  }

  function toggleStore(storeId: StoreId) {
    const enabled = settings.enabled_stores.includes(storeId);
    if (enabled && settings.enabled_stores.length === 1) {
      setStoreHint("Leave at least one store on.");
      return;
    }
    const selected = new Set(settings.enabled_stores);
    if (enabled) selected.delete(storeId);
    else selected.add(storeId);
    setStoreHint(null);
    setWorkspaceSettings({
      ...settings,
      enabled_stores: STORE_IDS.filter((id) => selected.has(id)),
    });
  }

  async function optimize() {
    if (!request || !requestKey) {
      setOptimizeError("None of these cards resolved to a Scryfall printing the optimizer can price.");
      return;
    }
    const key = requestKey;
    setOptimizing(true);
    setOptimizeError(null);
    try {
      const response = await optimizeDeck(request);
      setResult(response);
      setPricedKey(key);
    } catch (error) {
      setOptimizeError(errorMessage(error));
    } finally {
      setOptimizing(false);
    }
  }

  return (
    <div className="mx-auto flex w-full min-w-0 max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6">
      <ImportBar
        busy={importing || optimizing}
        error={importError}
        hasDeck={items.length > 0}
        onImportText={(text) => void runImport(() => parseDeckText(text))}
        onImportUrl={(url) => void runImport(() => parseDeckUrl(url))}
        onImportFile={(file) => void runImport(() => parseDeckFile(file))}
        onClear={() => applyDeck([])}
      />
      <OptimizerControls
        settings={settings}
        busy={optimizing}
        canOptimize={request !== null && !importing}
        stale={stale}
        storeHint={storeHint}
        blockedReason={blockedReason}
        onChange={(next) => {
          setStoreHint(null);
          setWorkspaceSettings(next);
        }}
        onToggleStore={toggleStore}
        onOptimize={() => void optimize()}
      />
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2">
        <TriageGrid
          items={items}
          excludeBasics={settings.exclude_basic_lands}
          onToggleLock={(index) => {
            mapWorkspaceItems((current) =>
              current.map((item, itemIndex) =>
                itemIndex === index && item.scryfall_id ? { ...item, printing_locked: !item.printing_locked } : item,
              ),
            );
          }}
        />
        <ResultsDashboard result={result} optimizing={optimizing} error={optimizeError} stale={stale} />
      </div>
    </div>
  );
}
