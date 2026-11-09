// Display helpers shared by every screen.

export function duration(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes} min`;
  const hours = ms / 3_600_000;
  return hours < 10 ? `${hours.toFixed(1)} h` : `${Math.round(hours).toLocaleString("en-GB")} h`;
}

export function hoursLong(ms: number): string {
  const minutes = Math.round(ms / 60_000);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  const hours = Math.round(ms / 3_600_000);
  return `${hours.toLocaleString("en-GB")} ${hours === 1 ? "hour" : "hours"}`;
}

export function count(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-GB")} ${n === 1 ? one : many}`;
}

export function percent(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((100 * part) / whole)}%` : "0%";
}

// ISO 639-3 codes MusicBrainz uses, in plain words. "und" = not known yet.
const LANGUAGE_NAMES: Record<string, string> = {
  eng: "English", fra: "French", spa: "Spanish", ara: "Arabic", por: "Portuguese",
  ita: "Italian", deu: "German", nld: "Dutch", jpn: "Japanese", kor: "Korean",
  zho: "Chinese", hin: "Hindi", tur: "Turkish", rus: "Russian", pol: "Polish",
  swe: "Swedish", yor: "Yoruba", wol: "Wolof", ber: "Berber", kab: "Kabyle",
  und: "Not yet known",
};

export function languageName(code: string | null | undefined): string {
  if (!code) return LANGUAGE_NAMES.und;
  return LANGUAGE_NAMES[code] ?? code.toUpperCase();
}

// Sleeve-ink colours. Fixed for the common languages so they stay the same
// everywhere; anything else gets one of the spares, chosen by its code.
const LANGUAGE_COLOURS: Record<string, string> = {
  fra: "#8FA9D9", // slate blue
  spa: "#E0A458", // saffron
  ara: "#6DBE9E", // jade
  eng: "#D98C9E", // faded rose
  por: "#C9B458", // mustard
  ita: "#B48AD9", // violet
  und: "#5E5265", // no colour yet
};
const SPARES = ["#9CC6D6", "#D9A38C", "#A7C47A", "#C79BC2", "#8FB3A8", "#D6C08C"];

export function languageColour(code: string | null | undefined): string {
  const c = code || "und";
  if (LANGUAGE_COLOURS[c]) return LANGUAGE_COLOURS[c];
  let h = 0;
  for (const ch of c) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return SPARES[h % SPARES.length];
}

/** Languages sorted by listening, known ones first, "und" last. */
export function sortedLanguages(langs: Record<string, number>): [string, number][] {
  return Object.entries(langs).sort((a, b) =>
    (a[0] === "und" ? 1 : 0) - (b[0] === "und" ? 1 : 0) || b[1] - a[1]);
}
