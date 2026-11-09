"""Send cleaned events to Postgres / Supabase.

Two interchangeable ways to talk to the database:
  * PsycopgDB - uses the psycopg Python driver (pip install "psycopg[binary]")
  * PsqlDB    - shells out to the psql command-line client
Both stream the exact same CSV into import_staging and then call the
process_import_batch() SQL function, so the database does the real work.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from typing import Protocol

from .parse import Event, ParseResult

STAGING_COLUMNS = [
    "batch_id", "row_num", "ended_at", "ms_played", "track_name", "artist_name", "album_name",
    "spotify_track_uri", "reason_start", "reason_end", "shuffle", "skipped", "offline",
    "incognito", "version_type", "featured_artists",
]


def _pg_bool(v: bool | None) -> str:
    return "" if v is None else ("t" if v else "f")


def _pg_text_array(items) -> str:
    escaped = ('"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"' for s in items)
    return "{" + ",".join(escaped) + "}"


def staging_csv(batch_id: str, events: list[Event]) -> bytes:
    """CSV for COPY ... (FORMAT csv, HEADER true). Empty unquoted = NULL."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(STAGING_COLUMNS)
    for i, e in enumerate(events):
        w.writerow([
            batch_id, i, e.ended_at.isoformat(), e.ms_played, e.track_name, e.artist_name,
            e.album_name or "", e.spotify_track_uri, e.reason_start or "", e.reason_end or "",
            _pg_bool(e.shuffle), _pg_bool(e.skipped), _pg_bool(e.offline), _pg_bool(e.incognito),
            e.version_type, _pg_text_array(e.featured_artists),
        ])
    return buf.getvalue().encode("utf-8")


# SQL functions the importer may call with a single jsonb argument.
CALLABLE_FUNCTIONS = {
    "musicbrainz_queue", "apply_musicbrainz_results", "musicbrainz_review",
    "resolve_musicbrainz_match", "set_manual_musicbrainz_id", "find_entity",
    "requeue_musicbrainz", "enrichment_coverage",
}


def _check_fn(fn: str) -> None:
    if fn not in CALLABLE_FUNCTIONS:
        raise ValueError(f"not an importer function: {fn}")


class Database(Protocol):
    def call(self, fn: str, arg: dict): ...
    def user_id_for_email(self, email: str) -> str | None: ...
    def import_batch(self, batch_id: str, user_id: str, files: list[str],
                     parse_stats: dict, csv_bytes: bytes) -> dict: ...
    def rollback(self, batch_id: str) -> int: ...
    def list_batches(self, user_id: str | None) -> list[dict]: ...


LIST_BATCHES_SQL = """
select coalesce(json_agg(b order by b.created_at desc), '[]')
from (select id, user_id, status, created_at, completed_at,
             stats->>'events_inserted' as events_inserted, file_names
      from public.import_batches
      where %s
      order by created_at desc limit 50) b
"""


# ---------------------------------------------------------------------------
# psycopg
# ---------------------------------------------------------------------------

