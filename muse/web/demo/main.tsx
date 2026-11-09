// Standalone preview: the real Timeline components, fed with data exported
// from the timeline SQL functions instead of a live Supabase connection.
import { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { Timeline } from "@/components/timeline/Timeline";
import { resolvePeriod } from "@/lib/period";
import type { Overview, PeriodData } from "@/lib/types";
import { parseHash } from "./shims/routes";

interface Bundle { overview: Overview; periods: Record<string, PeriodData> }

async function loadData(): Promise<Bundle> {
  const b64 = document.getElementById("muse-data")!.textContent!.trim();
  const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
  return JSON.parse(await new Response(stream).text());
}

function emptyPeriod(start: string, end: string, bucket: PeriodData["bucket"]): PeriodData {
  return { start, end, bucket, totals: { ms: 0, plays: 0, artists: 0, tracks: 0, new_artists: 0 },
           top_artists: [], top_tracks: [], top_albums: [], discoveries: [], buckets: [], languages: {}, log: null };
}

function App({ bundle }: { bundle: Bundle }) {
  const [hash, setHash] = useState(() => window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const ov = bundle.overview;
  const bounds = { first: ov.first_play!.slice(0, 10), last: ov.last_play!.slice(0, 10) };
  const { view, key } = parseHash(hash);
  const period = resolvePeriod(view, key, bounds);
  const id = period.view === "all" ? "all" : `${period.view}:${period.key}`;
  const data = bundle.periods[id] ?? emptyPeriod(period.start, period.end, period.bucket);

  // Moving to a new period scrolls to its heading, unless it's already in view.
  useEffect(() => {
    const el = document.querySelector(".period-header");
    if (el && hash && el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: "start" });
  }, [hash]);

  return (
    <>
      <header className="site-header">
        <a href="#" className="wordmark">MUSE</a>
        <nav aria-label="Main"><a href="#" aria-current="page">Timeline</a></nav>
      </header>
      <p className="preview-note">Preview with invented listening data. Your real history appears once the
        site is connected to your Supabase database.</p>
      <Timeline overview={ov} period={period} data={data} bounds={bounds} />
    </>
  );
}

const root = createRoot(document.getElementById("root")!);
loadData().then(
  (bundle) => root.render(<App bundle={bundle} />),
  (err) => root.render(<main className="timeline"><p className="empty">This preview needs a current
    browser to unpack its data ({String(err)}).</p></main>),
);
