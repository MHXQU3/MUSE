// The Timeline page body. Pure: everything arrives as props, so the same
// component renders in the Next.js app and in the standalone preview.

import Link from "next/link";
import { count, duration, hoursLong, languageColour, languageName, percent, sortedLanguages } from "@/lib/format";
import {
  type Bounds, type Period, VIEW_LABELS, VIEWS, addDays, fmtMonthShort, fmtWeekday,
  labelFor, switchKey, toDate,
} from "@/lib/period";
import { timelineHref } from "@/lib/routes";
import type { Overview, PeriodData } from "@/lib/types";
import { LanguageKey, Shelf } from "./Shelf";
import { Sleeve } from "./Sleeve";

interface Props {
  overview: Overview;
  period: Period;
  data: PeriodData;
  bounds: Bounds;
}

export function Timeline({ overview, period, data, bounds }: Props) {
  const allLanguages: Record<string, number> = {};
  for (const m of overview.months) {
    for (const [code, ms] of Object.entries(m.languages)) allLanguages[code] = (allLanguages[code] ?? 0) + ms;
  }
  const empty = data.totals.plays === 0;

  return (
    <main className="timeline">
      <section className="shelf-section" aria-labelledby="shelf-heading">
        <div className="intro">
          <h1 id="shelf-heading">Timeline</h1>
          <p className="lede">Every month you&rsquo;ve listened, on one shelf. Taller spines are bigger
            months; the colours are the languages you were listening in. Pick a month to open it.</p>
        </div>
        <Shelf months={overview.months} selectedStart={period.start} selectedEnd={period.end}
               currentMonth={period.view === "month" ? period.key : undefined} />
        <LanguageKey languages={allLanguages} />
      </section>

      <PeriodHeader period={period} bounds={bounds} />

      {empty ? (
        <EmptyPeriod period={period} bounds={bounds} />
      ) : (
        <>
          <Summary data={data} period={period} />
          {period.view !== "all" && <Rhythm data={data} period={period} />}
          <div className="columns">
            <div className="column">
              <TopArtists data={data} />
              {data.discoveries.length > 0 && <Discoveries data={data} period={period} />}
            </div>
            <div className="column">
              <TopAlbums data={data} />
              <TopTracks data={data} />
            </div>
          </div>
          {data.log && <DayLog log={data.log} />}
        </>
      )}
    </main>
  );
}

// ---------------------------------------------------------------------------

function PeriodHeader({ period, bounds }: { period: Period; bounds: Bounds }) {
  return (
    <header className="period-header">
      <nav className="view-switch" aria-label="Zoom">
        {VIEWS.map((v) => (
          <Link key={v} href={timelineHref(v, switchKey(period, v, bounds))}
                aria-current={v === period.view ? "page" : undefined}>
            {VIEW_LABELS[v]}
          </Link>
        ))}
      </nav>
      <h2 className="period-title">{period.label}</h2>
      {period.view !== "all" && (
        <nav className="step" aria-label="Previous and next">
          {period.prev
            ? <Link href={timelineHref(period.view, period.prev)} rel="prev">
                <span aria-hidden="true">‹ </span>{shortLabel(period.view, period.prev)}</Link>
            : <span />}
          {period.next
            ? <Link href={timelineHref(period.view, period.next)} rel="next">
                {shortLabel(period.view, period.next)}<span aria-hidden="true"> ›</span></Link>
            : <span />}
        </nav>
      )}
    </header>
  );
}

function shortLabel(view: Period["view"], key: string): string {
  if (view === "month") return labelFor("month", key);
  if (view === "day") return new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "short" })
    .format(toDate(key));
  if (view === "week") return labelFor("week", key);
  return key;
}

function EmptyPeriod({ period, bounds }: { period: Period; bounds: Bounds }) {
  const from = labelFor("month", bounds.first.slice(0, 7));
  const to = labelFor("month", bounds.last.slice(0, 7));
  return (
    <section className="empty">
      <p>Nothing was played {period.view === "day" ? "on" : "in"} {period.label}.</p>
      <p>Your history runs from {from} to {to}.{" "}
        <Link href={timelineHref("month", bounds.last.slice(0, 7))}>Go to {to}</Link></p>
    </section>
  );
}

function Summary({ data, period }: { data: PeriodData; period: Period }) {
  const t = data.totals;
  const langs = sortedLanguages(data.languages).filter(([c]) => c !== "und");
  const known = langs.reduce((a, [, ms]) => a + ms, 0);
  const langSentence = langs.length === 0 ? null
    : langs.length === 1 ? `All of it in ${languageName(langs[0][0])}.`
    : `Mostly ${languageName(langs[0][0])} (${percent(langs[0][1], known)}), then ${languageName(langs[1][0])} (${percent(langs[1][1], known)}).`;
  const whose = period.view === "all" ? "for the first time" : `for the first time ${period.view === "day" ? "today" : `this ${period.view}`}`;
  return (
    <section className="summary" aria-label="Summary">
      <p className="summary-text">
        {hoursLong(t.ms)} across {count(t.plays, "play")}, {count(t.tracks, "song")} and {count(t.artists, "artist")}.{" "}
        {langSentence}{" "}
        {t.new_artists > 0 && period.view !== "all" && <>{count(t.new_artists, "artist")} heard {whose}.</>}
      </p>
    </section>
  );
}

