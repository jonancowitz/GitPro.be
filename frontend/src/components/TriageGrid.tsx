"use client";

import { Lock, LockOpen } from "lucide-react";
import { useState } from "react";

import { CardName } from "@/components/CardName";
import { PrintingPicker } from "@/components/PrintingPicker";
import { panelClass } from "@/components/ui";
import type { ParsedDeckItem, StoreId } from "@/types/deck";

interface TriageGridProps {
  items: ParsedDeckItem[];
  excludeBasics: boolean;
  enabledStores: StoreId[];
  onToggleLock: (index: number) => void;
  onReplaceItem: (index: number, item: ParsedDeckItem) => void;
}

export function TriageGrid({ items, excludeBasics, enabledStores, onToggleLock, onReplaceItem }: TriageGridProps) {
  const [openIndex, setOpenIndex] = useState<number | null>(null);
  const unmatched = items.filter((item) => item.match_status !== "matched").length;
  const copies = items.reduce((sum, item) => sum + item.quantity, 0);
  const openItem = openIndex === null ? null : (items[openIndex] ?? null);

  return (
    <section aria-label="Deck triage" className={`${panelClass} min-w-0`}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-paper">Deck</h2>
        <p className="text-xs text-muted">
          {items.length === 0
            ? "Nothing imported"
            : `${copies} ${copies === 1 ? "card" : "cards"}${unmatched > 0 ? ` · ${unmatched} unresolved` : ""}`}
        </p>
      </div>
      {items.length === 0 ? (
        <p className="mt-4 text-sm text-muted">Import a deck to set quantities, finishes, and printing locks.</p>
      ) : (
        <div className="mt-3 max-h-[36rem] overflow-auto">
          <table className="w-full border-collapse text-sm">
            <thead className="sticky top-0 bg-surface text-left text-[11px] uppercase tracking-[0.14em] text-muted">
              <tr>
                <th className="px-2 py-2 font-medium">Qty</th>
                <th className="px-2 py-2 font-medium">Name</th>
                <th className="px-2 py-2 font-medium">Set / finish</th>
                <th className="px-2 py-2 font-medium">Lock</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, index) => {
                const excluded = excludeBasics && item.is_basic_land;
                const canLock = item.scryfall_id !== null;
                const canChoose = item.oracle_id !== null;
                return (
                  <tr
                    key={`${item.raw_line}-${index}`}
                    className={`border-t border-line ${excluded ? "opacity-50" : ""} ${canChoose ? "cursor-pointer hover:bg-white/[0.03]" : ""}`}
                    onClick={() => {
                      if (canChoose) setOpenIndex(index);
                    }}
                  >
                    <td className="px-2 py-2 font-mono text-paper tabular-nums">{item.quantity}</td>
                    <td className="px-2 py-2">
                      {canChoose ? (
                        <button
                          type="button"
                          className="group text-left"
                          aria-haspopup="dialog"
                          title={`Choose a printing for ${item.name}`}
                          onClick={(event) => {
                            event.stopPropagation();
                            setOpenIndex(index);
                          }}
                        >
                          <CardName name={item.name} imageUri={item.image_uri} className="group-hover:text-accent" />
                        </button>
                      ) : (
                        <CardName name={item.name} imageUri={item.image_uri} />
                      )}
                      {item.match_status !== "matched" ? (
                        <span className="ml-2 rounded bg-warn/15 px-1.5 py-0.5 align-middle text-[10px] uppercase tracking-wide text-warn">
                          {item.match_status}
                        </span>
                      ) : null}
                    </td>
                    <td className="px-2 py-2">
                      <span className="inline-flex rounded-full border border-line px-2 py-0.5 font-mono text-xs text-paper">
                        {printingLabel(item)}
                      </span>
                      {excluded ? <span className="ml-2 text-xs text-muted">Excluded</span> : null}
                    </td>
                    <td className="px-2 py-2">
                      <button
                        type="button"
                        aria-pressed={item.printing_locked}
                        aria-label={
                          item.printing_locked ? `Unlock printing for ${item.name}` : `Lock printing for ${item.name}`
                        }
                        title={canLock ? "Lock this printing" : "This card has no exact printing to lock"}
                        disabled={!canLock}
                        onClick={(event) => {
                          event.stopPropagation();
                          onToggleLock(index);
                        }}
                        className="inline-flex size-8 items-center justify-center rounded-md border border-line text-paper disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {item.printing_locked ? <Lock aria-hidden="true" size={15} /> : <LockOpen aria-hidden="true" size={15} />}
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {openItem?.oracle_id ? (
        <PrintingPicker
          item={openItem}
          enabledStores={enabledStores}
          onClose={() => setOpenIndex(null)}
          onChoose={(next) => {
            if (openIndex === null) return;
            onReplaceItem(openIndex, next);
            setOpenIndex(null);
          }}
        />
      ) : null}
    </section>
  );
}

function printingLabel(item: ParsedDeckItem): string {
  const set = item.set_code ? item.set_code.toUpperCase() : "Any set";
  const collector = item.collector_number ? ` #${item.collector_number}` : "";
  return `${set}${collector} · ${item.finish}`;
}
