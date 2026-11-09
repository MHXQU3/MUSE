"use client";
// The record shelf: one spine per month of your history. A spine's height is
// how much you listened that month; its colour bands are the languages you
// listened in, largest at the bottom. Eras show up as the colours shift.

import Link from "next/link";
import { type CSSProperties, useEffect, useRef } from "react";
import { duration, languageColour, languageName, sortedLanguages } from "@/lib/format";
import { addMonths, fmtMonth, fmtMonthShort, toDate } from "@/lib/period";
import { timelineHref } from "@/lib/routes";
import type { OverviewMonth } from "@/lib/types";

interface Props {
  months: OverviewMonth[];
  selectedStart: string; // YYYY-MM-DD; the spines inside [start, end) are highlighted
  selectedEnd: string;
  currentMonth?: string;  // set when the page shows exactly one month
}

export function Shelf({ months, selectedStart, selectedEnd, currentMonth }: Props) {
  const scroller = useRef<HTMLDivElement>(null);

  // Fill in months with no listening so the shelf has no gaps.
  const byMonth = new Map(months.map((m) => [m.month, m]));
  const all: OverviewMonth[] = [];
  if (months.length) {
    for (let m = `${months[0].month}-01`; m <= `${months[months.length - 1].month}-01`; m = addMonths(m, 1)) {
      const key = m.slice(0, 7);
      all.push(byMonth.get(key) ?? { month: key, ms: 0, languages: {}, plays: 0, artists: 0, top_artist: "" });
    }
  }
  const max = Math.max(1, ...all.map((m) => m.ms));
  const startMonth = selectedStart.slice(0, 7);
  // A month is highlighted if the period covers it, or the period sits inside it.
  const isSelected = (month: string) => {
    const first = `${month}-01`;
    return (first >= selectedStart && first < selectedEnd) || month === startMonth;
  };

  // Bring the selected month into view on small screens.
  useEffect(() => {
    const el = scroller.current?.querySelector<HTMLElement>("[data-selected='true']");
    if (el && scroller.current) {
      const box = scroller.current;
      box.scrollLeft = el.offsetLeft - box.clientWidth / 2 + el.clientWidth / 2;
    }
  }, [selectedStart]);

  return (
    <div className="shelf" ref={scroller} data-focused={selectedEnd > selectedStart && !(selectedStart <= `${months[0]?.month}-01` && selectedEnd > `${months[months.length - 1]?.month}-01`) ? "true" : undefined}>
      <ol className="shelf-row" aria-label="Your listening by month">
        {all.map((m, index) => {
          const langs = sortedLanguages(m.languages);
          const top = langs.find(([code]) => code !== "und");
          const d = toDate(`${m.month}-01`);
          const isJan = d.getUTCMonth() === 0 || m === all[0];
          const selected = isSelected(m.month);
          const height = m.ms ? Math.max(4, Math.round((m.ms / max) * 100)) : 0;
          const name = `${fmtMonth.format(d)} ${d.getUTCFullYear()}`;
          const desc = m.ms
            ? `${name}: ${duration(m.ms)}${top ? `, mostly ${languageName(top[0])}` : ""}${m.top_artist ? `, most played ${m.top_artist}` : ""}`
            : `${name}: nothing played`;
          return (
            <li key={m.month} className="shelf-slot" data-selected={selected ? "true" : undefined}>
              <Link href={timelineHref("month", m.month)} className="spine-link" aria-label={desc}
                    aria-current={currentMonth === m.month ? "page" : undefined} title={desc}>
                <span className="spine" style={{ height: `${height}%`, "--i": index } as CSSProperties}>
                  {/* column-reverse: the first (biggest) language sits at the base. */}
                  {langs.map(([code, ms]) => (
                    <span key={code} className="band"
                          style={{ flexGrow: ms, background: languageColour(code) }} />
                  ))}
                </span>
              </Link>
              <span className="shelf-tick" aria-hidden="true">
                {isJan ? d.getUTCFullYear() : selected ? fmtMonthShort.format(d) : ""}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

export function LanguageKey({ languages }: { languages: Record<string, number> }) {
  const total = Object.values(languages).reduce((a, b) => a + b, 0);
  const langs = sortedLanguages(languages).filter(([, ms]) => ms / total >= 0.01);
  return (
    <ul className="language-key" aria-label="Colour key">
      {langs.map(([code]) => (
        <li key={code}>
          <span className="swatch" style={{ background: languageColour(code) }} aria-hidden="true" />
          {languageName(code)}
        </li>
      ))}
    </ul>
  );
}