class PsycopgDB:
    def __init__(self, dsn: str):
        import psycopg  # imported lazily so the psql path works without it
        self._psycopg = psycopg
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn)

    def call(self, fn: str, arg: dict):
        _check_fn(fn)
        with self._connect() as conn:
            row = conn.execute(f"select public.{fn}(%s::jsonb)::text", (json.dumps(arg),)).fetchone()
            return json.loads(row[0]) if row and row[0] is not None else None

    def user_id_for_email(self, email: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("select id::text from auth.users where lower(email) = lower(%s)",
                               (email,)).fetchone()
            return row[0] if row else None

    def import_batch(self, batch_id, user_id, files, parse_stats, csv_bytes) -> dict:
        with self._connect() as conn:
            with conn.transaction():
                conn.execute(
                    "insert into public.import_batches (id, user_id, file_names, stats) "
                    "values (%s, %s, %s, %s::jsonb)",
                    (batch_id, user_id, files, json.dumps(parse_stats)))
                with conn.cursor() as cur:
                    cols = ", ".join(STAGING_COLUMNS)
                    with cur.copy(f"copy public.import_staging ({cols}) "
                                  "from stdin with (format csv, header true)") as cp:
                        for start in range(0, len(csv_bytes), 1 << 20):
                            cp.write(csv_bytes[start:start + (1 << 20)])
                row = conn.execute("select public.process_import_batch(%s)::text",
                                   (batch_id,)).fetchone()
                return json.loads(row[0])

    def rollback(self, batch_id: str) -> int:
        with self._connect() as conn:
            return conn.execute("select public.rollback_import_batch(%s)", (batch_id,)).fetchone()[0]

    def list_batches(self, user_id: str | None) -> list[dict]:
        with self._connect() as conn:
            if user_id:
                row = conn.execute(LIST_BATCHES_SQL % "user_id = %s", (user_id,)).fetchone()
            else:
                row = conn.execute(LIST_BATCHES_SQL % "true").fetchone()
            return row[0]


# ---------------------------------------------------------------------------
# psql
# ---------------------------------------------------------------------------

class PsqlDB:
    """Runs SQL through psql. Values are passed as psql variables and quoted
    with :'name', so nothing user-supplied is pasted into SQL by hand."""

    def __init__(self, dsn: str, psql: str | None = None):
        self.dsn = dsn
        self.psql = psql or shutil.which("psql")
        if not self.psql:
            raise RuntimeError("psql not found. Install the Postgres client, or "
                               'pip install "psycopg[binary]" to use the Python driver.')

    def _run(self, script: str, variables: dict[str, str] | None = None) -> str:
        cmd = [self.psql, self.dsn, "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
               "--single-transaction"]
        for k, v in (variables or {}).items():
            cmd += ["-v", f"{k}={v}"]
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "psql failed")
        return proc.stdout.strip()

    def call(self, fn: str, arg: dict):
        _check_fn(fn)
        # The argument goes in a dollar-quoted literal with a random tag, so
        # its size isn't limited by the command line and it needs no escaping.
        payload = json.dumps(arg)
        tag = "muse_" + uuid.uuid4().hex
        while tag in payload:
            tag = "muse_" + uuid.uuid4().hex
        out = self._run(f"select public.{fn}(${tag}${payload}${tag}$::jsonb)::text;")
        return json.loads(out) if out else None

    def user_id_for_email(self, email: str) -> str | None:
        out = self._run("select id from auth.users where lower(email) = lower(:'email');",
                        {"email": email})
        return out or None

    def import_batch(self, batch_id, user_id, files, parse_stats, csv_bytes) -> dict:
        with tempfile.NamedTemporaryFile("wb", suffix=".csv", delete=False) as f:
            f.write(csv_bytes)
            csv_path = f.name
        try:
            cols = ", ".join(STAGING_COLUMNS)
            # psql doesn't substitute variables inside \copy, so the path (a
            # temp file we created ourselves) is quoted into the command.
            quoted_path = "'" + csv_path.replace("'", "''") + "'"
            script = (
                "insert into public.import_batches (id, user_id, file_names, stats) "
                "values (:'batch', :'user', string_to_array(:'files', E'\\n'), :'stats'::jsonb);\n"
                f"\\copy public.import_staging ({cols}) from {quoted_path} with (format csv, header true)\n"
                "select public.process_import_batch(:'batch');\n"
            )
            out = self._run(script, {"batch": batch_id, "user": user_id,
                                     "files": "\n".join(files), "stats": json.dumps(parse_stats)})
            return json.loads(out.splitlines()[-1])
        finally:
            os.unlink(csv_path)

    def rollback(self, batch_id: str) -> int:
        return int(self._run("select public.rollback_import_batch(:'batch');", {"batch": batch_id}))

    def list_batches(self, user_id: str | None) -> list[dict]:
        if user_id:
            out = self._run(LIST_BATCHES_SQL % "user_id = :'user'" + ";", {"user": user_id})
        else:
            out = self._run(LIST_BATCHES_SQL % "true" + ";")
        return json.loads(out)


def connect(dsn: str, backend: str = "auto") -> Database:
    if backend in ("auto", "psycopg"):
        try:
            return PsycopgDB(dsn)
        except ImportError:
            if backend == "psycopg":
                raise
    return PsqlDB(dsn)


def run_import(db: Database, user_id: str, parsed: ParseResult) -> tuple[str, dict]:
    batch_id = str(uuid.uuid4())
    parse_stats = {k: v for k, v in parsed.summary().items() if k != "files"}
    result = db.import_batch(batch_id, user_id, parsed.files, parse_stats,
                             staging_csv(batch_id, parsed.events))
    return batch_id, result
