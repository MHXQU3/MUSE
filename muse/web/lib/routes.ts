// Where timeline links point. Kept in one place so the standalone preview
// (demo/) can swap in its own version.
import type { View } from "./period";

export function timelineHref(view: View, key: string): string {
  return view === "all" ? "/timeline?view=all" : `/timeline?view=${view}&key=${key}`;
}
