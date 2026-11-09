# MUSE

Your music. Your world. A personal music archive built from your Spotify
history: an importer, a MusicBrainz enricher, a Postgres/Supabase database,
and a website (`web/`, see `web/README.md` to put it online).

```
supabase/migrations/   Database schema. Run on Supabase, in order.
  0001_core_schema.sql      tables, row level security, profile-on-signup
  0002_import_functions.sql process_import_batch(), rollback_import_batch()
  0003_analytics_views.sql  plays, monthly_artist_stats
  0004_musicbrainz_enrichment.sql  lookup queue, match decisions, review, merges
  0005_timeline_functions.sql      what the website's Timeline page reads
web/                   Next.js website (Timeline page, sign-in)
importer/muse_import/  Python importer (standard library only)
  parse.py                  reads the export, cleans and filters rows
  load.py                   sends rows to Postgres (psycopg or psql)
  musicbrainz.py            rate-limited, cached MusicBrainz client
  matching.py               decides which MusicBrainz entry something is
  enrich.py                 runs the lookups, resumably
  cli.py                    all commands
queries/insights.sql   Prototype queries for the first screens
scripts/generate_test_export.py   Fake Spotify export for testing
tests/                 40 tests (parser, matching, client, database)
dev/                   LOCAL TESTING ONLY: Supabase stand-in, DB reset
```

## How an import works

1. **Parse** (Python). Reads the ZIP, the extracted folder, or JSON files.
   Keeps music only: podcasts, audiobooks, local files and rows with no track
   are counted and skipped. Broken rows are rejected and reported. Only
   music fields are copied: **IP address, platform, country and user agent
   are never read into MUSE**. Duplicates across files are removed, and
   different spellings of the same artist are merged.
2. **Stage** (Postgres). Rows are bulk-copied into `import_staging` under a
   new `import_batches` row.
3. **Process** (Postgres, one transaction). `process_import_batch()` creates
   missing artists, albums and tracks, links featured artists, and inserts
   the plays. A play already stored (same track, same stop time) is skipped,
   so importing an overlapping or repeated export is safe.

### Decisions worth knowing

- **Every play is stored**, even a 3-second skip. Only the `plays` view
  applies the 30-second rule (`profiles.min_play_ms`), so the threshold can be
  changed later without re-importing.
- **Spotify's `ts` is when playback stopped.** `started_at` is estimated as
  `ts - ms_played`.
- **Tracks are identified by Spotify URI**, so a remix, remaster or sped-up
  version is a separate track (`version_type` records which). If Spotify
  renamed a track, the newest title wins.
- **Artists are matched by name for now**, through `artist_name_map`. Two
  different artists sharing a name will be merged until MusicBrainz
  enrichment splits them; the map exists so that's fixable.
- **Albums**: Spotify's export only names the track's main artist, so a
  various-artists compilation becomes one album per artist until enriched.
- **Rollback** removes the plays a batch added. If a later import overlapped
  it, re-import that later file afterwards.

## Set up on Supabase

1. Create a project at supabase.com.
2. In the **SQL Editor**, run the files in `supabase/migrations/` in
   order. Do not run anything from `dev/`.
3. In **Authentication > Users**, add yourself as a user. A profile is
   created for you automatically.
4. Get the database connection string from the **Connect** button on the
   project dashboard. The *session pooler* string works on most home networks.
   Treat it like a password: it bypasses row level security.
5. On your computer (Python 3.10+):

```bash
pip install "psycopg[binary]"
export MUSE_DATABASE_URL="postgresql://..."      # from step 4
cd importer

python -m muse_import check ~/Downloads/my_spotify_data.zip   # no database, just a report
python -m muse_import import ~/Downloads/my_spotify_data.zip --email you@example.com
python -m muse_import batches
python -m muse_import rollback <batch-id>                     # undo an import
```

To see insights, run `queries/insights.sql` in a SQL client with
`-v email=you@example.com` (it uses psql variables), or copy individual
queries into the Supabase SQL Editor with your user id pasted in.

## Enrich with MusicBrainz

