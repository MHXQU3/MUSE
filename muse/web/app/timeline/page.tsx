import { redirect } from "next/navigation";
import { SiteHeader } from "@/components/SiteHeader";
import { Timeline } from "@/components/timeline/Timeline";
import { resolvePeriod } from "@/lib/period";
import { createClient } from "@/lib/supabase/server";
import type { Overview, PeriodData } from "@/lib/types";

export const metadata = { title: "Timeline | MUSE" };

type Search = Promise<{ view?: string; key?: string }>;

export default async function TimelinePage({ searchParams }: { searchParams: Search }) {
  const { view, key } = await searchParams;
  const supabase = await createClient();

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) redirect("/login");

  const { data: overview, error } = await supabase.rpc("timeline_overview");
  if (error) throw new Error(`Couldn't load your timeline: ${error.message}`);
  const ov = overview as Overview;

  if (!ov.months.length || !ov.first_play || !ov.last_play) {
    return (
      <>
        <SiteHeader />
        <main className="timeline">
          <section className="empty">
            <h1>Timeline</h1>
            <p>No listening history yet. Import your Spotify export with the MUSE importer
              (<code>python -m muse_import import ...</code>), then reload this page.</p>
          </section>
        </main>
      </>
    );
  }

  const bounds = { first: ov.first_play.slice(0, 10), last: ov.last_play.slice(0, 10) };
  const period = resolvePeriod(view, key, bounds);
  const { data, error: periodError } = await supabase.rpc("timeline_period", {
    p: { start: period.start, end: period.end, bucket: period.bucket },
  });
  if (periodError) throw new Error(`Couldn't load ${period.label}: ${periodError.message}`);

  return (
    <>
      <SiteHeader />
      <Timeline overview={ov} period={period} data={data as PeriodData} bounds={bounds} />
    </>
  );
}
