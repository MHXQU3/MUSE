"""Tests for the MUSE importer. Standard library only.

    python -m unittest discover -s tests -v

The database tests run only when MUSE_TEST_DATABASE_URL points at a LOCAL
test database built with dev/reset_local_db.sh. Never point it at Supabase:
the tests create and delete users.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "importer"))

from muse_import.load import connect, run_import, staging_csv  # noqa: E402
from muse_import.parse import (  # noqa: E402
    Event, classify, detect_version, featured_artists, parse_export, parse_ts,
)

MUSIC = {
    "ts": "2024-03-01T18:22:10Z", "ms_played": 212000, "platform": "ios",
    "conn_country": "GB", "ip_addr": "203.0.113.9",
    "master_metadata_track_name": "Bitume (feat. Kalis)",
    "master_metadata_album_artist_name": "Sélène B",
    "master_metadata_album_album_name": "Nuit Blanche",
    "spotify_track_uri": "spotify:track:abc", "reason_start": "clickrow",
    "reason_end": "trackdone", "shuffle": False, "skipped": False, "offline": False,
    "offline_timestamp": None, "incognito_mode": False,
}


def generate_export(seed: int = 7) -> tuple[Path, dict]:
    out = Path(tempfile.mkdtemp(prefix="muse_test_"))
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_test_export.py"),
                    "--out", str(out), "--seed", str(seed)], check=True, capture_output=True)
    return out / "my_spotify_data.zip", json.loads((out / "expected.json").read_text())


class ClassifyTests(unittest.TestCase):
    def test_music_row(self):
        kind, e = classify(MUSIC)
        self.assertEqual(kind, "music")
        self.assertEqual(e.artist_name, "Sélène B")
        self.assertEqual(e.featured_artists, ("Kalis",))
        self.assertEqual(e.ended_at, datetime(2024, 3, 1, 18, 22, 10, tzinfo=timezone.utc))

    def test_sensitive_fields_never_copied(self):
        _, e = classify(MUSIC)
        values = json.dumps({k: str(v) for k, v in e.__dict__.items()})
        for secret in ("203.0.113.9", "ios", "GB"):
            self.assertNotIn(secret, values)
        self.assertFalse({"ip_addr", "platform", "conn_country"} & set(Event.__dataclass_fields__))

    def test_other_kinds(self):
        cases = {
            "podcast": {**MUSIC, "spotify_episode_uri": "spotify:episode:x"},
            "audiobook": {**MUSIC, "audiobook_uri": "spotify:show:x"},
            "local_file": {**MUSIC, "spotify_track_uri": "spotify:local:a:b:c:1"},
            "no_metadata": {**MUSIC, "spotify_track_uri": None, "master_metadata_track_name": None},
            "invalid_timestamp": {**MUSIC, "ts": "yesterday"},
            "invalid_duration": {**MUSIC, "ms_played": -1},
        }
        for expected, row in cases.items():
            with self.subTest(expected):
                self.assertEqual(classify(row)[0], expected)
        self.assertEqual(classify({**MUSIC, "ms_played": True})[0], "invalid_duration")
        self.assertEqual(classify("junk")[0], "not_a_record")

    def test_timestamps(self):
        self.assertIsNotNone(parse_ts("2024-01-01T00:00:00Z"))
        self.assertIsNone(parse_ts("1999-01-01T00:00:00Z"))  # before Spotify existed
        self.assertIsNone(parse_ts(None))

    def test_versions(self):
        self.assertEqual(detect_version("Marea - Remix"), "remix")
        self.assertEqual(detect_version("Marea - Remastered 2024"), "remaster")
        self.assertEqual(detect_version("Marea (Sped Up)"), "sped_up")
        self.assertEqual(detect_version("Marea - Slowed + Reverb"), "slowed")
        self.assertEqual(detect_version("Marea - Live at Wembley"), "live")
        self.assertEqual(detect_version("Oliver Twist"), "original")  # not "live"
        self.assertEqual(detect_version("Credit"), "original")         # not "edit"

    def test_featured(self):
        self.assertEqual(featured_artists("X (feat. A & B, C)"), ("A", "B", "C"))
        self.assertEqual(featured_artists("X [with Florence and the Machine]"),
                         ("Florence and the Machine",))
        self.assertEqual(featured_artists("X"), ())


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zip_path, cls.expected = generate_export()
        cls.parsed = parse_export([cls.zip_path])

    def test_counts_match_generator(self):
        s = self.parsed.stats
        self.assertEqual(s["music_events"], self.expected["unique_music_events"])
        self.assertGreater(s["duplicates_in_export"], 0)
        self.assertEqual(s["skipped_invalid_timestamp"] + s["skipped_invalid_duration"],
                         self.expected["broken_rows"])
        self.assertEqual(len({e.spotify_track_uri for e in self.parsed.events}),
                         self.expected["tracks_played"])

    def test_artist_spellings_merged(self):
        names = {e.artist_name for e in self.parsed.events}
        self.assertIn("Nayla Rif", names)
        self.assertNotIn("nayla  rif", names)

    def test_folder_and_zip_agree(self):
        folder = Path(tempfile.mkdtemp())
        zipfile.ZipFile(self.zip_path).extractall(folder)
        self.assertEqual(parse_export([folder]).stats["music_events"],
                         self.parsed.stats["music_events"])

    def test_rejects_non_export(self):
        bad = Path(tempfile.mkdtemp()) / "notes.json"
        bad.write_text("{}")
        with self.assertRaises(ValueError):
            parse_export([bad.parent])

    def test_csv_escapes_arrays(self):
        e = classify({**MUSIC, "master_metadata_track_name": 'X (feat. Say "Hi", Back\\slash)'})[1]
        csv_text = staging_csv(str(uuid.uuid4()), [e]).decode()
        self.assertIn('{""Say \\""Hi\\"""",""Back\\\\slash""}', csv_text)


