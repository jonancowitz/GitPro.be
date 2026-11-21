"use client";

import { Copy, ExternalLink } from "lucide-react";
import { useState } from "react";

import { CardName } from "@/components/CardName";
import { primaryButtonClass, secondaryButtonClass } from "@/components/ui";
import { formatCents, packageLabel, safeHttpUrl } from "@/lib/format";
import type { StoreBucket } from "@/types/deck";

export function StoreBucketCard({ bucket }: { bucket: StoreBucket }) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<string | null>(null);
  const checkoutUrl = safeHttpUrl(bucket.checkout_url);
  const landed = bucket.subtotal_cents + bucket.shipping_cents;

  async function copyList() {
    try {
      await navigator.clipboard.writeText(bucket.mass_entry_text);
      setCopyError(null);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
      setCopyError("Couldn't copy the list.");
    }
  }

  return (
    <article className="min-w-0 rounded-2xl border border-line bg-raised p-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-paper">{bucket.store_name}</h3>
          <p className="mt-1 text-xs text-muted">
            {bucket.price_source === "live" ? "Live quote" : "Inventory sync"} · {packageLabel(bucket.estimated_packages)}
          </p>
        </div>
        <p className="font-mono text-lg text-paper">{formatCents(landed)}</p>
      </div>

      {bucket.notice ? <p className="mt-3 text-sm text-warn">{bucket.notice}</p> : null}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-[11px] uppercase tracking-[0.14em] text-muted">
            <tr>
              <th className="py-1 pr-2 font-medium">Qty</th>
              <th className="py-1 pr-2 font-medium">Card</th>
              <th className="py-1 pr-2 font-medium">Printing</th>
              <th className="py-1 pr-2 text-right font-medium">Each</th>
              <th className="py-1 text-right font-medium">Line</th>
            </tr>
          </thead>
          <tbody>
            {bucket.cards.map((card, index) => (
              <tr key={`${card.scryfall_id}-${card.finish}-${card.condition}-${index}`} className="border-t border-line">
                <td className="py-2 pr-2 font-mono tabular-nums">{card.quantity}</td>
                <td className="py-2 pr-2">
                  <CardName name={card.name} imageUri={card.image_uri} />
                </td>
                <td className="py-2 pr-2 font-mono text-xs text-muted">
                  {card.set_code.toUpperCase()} #{card.collector_number} · {card.finish} · {card.condition}
                </td>
                <td className="py-2 pr-2 text-right font-mono tabular-nums">{formatCents(card.unit_price_cents)}</td>
                <td className="py-2 text-right font-mono tabular-nums">{formatCents(card.line_total_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
        <div>
          <dt className="text-xs text-muted">Subtotal</dt>
          <dd className="font-mono text-paper">{formatCents(bucket.subtotal_cents)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted">Shipping</dt>
          <dd className="font-mono text-paper">{formatCents(bucket.shipping_cents)}</dd>
        </div>
      </dl>

      <FreeShipping bucket={bucket} />

      <div className="mt-4 flex flex-col gap-2 sm:flex-row">
        {checkoutUrl ? (
          <a href={checkoutUrl} target="_blank" rel="noopener noreferrer" className={`${primaryButtonClass} flex-1`}>
            <ExternalLink aria-hidden="true" size={16} />
            Open Cart on {bucket.store_name}
          </a>
        ) : (
          <button type="button" className={`${primaryButtonClass} flex-1`} disabled>
            Open Cart on {bucket.store_name}
          </button>
        )}
        <button type="button" className={secondaryButtonClass} onClick={() => void copyList()}>
          <Copy aria-hidden="true" size={16} />
          {copied ? "Copied" : "Copy List"}
        </button>
      </div>
      {copyError ? <p className="mt-2 text-sm text-danger">{copyError}</p> : null}
    </article>
  );
}

function FreeShipping({ bucket }: { bucket: StoreBucket }) {
  const delta = bucket.free_shipping_delta_cents;
  if (delta === null) {
    return <p className="mt-3 text-xs text-muted">No free-shipping threshold at {bucket.store_name}.</p>;
  }
  const qualified = delta <= 0;
  const goal = bucket.subtotal_cents + Math.max(delta, 0);
  const percent = qualified || goal <= 0 ? 100 : Math.min(100, Math.round((bucket.subtotal_cents / goal) * 100));
  const label = qualified
    ? `Free Shipping at ${bucket.store_name}`
    : `${formatCents(delta)} away from Free Shipping at ${bucket.store_name}`;
  return (
    <div className="mt-4">
      <p className="text-sm text-paper">{label}</p>
      <div
        className="mt-2 h-2 overflow-hidden rounded-full bg-background"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label={label}
      >
        <div className={`h-full ${qualified ? "bg-good" : "bg-accent"}`} style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}
