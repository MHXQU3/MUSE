"""MUSE importer command line.

  python -m muse_import check   EXPORT...                 # parse only, no database
  python -m muse_import import  EXPORT... --email you@x   # load into the database
  python -m muse_import batches [--email you@x]
  python -m muse_import rollback BATCH_ID

  python -m muse_import enrich  [--minutes 60] [--only artist]  # MusicBrainz lookups
  python -m muse_import coverage                                # how much is enriched
  python -m muse_import review                                  # suggestions to check
  python -m muse_import confirm MATCH_ID... / reject MATCH_ID...
  python -m muse_import set-mbid artist "Name" MBID             # fix a match by hand

EXPORT can be the ZIP Spotify sends, the extracted folder, or JSON files.
The database connection string comes from --db or the MUSE_DATABASE_URL
environment variable. MusicBrainz requires contact details (an email address
or URL) in every request: pass --contact or set MUSE_CONTACT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid as uuid_mod

from .enrich import KINDS, run_enrichment
from .load import connect, run_import
from .musicbrainz import MusicBrainzClient
from .parse import parse_export

DEFAULT_CACHE = os.path.join(os.path.expanduser("~"), ".muse", "musicbrainz_cache.sqlite")


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


def _hours(ms) -> str:
    return f"{(ms or 0) / 3.6e6:,.1f} h"


def cmd_enrich(args) -> None:
    db = _db(args)
    os.makedirs(os.path.dirname(args.cache), exist_ok=True)
    mb = MusicBrainzClient(args.contact or os.environ.get("MUSE_CONTACT", ""), args.cache)
    kinds = tuple(args.only.split(",")) if args.only else KINDS
    for k in kinds:
        if k not in KINDS:
            sys.exit(f"--only takes {', '.join(KINDS)}")
    print("Looking things up on MusicBrainz (about one request per second; Ctrl-C stops safely).")
    summary = run_enrichment(db, mb, kinds=kinds, limit=args.limit, minutes=args.minutes,
                             min_plays=args.min_plays, log=print if args.verbose else (lambda _m: None))
    print(f"\nRequests: {mb.requests_made:,} to MusicBrainz, {mb.cache_hits:,} from cache.")
    for k, v in sorted(summary.statuses.items()):
        print(f"  {k:<20} {v:,}")
    if summary.merged_artists:
        print(f"  artists merged (same MusicBrainz artist): {summary.merged_artists}")
    if summary.stopped_early:
        print(f"Stopped: {summary.stopped_early}. Run again to continue where it left off.")
    _print_coverage(db.call("enrichment_coverage", {}))


def _print_coverage(c: dict) -> None:
    share = c.get("listening_share") or {}
    print("\nCoverage")
    print(f"  artists matched  {c['artists_matched']:,} / {c['artists_total']:,}")
    print(f"  albums matched   {c['albums_matched']:,} / {c['albums_total']:,}")
    print(f"  tracks matched   {c['tracks_matched']:,} / {c['tracks_total']:,}")
    print("  share of your listening time with:")
    for key, label in [("artist_matched", "matched artist"), ("country", "artist country"),
                       ("genre", "genre"), ("language", "language"),
                       ("release_date", "release date"), ("track_matched", "matched track")]:
        print(f"    {label:<16} {share.get(key) or 0:5.1f}%")
    if c.get("suggestions_waiting"):
        print(f"  {c['suggestions_waiting']} suggestions waiting: run `python -m muse_import review`")


def cmd_coverage(args) -> None:
    _print_coverage(_db(args).call("enrichment_coverage", {}))


def cmd_review(args) -> None:
    items = _db(args).call("musicbrainz_review", {"kind": args.kind, "limit": args.limit})
    if not items:
        print("Nothing to review.")
        return
    print("Suggested matches (not applied yet). Check the link, then confirm or reject by id.\n")
    for m in items:
        entity = {"album": "release-group", "track": "recording"}.get(m["entity_type"], m["entity_type"])
        print(f"[{m['match_id']}] {m['entity_type']}: {m['muse_name']}  ({_hours(m['listening_ms'])} listened)")
        print(f"      looks like: {m['candidate'] or '?'}   confidence {m['confidence']}")
        print(f"      https://musicbrainz.org/{entity}/{m['mbid']}\n")
    print("python -m muse_import confirm ID [ID...]   |   python -m muse_import reject ID [ID...]")


def cmd_resolve(args) -> None:
    db = _db(args)
    for match_id in args.match_ids:
        db.call("resolve_musicbrainz_match", {"match_id": match_id, "decision": args.decision})
        print(f"{args.decision}ed {match_id}")


def cmd_set_mbid(args) -> None:
    db = _db(args)
    target = args.target
    try:
        uuid_mod.UUID(target)
        entity_id = target
    except ValueError:
        found = db.call("find_entity", {"kind": args.kind, "name": target})
        if not found:
            sys.exit(f"No {args.kind} called {target!r} in MUSE.")
        if len(found) > 1:
            print(f"Several {args.kind}s match {target!r}; use the id instead:")
            for f in found:
                print(f"  {f['id']}  {f['label']}")
            sys.exit(1)
        entity_id = found[0]["id"]
    mbid = args.mbid.rstrip("/").split("/")[-1]  # accept a pasted MusicBrainz URL
    db.call("set_manual_musicbrainz_id", {"kind": args.kind, "id": entity_id, "mbid": mbid})
    print(f"Set. The next `enrich` run will fetch details for {mbid}.")


def cmd_requeue(args) -> None:
    n = _db(args).call("requeue_musicbrainz", {"kind": args.kind, "only_unmatched": not args.all})
    print(f"{n:,} {args.kind}s will be looked up again on the next enrich run.")


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

    p = sub.add_parser("enrich", help="look up artists, albums and tracks on MusicBrainz")
    p.add_argument("--contact", help="your email or a URL, sent to MusicBrainz (default: $MUSE_CONTACT)")
    p.add_argument("--only", help="comma-separated: artist,album,track")
    p.add_argument("--minutes", type=float, help="stop after this long (safe to resume)")
    p.add_argument("--limit", type=int, help="stop after this many lookups")
    p.add_argument("--min-plays", type=int, default=1, help="skip things played fewer times")
    p.add_argument("--cache", default=DEFAULT_CACHE, help=f"response cache (default {DEFAULT_CACHE})")
    p.add_argument("-q", "--quiet", dest="verbose", action="store_false", help="no per-item lines")
    db_opts(p)
    p.set_defaults(fn=cmd_enrich)

    p = sub.add_parser("coverage", help="how much of your listening has metadata")
    db_opts(p)
    p.set_defaults(fn=cmd_coverage)

    p = sub.add_parser("review", help="list suggested matches waiting for a decision")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--limit", type=int, default=30)
    db_opts(p)
    p.set_defaults(fn=cmd_review)

    for name in ("confirm", "reject"):
        p = sub.add_parser(name, help=f"{name} suggested matches by id")
        p.add_argument("match_ids", nargs="+", type=int)
        db_opts(p)
        p.set_defaults(fn=cmd_resolve, decision=name)

    p = sub.add_parser("set-mbid", help="set the MusicBrainz id for something by hand")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("target", help="MUSE id, or the exact name / title")
    p.add_argument("mbid", help="MusicBrainz id or URL")
    db_opts(p)
    p.set_defaults(fn=cmd_set_mbid)

    p = sub.add_parser("requeue", help="look things up again on the next enrich run")
    p.add_argument("kind", choices=KINDS)
    p.add_argument("--all", action="store_true", help="include already-matched ones")
    db_opts(p)
    p.set_defaults(fn=cmd_requeue)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except (ValueError, RuntimeError) as exc:  # includes MusicBrainzError
        sys.exit(f"Error: {exc}")


if __name__ == "__main__":
    main()
