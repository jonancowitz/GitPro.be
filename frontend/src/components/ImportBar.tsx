"use client";

import { Upload } from "lucide-react";
import { useState } from "react";

import { FieldLabel, panelClass, primaryButtonClass, secondaryButtonClass } from "@/components/ui";
import { containsMoxfieldUrl, isLoneMoxfieldUrl, isMoxfieldUrl, MOXFIELD_HINT } from "@/lib/moxfield";

interface ImportBarProps {
  busy: boolean;
  error: string | null;
  hasDeck: boolean;
  onImportText: (text: string) => void;
  onImportUrl: (url: string) => void;
  onImportFile: (file: File) => void;
  onClear: () => void;
}

export function ImportBar({ busy, error, hasDeck, onImportText, onImportUrl, onImportFile, onClear }: ImportBarProps) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [dragging, setDragging] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const showMoxfieldHint = containsMoxfieldUrl(text) || isMoxfieldUrl(url);
  const shownError = localError ?? error;

  function importText() {
    if (isLoneMoxfieldUrl(text)) {
      setLocalError(null);
      return;
    }
    if (!text.trim()) {
      setLocalError("Paste a decklist, drop a CSV, or fetch an Archidekt URL.");
      return;
    }
    setLocalError(null);
    onImportText(text);
  }

  function importUrl() {
    const trimmed = url.trim();
    if (isMoxfieldUrl(trimmed)) {
      setLocalError(null);
      return;
    }
    if (!trimmed) {
      setLocalError("Paste an Archidekt deck URL.");
      return;
    }
    setLocalError(null);
    onImportUrl(trimmed);
  }

  function importFile(file: File | undefined) {
    if (!file) return;
    const name = file.name.toLowerCase();
    const allowed = name.endsWith(".csv") || name.endsWith(".txt") || file.type === "text/csv" || file.type === "text/plain";
    if (!allowed) {
      setLocalError("Drop a .csv or .txt deck file.");
      return;
    }
    setLocalError(null);
    onImportFile(file);
  }

  return (
    <section aria-label="Import deck" className={`${panelClass} min-w-0`}>
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-paper">Import</h2>
        {hasDeck ? (
          <button
            type="button"
            className="text-xs text-muted underline-offset-2 hover:text-paper hover:underline"
            onClick={() => {
              setText("");
              setUrl("");
              setLocalError(null);
              onClear();
            }}
          >
            Clear deck
          </button>
        ) : null}
      </div>
      <div
        className={`mt-3 rounded-xl border border-dashed px-3 py-3 transition ${dragging ? "border-accent bg-accent/5" : "border-line bg-background/40"}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
          setDragging(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          const file = event.dataTransfer.files[0];
          if (file) {
            importFile(file);
            return;
          }
          const dropped = event.dataTransfer.getData("text/plain");
          if (dropped) setText(dropped);
        }}
      >
        <FieldLabel htmlFor="deck-text">Paste decklist</FieldLabel>
        <textarea
          id="deck-text"
          value={text}
          rows={7}
          spellCheck={false}
          placeholder={"1 Sol Ring (LTC) 284 *F*\n1 Command Tower\n4 Lightning Bolt"}
          className="mt-1 w-full resize-y rounded-lg border border-line bg-background px-3 py-2 font-mono text-sm text-paper outline-none placeholder:text-muted/70"
          onChange={(event) => {
            setText(event.target.value);
            setLocalError(null);
          }}
        />
        <p className="mt-2 text-xs text-muted">Drop a ManaBox .csv or a .txt list on this panel.</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button type="button" className={primaryButtonClass} disabled={busy} onClick={importText}>
            Import list
          </button>
          <label className={`${secondaryButtonClass} cursor-pointer`}>
            <Upload aria-hidden="true" size={16} />
            Upload CSV
            <input
              type="file"
              accept=".csv,.txt,text/csv,text/plain"
              className="sr-only"
              onChange={(event) => {
                importFile(event.target.files?.[0]);
                event.target.value = "";
              }}
            />
          </label>
        </div>
      </div>

      <form
        className="mt-4"
        onSubmit={(event) => {
          event.preventDefault();
          importUrl();
        }}
      >
        <FieldLabel htmlFor="archidekt-url">Archidekt URL</FieldLabel>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            id="archidekt-url"
            type="text"
            inputMode="url"
            value={url}
            placeholder="https://archidekt.com/decks/12345"
            className="h-10 min-w-0 flex-1 rounded-lg border border-line bg-background px-3 text-sm text-paper outline-none placeholder:text-muted/70"
            onChange={(event) => {
              setUrl(event.target.value);
              setLocalError(null);
            }}
          />
          <button type="submit" className={secondaryButtonClass} disabled={busy}>
            Fetch Archidekt
          </button>
        </div>
      </form>

      {showMoxfieldHint ? (
        <p role="status" className="mt-3 text-sm text-warn">
          {MOXFIELD_HINT}
        </p>
      ) : null}
      {shownError ? (
        <p role="alert" className="mt-3 text-sm text-danger">
          {shownError}
        </p>
      ) : null}
    </section>
  );
}
