-- LOCAL TESTING ONLY. Fills in artist countries for the invented test artists,
-- standing in for MusicBrainz enrichment, so geography queries have data.
-- Run from the project root:  psql -d muse_test -f dev/seed_test_countries.sql
\set countries `cat test_data/artist_countries.json`
update public.artists a
set country = c.value, metadata_source = 'test_seed', enriched_at = now()
from json_each_text(:'countries'::json) c
where a.name_key = public.name_key(c.key);
