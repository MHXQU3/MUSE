"""Tests for MusicBrainz enrichment. Standard library only.

Matching and client tests always run. The end-to-end test runs when
MUSE_TEST_DATABASE_URL points at a LOCAL test database (see test_importer.py).
Nothing here talks to the real MusicBrainz: tests/fake_musicbrainz.py stands in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "importer"), str(ROOT / "tests")]

from muse_import.enrich import run_enrichment  # noqa: E402
from muse_import.load import connect, run_import  # noqa: E402
from muse_import.matching import (  # noqa: E402
    base_album, base_title, fold, match_album, match_artist_by_name,
    match_artist_by_recordings, match_track, parse_mb_date, similarity,
)
from muse_import.musicbrainz import MusicBrainzClient, MusicBrainzError, lucene_phrase  # noqa: E402
from muse_import.parse import parse_export  # noqa: E402

REAL, DECOY = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"


def rec(rid, title, artist_id, artist_name, length=200_000, score=100, disambiguation=""):
    return {"id": rid, "title": title, "length": length, "score": score, "disambiguation": disambiguation,
            "artist-credit": [{"name": artist_name, "artist": {"id": artist_id, "name": artist_name}}]}


class TextTests(unittest.TestCase):
    def test_fold(self):
        self.assertEqual(fold("Sélène B"), "selene b")
        self.assertEqual(fold("JAY-Z"), "jay z")
        self.assertEqual(fold("Simon & Garfunkel"), "simon and garfunkel")
        self.assertEqual(similarity("Cœur de Pierre", "Coeur de Pierre"), 1.0)

    def test_base_titles(self):
        self.assertEqual(base_title("Marea (feat. X & Y) - Remastered 2024"), "Marea")
        self.assertEqual(base_title("Marea - Sped Up"), "Marea")
        self.assertEqual(base_title("Live Forever"), "Live Forever")  # the word, not a version
        self.assertEqual(base_album("Bitume (Deluxe Edition)"), "Bitume")
        self.assertEqual(base_album("Bitume Deluxe"), "Bitume")

    def test_dates(self):
        self.assertEqual(parse_mb_date("2019"), ("2019-01-01", "year"))
        self.assertEqual(parse_mb_date("2019-05"), ("2019-05-01", "month"))
        self.assertEqual(parse_mb_date("2019-05-17"), ("2019-05-17", "day"))
        self.assertEqual(parse_mb_date("bad"), (None, None))

    def test_lucene_phrase(self):
        self.assertEqual(lucene_phrase('Say "Hi"\\'), '"Say \\"Hi\\"\\\\"')


class MatchingTests(unittest.TestCase):
    artist = {"id": "muse-kalis", "name": "Kalis", "rejected": []}
    tracks = [{"id": "t1", "title": "Béton (feat. Nekfa)"}, {"id": "t2", "title": "Bitume"}]

    def test_songs_pick_the_right_namesake(self):
        searches = [
            (self.tracks[0], {"recordings": [rec("r1", "Béton", REAL, "Kalis"),
                                             rec("r9", "Beton Storm", DECOY, "Kalis", score=70)]}),
            (self.tracks[1], {"recordings": [rec("r2", "Bitume", REAL, "Kalis")]}),
        ]
        ev = match_artist_by_recordings(self.artist, searches)
        self.assertEqual(ev.decision.status, "auto")
        self.assertEqual(ev.decision.mbid, REAL)
        self.assertEqual(set(ev.recordings_by_track), {"t1", "t2"})

    def test_rejected_artist_is_never_chosen(self):
        searches = [(self.tracks[1], {"recordings": [rec("r2", "Bitume", REAL, "Kalis")]})]
        ev = match_artist_by_recordings({**self.artist, "rejected": [REAL]}, searches)
        self.assertIsNone(ev)

    def test_split_evidence_is_not_auto(self):
        searches = [(self.tracks[1], {"recordings": [rec("r2", "Bitume", REAL, "Kalis"),
                                                     rec("r3", "Bitume", DECOY, "Kalis")]})]
        ev = match_artist_by_recordings(self.artist, searches)
        self.assertNotEqual(ev.decision.status, "auto")

    def test_name_alone_is_only_a_suggestion(self):
        one = {"artists": [{"id": REAL, "name": "Kalis", "score": 100}]}
        self.assertEqual(match_artist_by_name(self.artist, one).status, "suggested")
        two = {"artists": [{"id": REAL, "name": "Kalis", "score": 100},
                           {"id": DECOY, "name": "Kalis", "score": 100}]}
        self.assertEqual(match_artist_by_name(self.artist, two).status, "unmatched")

    def test_track_prefers_right_version_and_length(self):
        track = {"id": "t", "title": "Bitume", "version_type": "original",
                 "est_duration_ms": 201_000, "artist_mbid": REAL, "rejected": []}
        studio = rec("studio", "Bitume", REAL, "Kalis", 200_000)
        live = rec("live", "Bitume", REAL, "Kalis", 420_000, disambiguation="live, 2025")
        d = match_track(track, [live, studio])
        self.assertEqual((d.status, d.mbid), ("auto", "studio"))
        self.assertEqual(match_track(track, [live]).status, "unmatched")

    def test_remix_is_not_matched_to_original(self):
        remix = {"id": "t", "title": "Bitume - Remix", "version_type": "remix",
                 "est_duration_ms": 200_000, "artist_mbid": REAL, "rejected": []}
        self.assertEqual(match_track(remix, [rec("o", "Bitume", REAL, "Kalis")]).status, "unmatched")

    def test_remaster_matches_original_recording(self):
        t = {"id": "t", "title": "Bitume - Remastered 2024", "version_type": "remaster",
             "est_duration_ms": 200_000, "artist_mbid": REAL, "rejected": []}
        self.assertEqual(match_track(t, [rec("o", "Bitume", REAL, "Kalis")]).status, "auto")

    def test_other_artists_recording_is_ignored(self):
        t = {"id": "t", "title": "Bitume", "version_type": "original",
             "est_duration_ms": 200_000, "artist_mbid": REAL, "rejected": []}
        self.assertEqual(match_track(t, [rec("x", "Bitume", DECOY, "Kalis")]).status, "unmatched")

    def test_album_fields(self):
        rg = {"id": "rg1", "primary-type": "Album", "secondary-types": []}
        credit = [{"name": "Kalis", "artist": {"id": REAL, "name": "Kalis"}}]
        releases = [
            {"id": "a", "title": "Bitume (Deluxe Edition)", "status": "Official", "date": "2024-02",
             "score": 100, "text-representation": {"language": "fra"}, "release-group": rg, "artist-credit": credit},
            {"id": "b", "title": "Bitume", "status": "Official", "date": "2023-11-03", "score": 100,
             "text-representation": {"language": None}, "release-group": rg, "artist-credit": credit},
        ]
        d = match_album({"id": "al", "title": "Bitume", "artist_mbid": REAL, "rejected": []}, releases)
        self.assertEqual((d.status, d.mbid), ("auto", "rg1"))
        self.assertEqual(d.fields["release_date"], "2023-11-03")   # earliest edition
        self.assertEqual(d.fields["language"], "fra")
        self.assertEqual(d.fields["album_type"], "album")
        self.assertIn("coverartarchive.org/release-group/rg1", d.fields["cover_url"])


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


class ClientTests(unittest.TestCase):
    def make(self, responses, cache=None):
        calls = []
        def transport(url, headers):
            calls.append((url, headers))
            return responses.pop(0)
        clk = FakeClock()
        mb = MusicBrainzClient("me@example.com", cache, transport=transport, clock=clk.clock, sleep=clk.sleep)
        return mb, calls, clk

    def test_contact_required(self):
        with self.assertRaises(ValueError):
            MusicBrainzClient("")

    def test_user_agent_and_encoding(self):
        mb, calls, _ = self.make([(200, {}, b'{"recordings": []}')])
        mb.search("recording", 'recording:"Slowed + Reverb"')
        url, headers = calls[0]
        self.assertEqual(headers["User-Agent"], "MUSE/0.2.0 ( me@example.com )")
        self.assertIn("%2B", url)  # a literal + in the query must not become a space
        self.assertIn("fmt=json", url)

    def test_one_request_per_second(self):
        mb, _, clk = self.make([(200, {}, b"{}")] * 3)
        for i in range(3):
            mb.search("artist", f"artist:x{i}")
        self.assertGreaterEqual(sum(clk.sleeps), 2 * 1.1 - 1e-9)

    def test_retries_on_503_and_honours_retry_after(self):
        mb, calls, clk = self.make([(503, {"Retry-After": "5"}, b""), (200, {}, b'{"artists": []}')])
        self.assertEqual(mb.search("artist", "artist:x"), {"artists": []})
        self.assertEqual(len(calls), 2)
        self.assertIn(5.0, clk.sleeps)

    def test_gives_up_eventually(self):
        mb, _, _ = self.make([(503, {}, b"")] * 10)
        with self.assertRaises(MusicBrainzError):
            mb.search("artist", "artist:x")

    def test_cache_and_404(self):
        cache = Path(tempfile.mkdtemp()) / "c.sqlite"
        mb, calls, _ = self.make([(200, {}, b'{"id": "1"}'), (404, {}, b"")], cache)
        self.assertEqual(mb.lookup("artist", "1"), {"id": "1"})
        self.assertEqual(mb.lookup("artist", "1"), {"id": "1"})
        self.assertIsNone(mb.lookup("artist", "2"))
        self.assertIsNone(mb.lookup("artist", "2"))
        self.assertEqual(len(calls), 2)
        self.assertEqual(mb.cache_hits, 2)
        # A fresh client reuses the cache file.
        mb2, calls2, _ = self.make([], cache)
        self.assertEqual(mb2.lookup("artist", "1"), {"id": "1"})
        self.assertEqual(calls2, [])


DB_URL = os.environ.get("MUSE_TEST_DATABASE_URL")


@unittest.skipUnless(DB_URL, "set MUSE_TEST_DATABASE_URL to a local test database")
class EndToEndTests(unittest.TestCase):
    """Import the test export, enrich it against the fake MusicBrainz, then
    review, confirm, override and re-run, checking the database each step."""

    @classmethod
    def setUpClass(cls):
        from fake_musicbrainz import NOUR_MBID, FakeMusicBrainz, mbid
        cls.NOUR_MBID, cls.mbid = NOUR_MBID, staticmethod(mbid)
        out = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_test_export.py"), "--out", str(out)],
                       check=True, capture_output=True)
        cls.parsed = parse_export([out / "my_spotify_data.zip"])
        cls.sql("truncate artists, albums, tracks, genres, metadata_matches, import_batches, artist_name_map cascade")
        cls.email = f"enrich-{uuid.uuid4().hex[:8]}@example.com"
        cls.sql(f"insert into auth.users (email) values ('{cls.email}')")
        cls.db = connect(DB_URL, os.environ.get("MUSE_TEST_BACKEND", "auto"))
        uid = cls.db.user_id_for_email(cls.email)
        run_import(cls.db, uid, cls.parsed)

        # One more play, under a second spelling of an existing artist.
        lv = next(e for e in cls.parsed.events if e.artist_name == "Lil Vesper" and e.reason_end == "trackdone")
        extra = Path(tempfile.mkdtemp()) / "Streaming_History_Audio_2026_9.json"
        extra.write_text(json.dumps([{
            "ts": "2026-10-20T10:00:00Z", "ms_played": lv.ms_played, "master_metadata_track_name": lv.track_name,
            "master_metadata_album_artist_name": "Lil' Vesper", "master_metadata_album_album_name": lv.album_name,
            "spotify_track_uri": "spotify:track:LILV2"}]))
        run_import(cls.db, uid, parse_export([extra]))
        cls.total_events = int(cls.sql("select count(*) from listening_events"))

        cls.fake = FakeMusicBrainz(cls.parsed.events)
        cls.mb = MusicBrainzClient("test@example.com", transport=cls.fake, min_interval=0, sleep=lambda s: None)
        cls.summary = run_enrichment(cls.db, cls.mb, log=lambda m: None)

    @classmethod
    def tearDownClass(cls):
        cls.sql(f"delete from auth.users where email = '{cls.email}'")

    @staticmethod
    def sql(q: str) -> str:
        return subprocess.run(["psql", DB_URL, "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", q],
                              check=True, capture_output=True, text=True).stdout.strip()

    def artist(self, name: str) -> dict:
        row = self.sql("select json_build_object('mbid', mbid, 'country', country, 'type', mb_type) "
                       "from artists where name = '" + name.replace("'", "''") + "'")
        return json.loads(row) if row else {}

    def test_01_artists(self):
        self.assertEqual(self.artist("Kalis")["mbid"], self.mbid("artist", "Kalis"))  # not the metal band
        self.assertEqual(self.artist("Kalis")["country"], "BE")
        self.assertIsNone(self.artist("Mira Solace")["mbid"])     # suggested, not applied
        self.assertIsNone(self.artist("نور الهدى")["mbid"])       # not findable
        self.assertEqual(self.artist("Lil' Vesper"), {})           # merged away...
        self.assertEqual(self.sql("select artist_id = (select id from artists where name = 'Lil Vesper') "
                                  "from artist_name_map where name_key = 'lil'' vesper'"), "t")
        self.assertEqual(self.summary.merged_artists, 1)
        self.assertEqual(int(self.sql("select count(*) from listening_events")), self.total_events)

    def test_02_genres_albums_languages(self):
        self.assertEqual(self.sql("select string_agg(g.name, ',' order by ag.weight desc) from artist_genres ag "
                                  "join genres g on g.id = ag.genre_id join artists a on a.id = ag.artist_id "
                                  "where a.name = 'Nekfa'"), "french hip hop,cloud rap")
        unmatched_albums = self.sql("select string_agg(distinct a.name, ',') from albums al "
                                    "join artists a on a.id = al.primary_artist_id where al.mbid is null")
        self.assertEqual(set(unmatched_albums.split(",")), {"Mira Solace", "نور الهدى"})
        self.assertEqual(self.sql("select string_agg(distinct t.language, ',' order by t.language) from tracks t "
                                  "join artists a on a.id = t.primary_artist_id where a.country in ('ES')"), "spa")
        self.assertEqual(self.sql("select count(*) from albums where mbid is not null and "
                                  "(release_date is null or cover_url is null)"), "0")

    def test_03_tracks(self):
        # No remix or sped-up track got matched to a plain studio recording.
        bad = self.sql("select count(*) from tracks t join metadata_matches mm on mm.entity_id = t.id "
                       "where t.version_type in ('remix', 'sped_up') and mm.status in ('auto', 'suggested') "
                       "and mm.payload->'fields'->>'label' not ilike '%sped up%'")
        self.assertEqual(bad, "0")
        self.assertEqual(self.sql("select count(*) from tracks where mbid is not null and isrc is null"), "0")

    def test_04_rerun_is_free(self):
        before = len(self.fake.requests)
        s = run_enrichment(self.db, self.mb, log=lambda m: None)
        self.assertEqual(sum(s.looked_up.values()), 0)
        self.assertEqual(len(self.fake.requests), before)

    def test_05_review_and_confirm(self):
        items = self.db.call("musicbrainz_review", {"kind": "artist"})
        mira = next(i for i in items if i["muse_name"] == "Mira Solace")
        self.db.call("resolve_musicbrainz_match", {"match_id": mira["match_id"], "decision": "confirm"})
        self.assertEqual(self.artist("Mira Solace")["country"], "SE")
        # Her albums are now queued and get matched on the next run.
        run_enrichment(self.db, self.mb, kinds=("album",), log=lambda m: None)

    def test_06_reject(self):
        items = self.db.call("musicbrainz_review", {"kind": "track"})
        self.assertTrue(items)
        self.db.call("resolve_musicbrainz_match", {"match_id": items[0]["match_id"], "decision": "reject"})
        self.assertEqual(self.sql(f"select status from metadata_matches where id = {items[0]['match_id']}"),
                         "rejected")

    def test_07_manual_override_and_lock(self):
        nour = self.sql("select id from artists where name = 'نور الهدى'")
        self.db.call("set_manual_musicbrainz_id", {"kind": "artist", "id": nour, "mbid": self.NOUR_MBID})
        run_enrichment(self.db, self.mb, kinds=("artist",), log=lambda m: None)
        self.assertEqual(self.artist("نور الهدى"), {"mbid": self.NOUR_MBID, "country": "MA", "type": "Person"})
        # Re-matching everything must not undo a manual choice.
        self.db.call("requeue_musicbrainz", {"kind": "artist", "only_unmatched": False})
        run_enrichment(self.db, self.mb, kinds=("artist",), log=lambda m: None)
        self.assertEqual(self.artist("نور الهدى")["mbid"], self.NOUR_MBID)

    def test_08_coverage_and_permissions(self):
        c = self.db.call("enrichment_coverage", {})
        self.assertGreater(c["listening_share"]["country"], 90)
        out = subprocess.run(["psql", DB_URL, "-X", "-A", "-t", "-q"],
                             input="begin; set local role authenticated;\n"
                                   "select public.apply_musicbrainz_results('{}'::jsonb);\nrollback;\n",
                             capture_output=True, text=True)
        self.assertIn("permission denied", out.stderr)


if __name__ == "__main__":
    unittest.main()
