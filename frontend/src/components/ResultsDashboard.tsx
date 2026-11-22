import { StoreBucketCard } from "@/components/StoreBucketCard";
import { panelClass } from "@/components/ui";
import { formatCents } from "@/lib/format";
import type { OptimizeResponse, StoreBucket, UnmatchedCard } from "@/types/deck";

interface ResultsDashboardProps {
  result: OptimizeResponse | null;
  optimizing: boolean;
  error: string | null;
  stale: boolean;
}

interface SplitColumn {
  key: string;
  title: string;
  winner: boolean;
  totalCents: number;
  buckets: StoreBucket[];
}

export function ResultsDashboard({ result, optimizing, error, stale }: ResultsDashboardProps) {
  if (!result && !optimizing && !error) {
    return (
      <section aria-label="Optimization results" className={`${panelClass} min-w-0 border-dashed`}>
        <h2 className="text-sm font-semibold text-paper">Results</h2>
        <p className="mt-3 text-sm text-muted">
          Run the optimizer to compare the winning split with buying the deck at each store.
        </p>
      </section>
    );
  }

  return (
    <section aria-label="Optimization results" className="min-w-0 space-y-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-paper">Results</h2>
        {optimizing ? <p className="text-xs text-muted">Pricing the split…</p> : null}
      </div>
      {error ? (
        <p role="alert" className="rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-sm text-danger">
          {error}
        </p>
      ) : null}
      {stale && result ? (
        <p className="rounded-xl border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
          Deck or settings changed. Update the split to refresh these prices.
        </p>
      ) : null}
      {result ? (
        <>
          <SavingsBanner result={result} />
          {result.notices.length > 0 ? (
            <ul className="space-y-2">
              {result.notices.map((notice) => (
                <li key={notice} className="rounded-xl border border-warn/40 bg-warn/10 px-3 py-2 text-sm text-warn">
                  {notice}
                </li>
              ))}
            </ul>
          ) : null}
          {result.buckets.length === 0 ? (
            <p className={`${panelClass} text-sm text-muted`}>No store could fill this list with the current settings.</p>
          ) : (
            <SplitColumns result={result} />
          )}
          <UnmatchedTray cards={result.unmatched} />
        </>
      ) : null}
    </section>
  );
}

function SplitColumns({ result }: { result: OptimizeResponse }) {
  const columns = buildSplitColumns(result);
  const winnerTotal = columns.find((column) => column.winner)?.totalCents ?? result.savings.optimized_total_cents;

  return (
    <div className="min-w-0 overflow-x-auto pb-1" aria-label="Candidate store splits">
      <div className="flex w-max min-w-full items-start gap-4">
        {columns.map((column) => {
          const delta = column.totalCents - winnerTotal;
          return (
            <div
              key={column.key}
              className={`w-[min(24rem,85vw)] shrink-0 rounded-2xl border-2 p-3 ${
                column.winner ? "border-accent bg-accent/10" : "border-line"
              }`}
              aria-label={`${column.winner ? "Winning split, " : ""}${column.title}, ${formatCents(column.totalCents)}`}
            >
              <div className="mb-3 flex items-start justify-between gap-3 px-1">
                <div>
                  <p className={`text-[11px] font-semibold uppercase tracking-[0.14em] ${column.winner ? "text-accent" : "text-muted"}`}>
                    {column.winner ? "Best split" : "Buy at one store"}
                  </p>
                  <h3 className="mt-1 text-base font-semibold text-paper">{column.title}</h3>
                  {column.winner ? null : <p className="mt-1 text-xs text-muted">{deltaLabel(delta)}</p>}
                </div>
                <div className="text-right">
                  {column.winner ? (
                    <span className="rounded-full bg-accent px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-accent-ink">
                      Best
                    </span>
                  ) : null}
                  <p className="mt-1 font-mono text-2xl text-paper">{formatCents(column.totalCents)}</p>
                </div>
              </div>
              <div className="space-y-3">
                {column.buckets.map((bucket) => (
                  <StoreBucketCard key={bucket.store_id} bucket={bucket} />
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SavingsBanner({ result }: { result: OptimizeResponse }) {
  const { savings } = result;
  const headline = savings.message ?? "No single-store baseline for this split.";
  return (
    <div className="rounded-2xl border border-good/30 bg-good/10 px-5 py-4">
      <p className="text-xs font-medium uppercase tracking-[0.14em] text-good">Optimized split</p>
      <p className="mt-1 font-mono text-3xl text-paper">{formatCents(savings.optimized_total_cents)}</p>
      <p className="mt-1 text-sm text-paper">{headline}</p>
      {savings.baseline_total_cents !== null && savings.baseline_store_name ? (
        <p className="mt-1 text-xs text-muted">
          {savings.baseline_store_name} baseline {formatCents(savings.baseline_total_cents)}
        </p>
      ) : null}
    </div>
  );
}

function UnmatchedTray({ cards }: { cards: UnmatchedCard[] }) {
  return (
    <section aria-label="Unmatched cards" className={panelClass}>
      <h3 className="text-sm font-semibold text-paper">Unmatched / out of stock</h3>
      {cards.length === 0 ? (
        <p className="mt-2 text-sm text-muted">Every priced card found a store.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {cards.map((card, index) => (
            <li
              key={`${card.scryfall_id ?? card.oracle_id ?? card.name ?? "card"}-${index}`}
              className="flex items-center justify-between gap-3 py-2 text-sm"
            >
              <span className="text-paper">
                <span className="font-mono">{card.quantity}×</span> {card.name ?? "Unknown card"}
                <span className="ml-2 text-muted">{card.finish}</span>
              </span>
              <span className="shrink-0 text-[11px] uppercase tracking-wide text-warn">
                {card.reason === "out_of_stock" ? "Out of stock" : "Unmatched"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function buildSplitColumns(result: OptimizeResponse): SplitColumn[] {
  const alternatives = result.alternatives ?? [];
  const optimizedTotal = result.savings.optimized_total_cents;
  const winnerIds = result.buckets.map((bucket) => bucket.store_id);
  const singleWinner = winnerIds.length === 1 ? winnerIds[0] : null;

  if (singleWinner !== null) {
    const columns: SplitColumn[] = alternatives.map((bucket) => {
      const winner = bucket.store_id === singleWinner;
      return {
        key: bucket.store_id,
        title: bucket.store_name,
        winner,
        totalCents: winner ? optimizedTotal : bucket.subtotal_cents + bucket.shipping_cents,
        buckets: winner ? result.buckets : [bucket],
      };
    });
    if (!columns.some((column) => column.winner)) {
      columns.unshift({
        key: "optimized",
        title: result.buckets[0]?.store_name ?? "Optimized split",
        winner: true,
        totalCents: optimizedTotal,
        buckets: result.buckets,
      });
    }
    return [...columns.filter((column) => column.winner), ...columns.filter((column) => !column.winner)];
  }

  return [
    {
      key: "optimized",
      title: "Optimized split",
      winner: true,
      totalCents: optimizedTotal,
      buckets: result.buckets,
    },
    ...alternatives.map((bucket) => ({
      key: bucket.store_id,
      title: bucket.store_name,
      winner: false,
      totalCents: bucket.subtotal_cents + bucket.shipping_cents,
      buckets: [bucket],
    })),
  ];
}

function deltaLabel(delta: number): string {
  if (delta === 0) return "Same total";
  if (delta > 0) return `${formatCents(delta)} more`;
  return `${formatCents(Math.abs(delta))} less`;
}
