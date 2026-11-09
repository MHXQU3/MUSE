#!/usr/bin/env python3
"""Export timeline data for the standalone preview.

Calls the same SQL functions the website calls (timeline_overview and
timeline_period), signed in as the given user, for every year, month, week
and day that has listening, and writes demo/data.json.

    python demo/export_data.py --db postgresql://... --email you@example.com
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import date, timedelta
from pathlib import Path


def run_as_user(db: str, uid: str, sql: str) -> str:
    script = ("begin;\nset local role authenticated;\n"
              f"select set_config('request.jwt.claim.sub', '{uid}', true) \\g /dev/null\n"
              f"{sql}\nrollback;\n")
    out = subprocess.run(["psql", db, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"],
                         input=script, capture_output=True, text=True, check=True)
    return out.stdout


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--email", required=True)
    ap.add_argument("--out", default=str(Path(__file__).with_name("data.json")))
    args = ap.parse_args()

    uid = subprocess.run(["psql", args.db, "-X", "-A", "-t", "-c",
                          f"select id from auth.users where email = '{args.email}'"],
                         capture_output=True, text=True, check=True).stdout.strip()
    overview = json.loads(run_as_user(args.db, uid, "select timeline_overview();"))
    first = date.fromisoformat(overview["first_play"][:10])
    last = date.fromisoformat(overview["last_play"][:10])

    periods: dict[str, tuple[str, str, str]] = {"all": (first.isoformat(), (last + timedelta(1)).isoformat(), "month")}
    for y in range(first.year, last.year + 1):
        periods[f"year:{y}"] = (f"{y}-01-01", f"{y + 1}-01-01", "month")
    for m in overview["months"]:
        y, mo = map(int, m["month"].split("-"))
        nxt = date(y + (mo == 12), mo % 12 + 1, 1)
        periods[f"month:{m['month']}"] = (f"{m['month']}-01", nxt.isoformat(), "day")
    d = first - timedelta(first.weekday())
    while d <= last:
        periods[f"week:{d.isoformat()}"] = (d.isoformat(), (d + timedelta(7)).isoformat(), "day")
        d += timedelta(7)

    # Days that have plays (one query), then each day's detail.
    days = run_as_user(args.db, uid,
                       "select distinct started_local::date from plays order by 1;").split()
    for day in days:
        dd = date.fromisoformat(day)
        periods[f"day:{day}"] = (day, (dd + timedelta(1)).isoformat(), "hour")

    # One psql session for everything: a row per period.
    keys = list(periods)
    values = ",\n".join(f"('{k}', '{s}', '{e}', '{b}')" for k, (s, e, b) in periods.items())
    rows = run_as_user(args.db, uid, f"""
        select k || chr(9) || timeline_period(jsonb_build_object('start', s, 'end', e, 'bucket', b))::text
        from (values {values}) v(k, s, e, b);""")
    data = {}
    short_ids: dict[str, int] = {}
    for line in rows.splitlines():
        if "\t" in line:
            k, payload = line.split("\t", 1)
            p = json.loads(payload)
            # The preview can't load remote images; its sleeves are typeset.
            for al in p["top_albums"]:
                al["cover_url"] = None
            # Keep the file small enough for a phone: short ids, shorter
            # lists for single days.
            for section in ("top_artists", "top_tracks", "top_albums", "discoveries"):
                for item in p[section]:
                    item["id"] = short_ids.setdefault(item["id"], len(short_ids))
            if k.startswith("day:"):
                p["top_artists"], p["top_tracks"], p["top_albums"] = (
                    p["top_artists"][:5], p["top_tracks"][:5], p["top_albums"][:3])
            data[k] = p
    assert len(data) == len(keys), (len(data), len(keys))

    Path(args.out).write_text(json.dumps({"overview": overview, "periods": data},
                                         ensure_ascii=False, separators=(",", ":")))
    print(f"{len(data):,} periods, {Path(args.out).stat().st_size / 1e6:.1f} MB -> {args.out}")


if __name__ == "__main__":
    main()
