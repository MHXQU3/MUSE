// Turning "view + key" (from the URL) into a concrete stretch of time.
// Pure functions on calendar dates; no timezone maths needed because the
// database already works in the user's local time.
//
//   view   key example    covers
//   all    -              the whole history
//   year   2024           1 Jan 2024 to 31 Dec 2024
//   month  2024-10        October 2024
//   week   2024-10-07     the week starting Monday 7 October 2024
//   day    2024-10-08     one day

export type View = "all" | "year" | "month" | "week" | "day";
export const VIEWS: View[] = ["day", "week", "month", "year", "all"];
export const VIEW_LABELS: Record<View, string> = {
  day: "Day", week: "Week", month: "Month", year: "Year", all: "All time",
};

export interface Period {
  view: View;
  key: string;
  start: string;   // inclusive, YYYY-MM-DD
  end: string;     // exclusive
  label: string;
  bucket: "hour" | "day" | "month";
  prev: string | null;  // key of the previous period, if it has any history
  next: string | null;
}

export interface Bounds {
  first: string; // YYYY-MM-DD of the first play
  last: string;  // YYYY-MM-DD of the last play
}

const DAY = 86_400_000;

export function toDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, (m || 1) - 1, d || 1));
}

export function iso(d: Date): string {
  return d.toISOString().slice(0, 10);
}

export function addDays(isoDate: string, n: number): string {
  return iso(new Date(toDate(isoDate).getTime() + n * DAY));
}

export function addMonths(isoDate: string, n: number): string {
  const d = toDate(isoDate);
  return iso(new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + n, 1)));
}

export function mondayOf(isoDate: string): string {
  const d = toDate(isoDate);
  return addDays(isoDate, -((d.getUTCDay() + 6) % 7));
}

const fmt = (opts: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...opts });
const fmtMonthYear = fmt({ month: "long", year: "numeric" });
const fmtDayLong = fmt({ weekday: "long", day: "numeric", month: "long", year: "numeric" });
const fmtDayShort = fmt({ day: "numeric", month: "long", year: "numeric" });
export const fmtMonth = fmt({ month: "long" });
export const fmtMonthShort = fmt({ month: "short" });
export const fmtWeekday = fmt({ weekday: "short" });

function isValidKey(view: View, key: string): boolean {
  switch (view) {
    case "all": return true;
    case "year": return /^\d{4}$/.test(key);
    case "month": return /^\d{4}-(0[1-9]|1[0-2])$/.test(key);
    default: return /^\d{4}-\d{2}-\d{2}$/.test(key) && !Number.isNaN(toDate(key).getTime())
      && iso(toDate(key)) === key;
  }
}

/** Start and end of the period a key names (end exclusive). */
function span(view: View, key: string, bounds: Bounds): [string, string] {
  switch (view) {
    case "all": return [bounds.first, addDays(bounds.last, 1)];
    case "year": return [`${key}-01-01`, `${Number(key) + 1}-01-01`];
    case "month": return [`${key}-01`, addMonths(`${key}-01`, 1)];
    case "week": { const s = mondayOf(key); return [s, addDays(s, 7)]; }
    case "day": return [key, addDays(key, 1)];
  }
}

/** The key for `view` that contains the given date. */
export function keyContaining(view: View, isoDate: string): string {
  switch (view) {
    case "all": return "all";
    case "year": return isoDate.slice(0, 4);
    case "month": return isoDate.slice(0, 7);
    case "week": return mondayOf(isoDate);
    case "day": return isoDate;
  }
}

function shift(view: View, key: string, dir: 1 | -1): string {
  switch (view) {
    case "all": return key;
    case "year": return String(Number(key) + dir);
    case "month": return addMonths(`${key}-01`, dir).slice(0, 7);
    case "week": return addDays(mondayOf(key), 7 * dir);
    case "day": return addDays(key, dir);
  }
}

function label(view: View, key: string): string {
  switch (view) {
    case "all": return "All time";
    case "year": return key;
    case "month": return fmtMonthYear.format(toDate(`${key}-01`));
    case "week": return `Week of ${fmtDayShort.format(toDate(mondayOf(key)))}`;
    case "day": return fmtDayLong.format(toDate(key)).replace(",", "");
  }
}

/**
 * Resolve URL parameters into a period. Anything missing or malformed falls
 * back to the month of the most recent play.
 */
export function resolvePeriod(viewParam: string | undefined, keyParam: string | undefined,
                              bounds: Bounds): Period {
  let view = (VIEWS as string[]).includes(viewParam ?? "") ? (viewParam as View) : "month";
  let key = keyParam ?? "";
  if (view === "week" && isValidKey("week", key)) key = mondayOf(key);
  if (!isValidKey(view, key)) {
    view = viewParam && (VIEWS as string[]).includes(viewParam) ? view : "month";
    key = keyContaining(view, bounds.last);
  }
  const [start, end] = span(view, key, bounds);
  const prevKey = shift(view, key, -1);
  const nextKey = shift(view, key, 1);
  const hasHistory = (k: string) => {
    const [s, e] = span(view, k, bounds);
    return e > bounds.first && s <= bounds.last;
  };
  return {
    view, key, start, end,
    label: label(view, key),
    bucket: view === "day" ? "hour" : view === "year" || view === "all" ? "month" : "day",
    prev: view !== "all" && hasHistory(prevKey) ? prevKey : null,
    next: view !== "all" && hasHistory(nextKey) ? nextKey : null,
  };
}

/** Switching view keeps you around the same time: the new period contains
 *  the start of the current one. */
export function switchKey(from: Period, to: View, bounds: Bounds): string {
  if (to === "all") return "all";
  // Starting from "all time", go to the most recent period.
  const anchor = from.view === "all" ? bounds.last : from.start;
  return keyContaining(to, anchor);
}

export function labelFor(view: View, key: string): string {
  return label(view, key);
}
