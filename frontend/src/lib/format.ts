export function formatCents(cents: number): string {
  const sign = cents < 0 ? "-" : "";
  const absolute = Math.abs(Math.trunc(cents));
  const dollars = Math.floor(absolute / 100);
  const remainder = (absolute % 100).toString().padStart(2, "0");
  return `${sign}$${dollars}.${remainder}`;
}

export function safeHttpUrl(value: string): string | null {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" || url.protocol === "http:") {
      return url.toString();
    }
  } catch {
    return null;
  }
  return null;
}

export function packageLabel(count: number): string {
  return count === 1 ? "1 package" : `${count} packages`;
}