function Rhythm({ data, period }: { data: PeriodData; period: Period }) {
  // Every slot in the period, including silent ones.
  const slots: { key: string; label: string; href?: string }[] = [];
  if (period.bucket === "hour") {
    for (let h = 0; h < 24; h++) slots.push({ key: String(h).padStart(2, "0"), label: h % 6 === 0 ? `${h}:00` : "" });
  } else if (period.bucket === "day") {
    for (let d = period.start; d < period.end; d = addDays(d, 1)) {
      const date = toDate(d);
      const label = period.view === "week" ? fmtWeekday.format(date)
        : date.getUTCDate() === 1 || date.getUTCDate() % 7 === 0 ? String(date.getUTCDate()) : "";
      slots.push({ key: d, label, href: timelineHref("day", d) });
    }
  } else {
    for (let m = period.start.slice(0, 7); `${m}-01` < period.end; m = addDays(`${m}-01`, 32).slice(0, 7)) {
      slots.push({ key: m, label: fmtMonthShort.format(toDate(`${m}-01`)).slice(0, 1),
                   href: timelineHref("month", m) });
    }
  }
  const ms = new Map(data.buckets.map((b) => [b.key, b.ms]));
  const max = Math.max(1, ...data.buckets.map((b) => b.ms));
  const title = period.bucket === "hour" ? "Through the day" : period.bucket === "day"
    ? (period.view === "week" ? "Day by day" : "Through the month") : "Month by month";

  return (
    <section className="rhythm" aria-labelledby="rhythm-heading">
      <h3 id="rhythm-heading">{title}</h3>
      <ol className={`rhythm-bars rhythm-${period.bucket}`}>
        {slots.map((s) => {
          const v = ms.get(s.key) ?? 0;
          const bar = <span className="rhythm-bar" style={{ height: `${v ? Math.max(3, (v / max) * 100) : 0}%` }} />;
          const name = period.bucket === "hour" ? `${s.key}:00` : period.bucket === "day"
            ? labelFor("day", s.key) : labelFor("month", s.key);
          const desc = `${name}: ${v ? duration(v) : "nothing played"}`;
          return (
            <li key={s.key} className="rhythm-slot">
              {s.href && v ? <Link href={s.href} aria-label={desc} title={desc}>{bar}</Link>
                : <span title={desc} aria-label={desc} role="img">{bar}</span>}
              <span className="rhythm-label" aria-hidden="true">{s.label}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function TopArtists({ data }: { data: PeriodData }) {
  const max = data.top_artists[0]?.ms ?? 1;
  return (
    <section aria-labelledby="artists-heading">
      <h3 id="artists-heading">Most played artists</h3>
      <ol className="ranked">
        {data.top_artists.map((a, i) => (
          <li key={a.id}>
            <span className="rank">{i + 1}</span>
            <div className="ranked-body">
              <p className="ranked-name">
                {a.name}
                {a.is_new && <span className="new-badge">first heard here</span>}
              </p>
              <p className="ranked-meta">{duration(a.ms)}, {count(a.plays, "play")}</p>
              <span className="share" aria-hidden="true">
                <span style={{ width: `${(a.ms / max) * 100}%`, background: languageColour(a.language) }} />
              </span>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

function TopAlbums({ data }: { data: PeriodData }) {
  if (!data.top_albums.length) return null;
  return (
    <section aria-labelledby="albums-heading">
      <h3 id="albums-heading">Albums in rotation</h3>
      <ul className="albums">
        {data.top_albums.map((al) => (
          <li key={al.id}>
            <Sleeve title={al.title} artist={al.artist} coverUrl={al.cover_url} language={al.language} />
            <p className="album-title">{al.title}</p>
            <p className="album-meta">{al.artist}, {duration(al.ms)}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function TopTracks({ data }: { data: PeriodData }) {
  return (
    <section aria-labelledby="tracks-heading">
      <h3 id="tracks-heading">On repeat</h3>
      <ol className="tracks">
        {data.top_tracks.map((t, i) => (
          <li key={t.id}>
            <span className="rank">{i + 1}</span>
            <span className="track-name">
              <span className="track-title">{t.title}</span>
              <span className="track-artist">{t.artist}</span>
            </span>
            <span className="track-plays">{count(t.plays, "play")}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Discoveries({ data, period }: { data: PeriodData; period: Period }) {
  const when = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", day: "numeric", month: "long",
    ...(period.view === "year" || period.view === "all" ? { year: "numeric" } : {}) });
  return (
    <section aria-labelledby="new-heading">
      <h3 id="new-heading">{period.view === "all" ? "First heard" : `First heard ${period.view === "day" ? "today" : `this ${period.view}`}`}</h3>
      <ul className="discoveries">
        {data.discoveries.map((d) => (
          <li key={d.id}>
            <p className="ranked-name">{d.name}</p>
            <p className="ranked-meta">
              <Link href={timelineHref("day", d.first_at.slice(0, 10))}>{when.format(toDate(d.first_at.slice(0, 10)))}</Link>
              , with &ldquo;{d.first_track}&rdquo;
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function DayLog({ log }: { log: NonNullable<PeriodData["log"]> }) {
  return (
    <section className="day-log" aria-labelledby="log-heading">
      <h3 id="log-heading">Everything you played</h3>
      <ol>
        {log.map((e, i) => (
          <li key={i} className={e.counted ? undefined : "log-short"}>
            <time>{e.at}</time>
            <span className="log-dot" style={{ background: languageColour(e.language) }} aria-hidden="true" />
            <span className="track-name">
              <span className="track-title">{e.title}</span>
              <span className="track-artist">{e.artist}</span>
            </span>
            <span className="track-plays">{e.counted ? duration(e.ms_played) : e.skipped ? "skipped" : "under 30 s"}</span>
          </li>
        ))}
      </ol>
    </section>
  );
}
