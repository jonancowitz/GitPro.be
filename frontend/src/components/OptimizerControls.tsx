"use client";

import { FieldLabel, labelClass, panelClass, primaryButtonClass } from "@/components/ui";
import { formatCents } from "@/lib/format";
import {
  MIN_CONDITIONS,
  MIN_CONDITION_TITLES,
  STORE_IDS,
  STORE_LABELS,
  type FinishMode,
  type OptimizerSettings,
  type StoreId,
} from "@/types/deck";

interface OptimizerControlsProps {
  settings: OptimizerSettings;
  busy: boolean;
  canOptimize: boolean;
  stale: boolean;
  storeHint: string | null;
  blockedReason: string | null;
  onChange: (settings: OptimizerSettings) => void;
  onToggleStore: (storeId: StoreId) => void;
  onOptimize: () => void;
}

export function OptimizerControls({
  settings,
  busy,
  canOptimize,
  stale,
  storeHint,
  blockedReason,
  onChange,
  onToggleStore,
  onOptimize,
}: OptimizerControlsProps) {
  return (
    <section aria-label="Optimizer controls" className={`${panelClass} min-w-0`}>
      <h2 className="text-sm font-semibold text-paper">Optimizer</h2>
      <div className="mt-4 flex flex-wrap items-end gap-x-6 gap-y-4">
        <div>
          <p className={labelClass}>Min condition</p>
          <div className="inline-flex rounded-lg border border-line bg-background p-0.5" role="group" aria-label="Min condition">
            {MIN_CONDITIONS.map((condition) => (
              <Segment
                key={condition}
                pressed={settings.min_condition === condition}
                title={MIN_CONDITION_TITLES[condition]}
                onClick={() => onChange({ ...settings, min_condition: condition })}
              >
                {condition}
              </Segment>
            ))}
          </div>
        </div>

        <div>
          <p className={labelClass}>Finish</p>
          <div className="inline-flex rounded-lg border border-line bg-background p-0.5" role="group" aria-label="Finish">
            <Segment
              pressed={settings.finish_mode === "cheapest"}
              onClick={() => onChange({ ...settings, finish_mode: "cheapest" satisfies FinishMode })}
            >
              Cheapest Legal
            </Segment>
            <Segment
              pressed={settings.finish_mode === "match_deck"}
              onClick={() => onChange({ ...settings, finish_mode: "match_deck" satisfies FinishMode })}
            >
              Match Decklist
            </Segment>
          </div>
        </div>

        <label className="flex h-10 items-center gap-2 text-sm text-paper">
          <input
            type="checkbox"
            className="size-4 accent-accent"
            checked={settings.exclude_basic_lands}
            onChange={(event) => onChange({ ...settings, exclude_basic_lands: event.target.checked })}
          />
          Exclude Basic Lands
        </label>

        <fieldset>
          <legend className={labelClass}>Stores</legend>
          <div className="flex flex-wrap gap-2">
            {STORE_IDS.map((storeId) => {
              const enabled = settings.enabled_stores.includes(storeId);
              return (
                <button
                  key={storeId}
                  type="button"
                  aria-pressed={enabled}
                  onClick={() => onToggleStore(storeId)}
                  className={`h-9 rounded-lg border px-3 text-sm transition ${
                    enabled ? "border-accent bg-accent/15 text-paper" : "border-line bg-background text-muted hover:text-paper"
                  }`}
                >
                  {STORE_LABELS[storeId]}
                </button>
              );
            })}
          </div>
        </fieldset>
      </div>

      <div className="mt-5">
        <div className="flex items-baseline justify-between gap-3">
          <FieldLabel htmlFor="package-penalty">Package penalty</FieldLabel>
          <span className="font-mono text-sm text-paper">{formatCents(settings.package_penalty_cents)}</span>
        </div>
        <input
          id="package-penalty"
          type="range"
          min={0}
          max={1500}
          step={25}
          value={settings.package_penalty_cents}
          aria-valuemin={0}
          aria-valuemax={1500}
          aria-valuenow={settings.package_penalty_cents}
          aria-valuetext={formatCents(settings.package_penalty_cents)}
          className="mt-2 w-full accent-accent"
          onChange={(event) =>
            onChange({
              ...settings,
              package_penalty_cents: clampPenalty(Number(event.target.value)),
            })
          }
        />
        <div className="mt-1 flex justify-between gap-3 text-xs text-muted">
          <span>Lowest Price · $0</span>
          <span>Fewer Packages · $15</span>
        </div>
      </div>

      <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-muted" role="status">
          {storeHint ?? blockedReason ?? "Package penalty is added once for each store the split uses."}
        </p>
        <button type="button" className={primaryButtonClass} disabled={!canOptimize || busy} onClick={onOptimize}>
          {busy ? "Optimizing…" : stale ? "Update split" : "Optimize split"}
        </button>
      </div>
    </section>
  );
}

function Segment({
  pressed,
  title,
  onClick,
  children,
}: {
  pressed: boolean;
  title?: string;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-pressed={pressed}
      onClick={onClick}
      className={`h-9 rounded-md px-3 text-sm transition ${pressed ? "bg-raised text-paper" : "text-muted hover:text-paper"}`}
    >
      {children}
    </button>
  );
}

function clampPenalty(value: number): number {
  if (!Number.isFinite(value)) return 300;
  return Math.min(1500, Math.max(0, Math.round(value)));
}