DB_URL = os.environ.get("MUSE_TEST_DATABASE_URL")


@unittest.skipUnless(DB_URL, "set MUSE_TEST_DATABASE_URL to a local test database")
class DatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.zip_path, cls.expected = generate_export()
        cls.parsed = parse_export([cls.zip_path])
        cls.db = connect(DB_URL, os.environ.get("MUSE_TEST_BACKEND", "auto"))
        cls.email = f"test-{uuid.uuid4().hex[:8]}@example.com"
        cls.sql(f"insert into auth.users (email) values ('{cls.email}')")
        cls.user_id = cls.db.user_id_for_email(cls.email)

    @classmethod
    def tearDownClass(cls):
        cls.sql(f"delete from auth.users where email = '{cls.email}'")

    @staticmethod
    def sql(query: str) -> str:
        out = subprocess.run(["psql", DB_URL, "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1", "-c", query],
                             check=True, capture_output=True, text=True)
        return out.stdout.strip()

    def count(self, where: str = "") -> int:
        return int(self.sql(f"select count(*) from listening_events where user_id = '{self.user_id}' {where}"))

    def test_import_is_complete_repeatable_and_reversible(self):
        batch, result = run_import(self.db, self.user_id, self.parsed)
        self.assertEqual(result["events_inserted"], self.expected["unique_music_events"])
        counted = self.sql(f"select count(*) from plays where user_id = '{self.user_id}'")
        self.assertEqual(int(counted), self.expected["counted_plays"])

        # Same export again: nothing new.
        batch2, again = run_import(self.db, self.user_id, self.parsed)
        self.assertEqual(again["events_inserted"], 0)
        self.assertEqual(self.count(), self.expected["unique_music_events"])

        # Renamed track keeps its most recent title.
        title = self.sql("select title from tracks where spotify_uri = "
                         f"'{self.expected['renamed_track_uri']}'")
        self.assertEqual(title, self.expected["renamed_track_current_title"])

        # Rolling back removes exactly this user's plays from that batch.
        self.assertEqual(self.db.rollback(batch), self.expected["unique_music_events"])
        self.assertEqual(self.count(), 0)
        self.assertEqual(self.db.rollback(batch2), 0)

    def test_other_users_cannot_see_plays(self):
        run_import(self.db, self.user_id, self.parsed)
        stranger = str(uuid.uuid4())
        self.assertGreater(self.count(), 0)  # the data is there for the owner
        out = subprocess.run(
            ["psql", DB_URL, "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1"],
            input=("begin;\nset local role authenticated;\n"
                   f"select set_config('request.jwt.claim.sub', '{stranger}', true) \\g /dev/null\n"
                   "select count(*) from listening_events;\nrollback;\n"),
            check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "0")


if __name__ == "__main__":
    unittest.main()
