// Preview routing: #month-2024-10, #week-2024-10-07, #day-2024-10-08, #year-2024, #all
import type { View } from "@/lib/period";

export function timelineHref(view: View, key: string): string {
  return view === "all" ? "#all" : `#${view}-${key}`;
}

export function parseHash(hash: string): { view?: string; key?: string } {
  const h = hash.replace(/^#/, "");
  if (h === "all") return { view: "all" };
  const m = /^(year|month|week|day)-([\d-]+)$/.exec(h);
  return m ? { view: m[1], key: m[2] } : {};
}
