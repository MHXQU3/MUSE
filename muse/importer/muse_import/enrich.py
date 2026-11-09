"""Enrichment run: ask the database what needs looking up, look it up on
MusicBrainz, send decisions back in small chunks.

Order matters: artists first (their MBIDs make album and track searches
precise), then albums (which supply release dates and languages), then
tracks. Everything is resumable: stop at any point and the next run carries
on, with already-fetched responses served from the local cache.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .matching import (
    Decision, album_fields_from_releases, artist_fields, base_album, base_title,
    match_album, match_artist_by_name, match_artist_by_recordings, match_track, track_fields,
)
from .musicbrainz import MusicBrainzClient, lucene_phrase

KINDS = ("artist", "album", "track")


@dataclass
class RunSummary:
    looked_up: dict = field(default_factory=lambda: {k: 0 for k in KINDS})
    statuses: dict = field(default_factory=dict)
    merged_artists: int = 0
    stopped_early: str | None = None

    def count(self, d: Decision) -> None:
        self.looked_up[d.kind] += 1
        key = f"{d.kind}:{d.status}"
        self.statuses[key] = self.statuses.get(key, 0) + 1


# ---------------------------------------------------------------------------
# One entity at a time
# ---------------------------------------------------------------------------

def enrich_artist(mb: MusicBrainzClient, artist: dict) -> list[Decision]:
    """Returns the artist decision, plus decisions for its top tracks when
    the artist was matched confidently (reusing the same search results)."""
    if artist.get("manual_mbid"):
        lookup = mb.lookup("artist", artist["manual_mbid"], ("genres",))
        if not lookup:
            return [Decision("artist", artist["id"], "unmatched", None, 0, {},
                             [{"error": f"MBID {artist['manual_mbid']} not found"}])]
        return [Decision("artist", artist["id"], "manual", lookup["id"], 1.0, artist_fields(lookup))]

    searches = []
    for track in artist.get("top_tracks") or []:
        q = f"recording:{lucene_phrase(base_title(track['title']))} AND artist:{lucene_phrase(artist['name'])}"
        searches.append((track, mb.search("recording", q, limit=10)))

    evidence = match_artist_by_recordings(artist, searches) if searches else None
    if evidence is None or evidence.decision.status == "unmatched":
        by_name = match_artist_by_name(
            artist, mb.search("artist", f"artist:{lucene_phrase(artist['name'])}", limit=5))
        decision = by_name if by_name.status != "unmatched" or evidence is None else evidence.decision
        evidence = None
    else:
        decision = evidence.decision

    if decision.mbid:
        lookup = mb.lookup("artist", decision.mbid, ("genres",))
        if lookup:
            decision.fields = artist_fields(lookup)

    out = [decision]
    if evidence and decision.status == "auto":
        for track in artist.get("top_tracks") or []:
            rec = evidence.recordings_by_track.get(track["id"])
            if rec:
                t = match_track({**track, "artist_mbid": decision.mbid, "rejected": []}, [rec])
                if t.status == "auto":
                    out.append(t)
    return out


def enrich_track(mb: MusicBrainzClient, track: dict) -> Decision:
    if track.get("manual_mbid"):
        rec = mb.lookup("recording", track["manual_mbid"], ("isrcs", "artist-credits"))
        if not rec:
            return Decision("track", track["id"], "unmatched", None, 0, {},
                            [{"error": f"MBID {track['manual_mbid']} not found"}])
        return Decision("track", track["id"], "manual", rec["id"], 1.0, track_fields(rec))
    q = f"recording:{lucene_phrase(base_title(track['title']))} AND arid:{track['artist_mbid']}"
    return match_track(track, mb.search("recording", q, limit=10).get("recordings") or [])


def enrich_album(mb: MusicBrainzClient, album: dict) -> Decision:
    if album.get("manual_mbid"):
        rg = mb.lookup("release-group", album["manual_mbid"], ("releases", "artist-credits"))
        if not rg:
            return Decision("album", album["id"], "unmatched", None, 0, {},
                            [{"error": f"MBID {album['manual_mbid']} not found"}])
        releases = [{**r, "release-group": rg, "artist-credit": rg.get("artist-credit")}
                    for r in rg.get("releases") or []]
        return Decision("album", album["id"], "manual", rg["id"], 1.0,
                        album_fields_from_releases(rg["id"], releases))
    q = f"release:{lucene_phrase(base_album(album['title']))} AND arid:{album['artist_mbid']}"
    return match_album(album, mb.search("release", q, limit=25).get("releases") or [])


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run_enrichment(db, mb: MusicBrainzClient, *, kinds=KINDS, limit: int | None = None,
                   minutes: float | None = None, min_plays: int = 1, chunk_size: int = 20,
                   log: Callable[[str], None] = print,
                   clock: Callable[[], float] = time.monotonic) -> RunSummary:
    summary = RunSummary()
    deadline = clock() + minutes * 60 if minutes else None
    budget = limit
    buffer: list[dict] = []

    def flush() -> None:
        if buffer:
            res = db.call("apply_musicbrainz_results", {"results": buffer})
            summary.merged_artists += res.get("artists_merged", 0)
            buffer.clear()

    try:
        for kind in KINDS:
            if kind not in kinds:
                continue
            while True:
                if deadline and clock() >= deadline:
                    summary.stopped_early = "time budget used"
                    return summary
                if budget is not None and budget <= 0:
                    summary.stopped_early = "limit reached"
                    return summary
                page = min(50, budget) if budget is not None else 50
                queue = db.call("musicbrainz_queue", {"kind": kind, "limit": page, "min_plays": min_plays})
                if not queue:
                    break
                for item in queue:
                    if deadline and clock() >= deadline:
                        summary.stopped_early = "time budget used"
                        return summary
                    if kind == "artist":
                        decisions = enrich_artist(mb, item)
                    elif kind == "album":
                        decisions = [enrich_album(mb, item)]
                    else:
                        decisions = [enrich_track(mb, item)]
                    for d in decisions:
                        summary.count(d)
                        buffer.append(d.as_result())
                    main = decisions[0]
                    label = item.get("name") or f"{item.get('title')} — {item.get('artist_name')}"
                    log(f"  {kind:<6} {main.status:<9} {main.confidence:4.2f}  {label}"
                        + (f"  →  {main.fields.get('label')}" if main.fields.get("label") else ""))
                    if budget is not None:
                        budget -= 1
                    if len(buffer) >= chunk_size:
                        flush()
                flush()  # the next queue page must see this page's results
    except KeyboardInterrupt:
        summary.stopped_early = "interrupted"
    finally:
        flush()
    return summary
