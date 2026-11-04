# MUSE: data foundation

The database and importer for MUSE. This is everything the app needs before
any screens exist: your Spotify history goes in clean, and the first insight
queries come back out of it.

```
supabase/migrations/   Database schema. Run on Supabase, in order.
  0001_core_schema.sql      tables, row level security, profile-on-signup
  0002_import_functions.sql process_import_batch(), rollback_import_batch()
  0003_analytics_views.sql  plays, monthly_artist_stats
importer/muse_import/  Python importer (standard library only)
  parse.py                  reads the export, cleans and filters rows
  load.py                   sends rows to Postgres (psycopg or psql)
  cli.py                    check / import / batches / rollback
queries/insights.sql   Prototype queries for the first screens
scripts/generate_test_export.py   Fake Spotify export for testing
tests/                 13 tests (parser + database)
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
2. In the **SQL Editor**, run the three files in `supabase/migrations/` in
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

The 11 parser tests need nothing. The 2 database tests run when
`MUSE_TEST_DATABASE_URL` points at a **local** Postgres built with
`dev/reset_local_db.sh` (never Supabase: they create and delete users).
The SQL for local testing loads `dev/supabase_stub.sql` first, which fakes
the parts of Supabase the migrations rely on.

## Not built yet

- Metadata enrichment (MusicBrainz: countries, genres, release dates, ISRCs;
  Cover Art Archive: artwork). The `metadata_matches` table is ready for it.
- Genres and languages: tables and columns exist, nothing fills them yet.
- Journal (Phase 2 in the blueprint).
- The Next.js app.