After importing, fill in countries, genres, languages, release dates, ISRCs
and cover art links:

```bash
export MUSE_CONTACT="you@example.com"   # MusicBrainz requires contact details
python -m muse_import enrich --minutes 60      # stop any time; re-run to continue
python -m muse_import coverage                 # how much of your listening is covered
python -m muse_import review                   # check suggestions
python -m muse_import confirm 21 34            # ...accept some
python -m muse_import reject 40                # ...and reject others
python -m muse_import set-mbid artist "Name" https://musicbrainz.org/artist/<id>
```

**How long it takes.** MusicBrainz allows about one request per second.
Roughly: 3 requests per artist, 1 per album, 1 per track. A history with
2,000 artists, 4,000 albums and 12,000 tracks is around 6 hours in total.
Things are looked up most-listened first, so the first hour already covers
most of your listening. Use `--only artist` to do the artist pass first
(countries and genres), and `--min-plays 3` to skip the long tail. Every
response is cached in `~/.muse/musicbrainz_cache.sqlite`, so stopping and
re-running costs nothing.

**How matching works.**
- *Artists* are matched through their songs, not their name: MUSE searches
  for your two most-played songs by the artist and lets the results vote.
  That's how it tells two artists called "Kalis" apart. A name-only match
  is never applied automatically; it waits in `review`.
- *Albums* and *tracks* are only searched within an already-matched artist.
  Tracks must agree on title, length (within a few seconds) and version: a
  remix is never matched to the original, a remaster is (MusicBrainz treats
  them as the same recording), and a 7-minute live version loses to the
  studio one.
- Each decision is `auto` (applied), `suggested` (waits for you) or
  `unmatched`, and every one is stored in `metadata_matches` with the
  runner-up candidates.
- Two MUSE artists that turn out to be the same MusicBrainz artist (e.g.
  "Lil' Vesper" and "Lil Vesper") are **merged**: plays, albums, credits,
  ratings and collections all move over.
- Your decisions are **locked**: `confirm`, `reject` and `set-mbid` are never
  overwritten by later automatic runs.
- *Language* comes from the lyrics language MusicBrainz records on releases,
  applied to that album's tracks. *Genres* are MusicBrainz's community genre
  tags, weighted by how many people applied them.

**Known gaps.**
- Coverage depends on MusicBrainz. Big artists are well covered; smaller
  French rap, Spanish urban and North African artists may be missing, or
  have no genres or languages. Expect some `set-mbid` for favourites.
- Names in another script (e.g. Arabic) only match if MusicBrainz has that
  spelling as a name or alias; otherwise use `set-mbid`.
- Artists who only ever appear as a *featured* artist aren't looked up yet.
- Cover links point at the Cover Art Archive, which has no image for some
  releases; the app should fall back to a placeholder.
- This was tested against a simulated MusicBrainz (`tests/fake_musicbrainz.py`)
  built to mimic its JSON, not the live service. The first real run is the
  real test: start with `--limit 20` and look at the results.

## Try it now with fake data

```bash
python scripts/generate_test_export.py --out test_data
cd importer && python -m muse_import check ../test_data/my_spotify_data.zip
```

The generator invents 25 artists across four eras (US hip-hop 2023, French
rap 2024, Spanish and North African 2025, a mix in 2026) and about 23,000
rows, including podcasts, skips, duplicates across files, remixes, a renamed
track, offline and incognito plays, non-Latin names and broken rows. It also
writes `expected.json`, the counts a correct import must produce.

## Run the tests

```bash
python -m unittest discover -s tests -v
```

The parser, matching and client tests need nothing. The database tests run when
`MUSE_TEST_DATABASE_URL` points at a **local** Postgres built with
`dev/reset_local_db.sh` (never Supabase: they create and delete users).
The SQL for local testing loads `dev/supabase_stub.sql` first, which fakes
the parts of Supabase the migrations rely on.

## Not built yet

- Journal (Phase 2 in the blueprint).
- Website pages beyond Timeline: Overview, Collection, Identity (Soundprint).
