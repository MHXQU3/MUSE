#!/usr/bin/env python3
"""Generate a fake Spotify Extended Streaming History export for testing MUSE.

Every artist, album and track is invented. The listening follows the "eras"
from the MUSE blueprint (US hip-hop in 2023, French rap in 2024, Spanish and
North African music in 2025, a mix in 2026) so the analytics have a story to
find.

The export deliberately includes the messy things a real one contains:
podcasts, audiobooks, rows with no track metadata, very short plays, the same
plays repeated across two files, remixes / features / remasters, a track that
Spotify renamed, inconsistent artist-name spacing, offline plays with
timestamps in seconds or milliseconds, incognito plays, non-Latin names, and a
few broken rows. Sensitive fields (IP address, platform, country) are filled
with fake values so the importer's stripping can be tested.

Alongside the ZIP it writes expected.json: the counts a correct importer must
arrive at.

Usage:
    python scripts/generate_test_export.py --out test_data --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIN_PLAY_MS = 30_000

# name, country, scene, main era years
ARTISTS = [
    ("Kade Monroe", "US", "us_hiphop"), ("Lil Vesper", "US", "us_hiphop"),
    ("Southside Halo", "US", "us_hiphop"), ("Jaylen Crest", "US", "us_hiphop"),
    ("Marlo Vance", "US", "us_hiphop"),
    ("Sélène B", "FR", "fr_rap"), ("Nekfa", "FR", "fr_rap"), ("Oumar Siel", "FR", "fr_rap"),
    ("La Brume", "FR", "fr_rap"), ("Tayeb 13", "FR", "fr_rap"), ("Kalis", "BE", "fr_rap"),
    ("Nayla Rif", "ES", "es_urban"), ("Dani Ocaso", "ES", "es_urban"),
    ("La Niña Trueno", "ES", "es_urban"), ("Bruno Peña", "ES", "es_urban"),
    ("Morena Sal", "ES", "es_urban"),
    ("Hamza El Nour", "EG", "north_africa"), ("Yasmine Adly", "EG", "north_africa"),
    ("Cheb Amine Wahran", "DZ", "north_africa"), ("Lounès K", "DZ", "north_africa"),
    ("Sanaa Atlas", "MA", "north_africa"), ("Rif Kingz", "MA", "north_africa"),
    ("نور الهدى", "MA", "north_africa"),
    ("Ollie Cane", "GB", "other"), ("Mira Solace", "SE", "other"),
]

# How much each scene is listened to in each year (relative weights).
ERA_WEIGHTS = {
    2023: {"us_hiphop": 70, "fr_rap": 12, "es_urban": 5, "north_africa": 3, "other": 10},
    2024: {"us_hiphop": 25, "fr_rap": 55, "es_urban": 8, "north_africa": 5, "other": 7},
    2025: {"us_hiphop": 10, "fr_rap": 25, "es_urban": 35, "north_africa": 25, "other": 5},
    2026: {"us_hiphop": 12, "fr_rap": 30, "es_urban": 25, "north_africa": 25, "other": 8},
}

WORDS = {
    "us_hiphop": ["Midnight", "Paper", "Chrome", "Westside", "Gravity", "Ghost", "Static",
                  "Velvet", "Concrete", "Neon", "Ritual", "Overtime", "Halo", "Trophies"],
    "fr_rap": ["Bitume", "Nuit Blanche", "Lumière", "Béton", "Mélancolie", "Ascenseur",
               "Brouillard", "Sablier", "Rêverie", "Cœur de Pierre", "Périphérique", "Écho"],
    "es_urban": ["Madrugada", "Fuego Lento", "Corazón", "Niña Mala", "Sin Frenos", "Luna Llena",
                 "Calle Ocho", "Señales", "Veneno", "Marea", "Peligro", "Azúcar"],
    "north_africa": ["Ya Lili", "Sahra", "Wahran", "Bladi", "Habibi Ya Nour", "Raï Forever",
                     "Casablanca Nights", "El Bahr", "Zina", "Ghorba", "Leila", "Atlas"],
    "other": ["Northern Lights", "Paper Boats", "Slow Motion", "Glasshouse", "Lowlands",
              "Satellite", "Wildfire", "Blue Hour"],
}


def build_catalogue(rng: random.Random) -> list[dict]:
    """Invent albums and tracks for every artist. Returns a flat list of tracks."""
    tracks: list[dict] = []
    uri_counter = 0

    def new_uri() -> str:
        nonlocal uri_counter
        uri_counter += 1
        return f"spotify:track:TEST{uri_counter:018d}"

    for artist, _country, scene in ARTISTS:
        words = WORDS[scene]
        for album_no in range(rng.randint(2, 3)):
            album = f"{rng.choice(words)} {['', 'Vol. 2', 'Deluxe', 'EP'][album_no % 4]}".strip()
            for _ in range(rng.randint(4, 9)):
                title = rng.choice(words)
                if rng.random() < 0.25:
                    feat = rng.choice([a for a, _, s in ARTISTS if s == scene and a != artist])
                    title = f"{title} (feat. {feat})"
                tracks.append({
                    "uri": new_uri(), "title": title, "artist": artist, "album": album,
                    "scene": scene, "duration_ms": rng.randint(120_000, 300_000),
                    "popularity": rng.random() ** 2,  # a few tracks dominate
                })
        # Variants of the artist's first track: these are different recordings.
        base = next(t for t in tracks if t["artist"] == artist)
        for suffix in [" - Remix", " - Remastered 2024", " - Sped Up"]:
            if rng.random() < 0.35:
                tracks.append({**base, "uri": new_uri(), "title": base["title"] + suffix,
                               "popularity": base["popularity"] * 0.3})
    return tracks


def pick_track(rng: random.Random, tracks: list[dict], year: int) -> dict:
    weights = ERA_WEIGHTS[min(max(year, 2023), 2026)]
    scene = rng.choices(list(weights), weights=list(weights.values()))[0]
    pool = [t for t in tracks if t["scene"] == scene]
    return rng.choices(pool, weights=[0.05 + t["popularity"] for t in pool])[0]


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def base_row(rng: random.Random) -> dict:
    # Fake sensitive fields. The importer must never store these.
    return {
        "platform": rng.choice(["ios", "android", "osx", "windows", "web_player"]),
        "conn_country": "GB",
        "ip_addr": f"203.0.113.{rng.randint(1, 254)}",  # documentation IP range
    }


def music_row(rng, track, ended: datetime, ms_played: int, *, skipped=False,
              offline=False, incognito=False) -> dict:
    row = base_row(rng)
    offline_ts = None
    if offline:
        secs = int(ended.timestamp()) - ms_played // 1000
        offline_ts = secs if rng.random() < 0.5 else secs * 1000  # real exports mix both
    row.update({
        "ts": iso(ended),
        "ms_played": ms_played,
        "master_metadata_track_name": track["title"],
        "master_metadata_album_artist_name": track["artist"],
        "master_metadata_album_album_name": track["album"],
        "spotify_track_uri": track["uri"],
        "episode_name": None, "episode_show_name": None, "spotify_episode_uri": None,
        "audiobook_title": None, "audiobook_uri": None,
        "audiobook_chapter_uri": None, "audiobook_chapter_title": None,
        "reason_start": rng.choice(["trackdone", "clickrow", "fwdbtn", "playbtn"]),
        "reason_end": "fwdbtn" if skipped else "trackdone",
        "shuffle": rng.random() < 0.4,
        "skipped": skipped,
        "offline": offline,
        "offline_timestamp": offline_ts,
        "incognito_mode": incognito,
    })
    return row


def episode_row(rng, ended: datetime) -> dict:
    row = music_row(rng, {"title": None, "artist": None, "album": None, "uri": None},
                    ended, rng.randint(300_000, 2_400_000))
    row.update({"episode_name": f"Episode {rng.randint(1, 300)}",
                "episode_show_name": "The Invented Podcast",
                "spotify_episode_uri": f"spotify:episode:TESTEP{rng.randint(1, 10**9):012d}"})
    return row


def audiobook_row(rng, ended: datetime) -> dict:
    row = music_row(rng, {"title": None, "artist": None, "album": None, "uri": None},
                    ended, rng.randint(600_000, 3_000_000))
    row.update({"audiobook_title": "A Made-Up Novel",
                "audiobook_uri": "spotify:show:TESTBOOK0001",
                "audiobook_chapter_title": f"Chapter {rng.randint(1, 40)}",
                "audiobook_chapter_uri": f"spotify:episode:TESTCH{rng.randint(1, 10**9):012d}"})
    return row


def generate(seed: int, start: datetime, end: datetime):
    rng = random.Random(seed)
    tracks = build_catalogue(rng)
    rows: list[dict] = []
    expected = {"podcast_rows": 0, "audiobook_rows": 0, "no_metadata_rows": 0, "broken_rows": 0,
                "music_events": 0, "short_plays": 0}

    # A track that Spotify renamed partway through: same URI, two titles.
    renamed = rng.choice(tracks)
    renamed_old_title = renamed["title"] + " (Original Mix)"

    day = start
    while day < end:
        # Heavier listening at weekends and in a couple of "obsession" months.
        sessions = rng.choices([0, 1, 2, 3], weights=[2, 5, 3, 1 + (day.weekday() >= 5) * 2])[0]
        if day.month in (7, 12):
            sessions += 1
        for _ in range(sessions):
            t = day.replace(hour=rng.choice([7, 8, 12, 17, 18, 19, 21, 22, 23]),
                            minute=rng.randint(0, 59), second=rng.randint(0, 59))
            incognito = rng.random() < 0.02
            offline = rng.random() < 0.05
            # Sometimes put one album on repeat, otherwise shuffle across the era.
            album_mode = rng.random() < 0.2
            seed_track = pick_track(rng, tracks, t.year)
            album_tracks = [x for x in tracks if x["album"] == seed_track["album"]
                            and x["artist"] == seed_track["artist"]]
            for i in range(rng.randint(4, 18)):
                if rng.random() < 0.03:
                    t += timedelta(minutes=rng.randint(20, 60))
                    rows.append(episode_row(rng, t)); expected["podcast_rows"] += 1
                    continue
                if rng.random() < 0.005:
                    t += timedelta(minutes=30)
                    rows.append(audiobook_row(rng, t)); expected["audiobook_rows"] += 1
                    continue
                track = album_tracks[i % len(album_tracks)] if album_mode else pick_track(rng, tracks, t.year)
                skipped = rng.random() < 0.18
                ms = rng.randint(0, 35_000) if skipped else track["duration_ms"]
                t += timedelta(milliseconds=ms + rng.randint(500, 4000))
                row = music_row(rng, track, t, ms, skipped=skipped, offline=offline,
                                incognito=incognito)
                if track is renamed and t.year < 2025:
                    row["master_metadata_track_name"] = renamed_old_title
                rows.append(row)
                expected["music_events"] += 1
                if ms < MIN_PLAY_MS:
                    expected["short_plays"] += 1
        day += timedelta(days=1)

    # Rows Spotify sometimes writes with no metadata at all.
    for _ in range(40):
        r = music_row(rng, {"title": None, "artist": None, "album": None, "uri": None},
                      start + timedelta(seconds=rng.randint(0, int((end - start).total_seconds()))),
                      rng.randint(0, 200_000))
        rows.append(r); expected["no_metadata_rows"] += 1

    # One artist written with inconsistent spacing / case on a few plays.
    # name_key() should fold these into the same artist.
    for r in rows:
        if r["master_metadata_album_artist_name"] == "Nayla Rif" and rng.random() < 0.05:
            r["master_metadata_album_artist_name"] = "nayla  rif"

    # Broken rows the importer must reject, not crash on.
    rows.append({**base_row(rng), "ms_played": 1000, "spotify_track_uri": tracks[0]["uri"]})  # no ts
    rows.append({**music_row(rng, tracks[1], start, 1000), "ts": "not-a-date"})
    rows.append({**music_row(rng, tracks[2], start + timedelta(days=3), 1000), "ms_played": -5})
    expected["broken_rows"] = 3

    rows.sort(key=lambda r: str(r.get("ts", "")))
    return rows, expected, tracks, renamed


def write_export(rows: list[dict], out_dir: Path) -> Path:
    """Split rows across files the way Spotify does, with an overlap between
    two of them so duplicate detection has something to catch."""
    out_dir.mkdir(parents=True, exist_ok=True)
    folder = "Spotify Extended Streaming History"
    zip_path = out_dir / "my_spotify_data.zip"
    chunk = 12_000
    files = []
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        if i > 0:
            part = rows[i - 250:i] + part  # 250 rows repeated from the previous file
        years = sorted({r["ts"][:4] for r in part if str(r.get("ts", ""))[:4].isdigit()})
        span = years[0] if len(years) == 1 else f"{years[0]}-{years[-1]}"
        files.append((f"{folder}/Streaming_History_Audio_{span}_{len(files)}.json", part))
    files.append((f"{folder}/Streaming_History_Video_2023-2026.json", []))

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, part in files:
            zf.writestr(name, json.dumps(part, ensure_ascii=False, indent=1))
    return zip_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="test_data")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--start", default="2023-01-01")
    ap.add_argument("--end", default="2026-11-01")
    args = ap.parse_args()

    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    rows, expected, tracks, renamed = generate(args.seed, start, end)
    out = Path(args.out)
    zip_path = write_export(rows, out)

    # A music event is identified by (stop time, track URI). Count unique ones.
    music = [r for r in rows if r.get("spotify_track_uri") and str(r.get("ts", "")).endswith("Z")
             and isinstance(r.get("ms_played"), int) and r["ms_played"] >= 0]
    expected["unique_music_events"] = len({(r["ts"], r["spotify_track_uri"]) for r in music})
    expected["counted_plays"] = len({(r["ts"], r["spotify_track_uri"]) for r in music
                                     if r["ms_played"] >= MIN_PLAY_MS})
    expected["tracks_played"] = len({r["spotify_track_uri"] for r in music})
    expected["artists"] = len({a for a, _, _ in ARTISTS})
    expected["renamed_track_uri"] = renamed["uri"]
    expected["renamed_track_current_title"] = renamed["title"]
    expected["total_rows_written"] = len(rows)
    (out / "expected.json").write_text(json.dumps(expected, indent=2, ensure_ascii=False))
    # Countries for the invented artists, standing in for MusicBrainz until enrichment exists.
    (out / "artist_countries.json").write_text(json.dumps({a: c for a, c, _ in ARTISTS},
                                                          indent=2, ensure_ascii=False))
    print(f"Wrote {zip_path} ({len(rows):,} rows)")
    print(json.dumps(expected, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
