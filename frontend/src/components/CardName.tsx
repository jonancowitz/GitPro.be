"use client";

import { useState } from "react";

interface PreviewPoint {
  left: number;
  top: number;
}

export function CardName({
  name,
  imageUri,
  className = "",
}: {
  name: string;
  imageUri: string | null;
  className?: string;
}) {
  const [preview, setPreview] = useState<PreviewPoint | null>(null);

  function show(clientX: number, clientY: number) {
    if (!imageUri) return;
    setPreview(placePreview(clientX, clientY));
  }

  return (
    <>
      <span
        className={`font-medium text-paper ${className}`}
        tabIndex={imageUri ? 0 : undefined}
        onMouseEnter={(event) => show(event.clientX, event.clientY)}
        onMouseMove={(event) => show(event.clientX, event.clientY)}
        onMouseLeave={() => setPreview(null)}
        onFocus={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          show(rect.left, rect.bottom);
        }}
        onBlur={() => setPreview(null)}
      >
        {name}
      </span>
      {preview && imageUri ? (
        <span className="pointer-events-none fixed z-50" style={{ left: preview.left, top: preview.top }}>
          {/* Scryfall hosts the catalog image. The card name is already in the row. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={imageUri}
            alt=""
            width={188}
            height={262}
            className="rounded-lg border border-line bg-raised shadow-2xl"
          />
        </span>
      ) : null}
    </>
  );
}

function placePreview(anchorX: number, anchorY: number): PreviewPoint {
  const width = 188;
  const height = 262;
  const margin = 12;
  const left = Math.min(anchorX + 16, window.innerWidth - width - margin);
  const top = Math.min(anchorY + 12, window.innerHeight - height - margin);
  return {
    left: Math.max(margin, left),
    top: Math.max(margin, top),
  };
}
