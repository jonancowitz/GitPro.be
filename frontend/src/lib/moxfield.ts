/** Matches the parser's unsupported Moxfield URL response. */
export const MOXFIELD_HINT =
  "Moxfield blocks direct server imports — click Export -> Copy for MTGO/Text on Moxfield and paste here.";

const MOXFIELD_URL = /^(?:https?:\/\/)?(?:www\.)?moxfield\.com(?:[/?#].*)?$/i;

export function isMoxfieldUrl(value: string): boolean {
  return MOXFIELD_URL.test(value.trim());
}

export function containsMoxfieldUrl(value: string): boolean {
  return /(?:https?:\/\/)?(?:www\.)?moxfield\.com\b/i.test(value);
}

export function isLoneMoxfieldUrl(value: string): boolean {
  const lines = value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.length === 1 && isMoxfieldUrl(lines[0] ?? "");
}
