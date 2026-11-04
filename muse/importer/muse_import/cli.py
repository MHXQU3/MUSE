"""MUSE importer command line.

  python -m muse_import check   EXPORT...                 # parse only, no database
  python -m muse_import import  EXPORT... --email you@x   # load into the database
  python -m muse_import batches [--email you@x]
  python -m muse_import rollback BATCH_ID

EXPORT can be the ZIP Spotify sends, the extracted folder, or JSON files.
The database connection string comes from --db or the MUSE_DATABASE_URL
environment variable (Supabase: Project Settings > Database > Connection string).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .load import connect, run_import
from .parse import parse_export


def _db(args):
    dsn = args.db or os.environ.get("MUSE_DATABASE_URL")
    if not dsn:
        sys.exit("No database given. Use --db or set MUSE_DATABASE_URL.")
    return connect(dsn, args.backend)


def _resolve_user(db, args) -> str | None:
    if getattr(args, "user_id", None):
        return args.user_id
    if getattr(args, "email", None):
        uid = db.user_id_for_email(args.email)
        if not uid:
            sys.exit(f"No MUSE account with email {args.email}. Sign up in the app first.")
        return uid
    return None


def cmd_check(args) -> None:
    parsed = parse_export(args.paths)
    print(json.dumps(parsed.summary(), indent=2, ensure_ascii=False))
    if parsed.rejected_samples:
        print("\nSample of rejected rows:")
        for r in parsed.rejected_samples:
            print(f"  {r['file']} row {r['row']}: {r['reason']}")


def cmd_import(args) -> None:
    parsed = parse_export(args.paths)
    s = parsed.stats
    print(f"Read {s['rows_read']:,} rows from {len(parsed.files)} file(s): "
          f"{s['music_events']:,} music plays to load.")
    db = _db(args)
    user_id = _resolve_user(db, args)
    if not user_id:
        sys.exit("Say whose history this is with --email or --user-id.")
    batch_id, result = run_import(db, user_id, parsed)
    print(f"Batch {batch_id}")
    print(f"  new plays stored:   {result['events_inserted']:,}")
    print(f"  already stored:     {result['already_stored']:,}")
    print(f"  new artists/albums/tracks: {result['new_artists']}/{result['new_albums']}/{result['new_tracks']}")
    print(f"Undo with: python -m muse_import rollback {batch_id}")


def cmd_batches(args) -> None:
    db = _db(args)
    for b in db.list_batches(_resolve_user(db, args)):
        print(f"{b['id']}  {b['status']:<11}  {b['created_at'][:19]}  "
              f"{b['events_inserted'] or 0:>8} plays  {', '.join(b['file_names'])[:60]}")


def cmd_rollback(args) -> None:
    removed = _db(args).rollback(args.batch_id)
    print(f"Rolled back batch {args.batch_id}: removed {removed:,} plays.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="muse_import", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def db_opts(p):
        p.add_argument("--db", help="Postgres connection string (default: $MUSE_DATABASE_URL)")
        p.add_argument("--backend", choices=["auto", "psycopg", "psql"], default="auto")

    p = sub.add_parser("check", help="parse an export and report what's in it")
    p.add_argument("paths", nargs="+")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("import", help="load an export into the database")
    p.add_argument("paths", nargs="+")
    who = p.add_mutually_exclusive_group()
    who.add_argument("--email")
    who.add_argument("--user-id")
    db_opts(p)
    p.set_defaults(fn=cmd_import)

    p = sub.add_parser("batches", help="list recent imports")
    p.add_argument("--email")
    db_opts(p)
    p.set_defaults(fn=cmd_batches)

    p = sub.add_parser("rollback", help="remove the plays an import added")
    p.add_argument("batch_id")
    db_opts(p)
    p.set_defaults(fn=cmd_rollback)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (ValueError, RuntimeError) as exc:
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
