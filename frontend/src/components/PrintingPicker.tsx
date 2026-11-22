"use client";

import { Star, X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";

import { errorMessage, fetchPrintings } from "@/lib/api";
import { formatCents } from "@/lib/format";
import type { CardPrinting, ParsedDeckItem, PrintingsResponse, StoreId } from "@/types/deck";

interface PrintingPickerProps {
  item: ParsedDeckItem;
  enabledStores: StoreId[];
  onClose: () => void;
  onChoose: (item: ParsedDeckItem) => void;
}

interface PrintingsLoad {
  key: string;
  data: PrintingsResponse | null;
  error: string | null;
}

export function PrintingPicker({ item, enabledStores, onClose, onChoose }: PrintingPickerProps) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const requestKey = `${item.oracle_id ?? ""}|${enabledStores.join(",")}`;
  const [load, setLoad] = useState<PrintingsLoad>({ key: "", data: null, error: null });
  const data = load.key === requestKey ? load.data : null;
  const error = load.key === requestKey ? load.error : null;
  const loading = item.oracle_id !== null && load.key !== requestKey;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    closeRef.current?.focus();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const oracleId = item.oracle_id;
    if (!oracleId) return;
    let cancelled = false;
    fetchPrintings(oracleId, enabledStores)
      .then((response) => {
        if (!cancelled) setLoad({ key: requestKey, data: response, error: null });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setLoad({ key: requestKey, data: null, error: errorMessage(caught) });
      });
    return () => {
      cancelled = true;
    };
  }, [item.oracle_id, enabledStores, requestKey]);

  const printings = [...(data?.printings ?? [])].sort((left, right) => {
    if (left.scryfall_id === item.scryfall_id) return -1;
    if (right.scryfall_id === item.scryfall_id) return 1;
    return 0;
  });

  function choose(printing: CardPrinting, finish: "nonfoil" | "foil") {
    onChoose({
      ...item,
      name: printing.name,
      set_code: printing.set_code,
      collector_number: printing.collector_number,
      scryfall_id: printing.scryfall_id,
      oracle_id: printing.oracle_id,
      image_uri: printing.image_uri,
      finish,
      printing_locked: true,
      match_status: "matched",
      available_printings: data?.printings.length ?? item.available_printings,
    });
  }

  return (
    <div
      className="fixed inset-0 z-[70] overflow-y-auto bg-black/75 p-4 sm:p-8"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className="mx-auto w-full max-w-5xl rounded-2xl border border-line bg-surface p-4 shadow-2xl sm:p-5"
      >
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 id={titleId} className="text-lg font-semibold text-paper">
              {item.name}
            </h2>
            <p className="mt-1 text-sm text-muted">
              Deck list {deckPrinting(item)}. Choose a printing, then foil or nonfoil.
            </p>
          </div>
          <button
            ref={closeRef}
            type="button"
            aria-label="Close printings"
            onClick={onClose}
            className="inline-flex size-9 shrink-0 items-center justify-center rounded-lg border border-line text-paper"
          >
            <X aria-hidden="true" size={16} />
          </button>
        </div>

        {loading ? <p className="mt-6 text-sm text-muted">Loading printings…</p> : null}
        {error ? (
          <p role="alert" className="mt-6 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
            {error}
          </p>
        ) : null}
        {!loading && !error && printings.length === 0 ? (
          <p className="mt-6 text-sm text-muted">No printings are catalogued for this card.</p>
        ) : null}

        {printings.length > 0 ? (
          <div className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {printings.map((printing) => (
              <PrintingChoice
                key={printing.scryfall_id}
                printing={printing}
                deckFinish={item.finish}
                selectedId={item.scryfall_id}
                selectedFinish={item.finish}
                onChoose={(finish) => choose(printing, finish)}
              />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function PrintingChoice({
  printing,
  deckFinish,
  selectedId,
  selectedFinish,
  onChoose,
}: {
  printing: CardPrinting;
  deckFinish: ParsedDeckItem["finish"];
  selectedId: string | null;
  selectedFinish: ParsedDeckItem["finish"];
  onChoose: (finish: "nonfoil" | "foil") => void;
}) {
  const [foil, setFoil] = useState(deckFinish === "foil");
  const finish = foil ? "foil" : "nonfoil";
  const available = printing.finishes.length === 0 || printing.finishes.includes(finish);
  const selected = selectedId === printing.scryfall_id && selectedFinish === finish;
  const vendors = printing.prices.find((entry) => entry.finish === finish)?.vendors ?? [];
  const setLabel = `${printing.set_code.toUpperCase()} #${printing.collector_number}`;

  return (
    <article className={`relative overflow-hidden rounded-xl border-2 bg-raised ${selected ? "border-accent" : "border-line"}`}>
      <button
        type="button"
        aria-pressed={foil}
        aria-label={`Foil for ${setLabel}`}
        title="Foil"
        onClick={() => setFoil((current) => !current)}
        className={`absolute top-2 left-2 z-10 inline-flex size-8 items-center justify-center rounded-full border shadow-md ${
          foil ? "border-accent bg-accent text-accent-ink" : "border-line bg-background/90 text-paper"
        }`}
      >
        <Star aria-hidden="true" size={15} className={foil ? "fill-current" : ""} />
      </button>
      <button
        type="button"
        disabled={!available}
        aria-current={selected ? "true" : undefined}
        onClick={() => onChoose(finish)}
        className="block w-full text-left disabled:cursor-not-allowed disabled:opacity-100"
      >
        {printing.image_uri ? (
          // Scryfall hosts the printing image. The set and collector number are the label.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={printing.image_uri} alt="" className="aspect-[63/88] w-full bg-background object-cover" />
        ) : (
          <div className="flex aspect-[63/88] w-full items-center justify-center bg-background text-sm text-muted">
            No image
          </div>
        )}
        <div className="space-y-2 p-3">
          <p className="font-mono text-xs text-paper">{setLabel}</p>
          {vendors.length === 0 ? (
            <p className="text-xs text-muted">No enabled store has a listing.</p>
          ) : (
            <ul>
              {vendors.map((vendor) => (
                <li key={vendor.store_id} className="flex items-baseline justify-between gap-3 py-0.5 text-xs">
                  <span className="text-muted">{vendor.store_name}</span>
                  <span className="font-mono text-paper">
                    {vendor.median_cents === null ? "—" : formatCents(vendor.median_cents)}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <p className={`text-xs font-semibold ${available ? "text-accent" : "text-muted"}`}>
            {selected ? "Current printing" : available ? `Use this ${finish} printing` : `No ${finish} printing`}
          </p>
        </div>
      </button>
    </article>
  );
}

function deckPrinting(item: ParsedDeckItem): string {
  const set = item.set_code ? item.set_code.toUpperCase() : "Any set";
  const collector = item.collector_number ? ` #${item.collector_number}` : "";
  return `${set}${collector} · ${item.finish}`;
}
