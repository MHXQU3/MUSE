"""Decide which MusicBrainz entry a MUSE artist, album or track is.

Pure functions over MusicBrainz JSON, so every rule here is unit-testable.

How artists are matched
    Searching for an artist by name alone is unreliable: many artists share a
    name. Instead MUSE searches for your most-played songs by that artist and
    lets the results vote. A MusicBrainz artist who is credited on recordings
    with the right titles is almost certainly the right one. A name-only match
    is only ever a *suggestion* for you to confirm.

Confidence thresholds
    auto       applied immediately
    suggested  stored, waits for you to confirm or reject
    unmatched  nothing good enough
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher

from .parse import FEAT_RE, detect_version

ARTIST_AUTO, ARTIST_SUGGEST = 0.75, 0.40
TRACK_AUTO, TRACK_SUGGEST = 0.80, 0.60
ALBUM_AUTO, ALBUM_SUGGEST = 0.85, 0.60

COVER_URL = "https://coverartarchive.org/release-group/{}/front-250"

_VERSION_SUFFIX_RE = re.compile(
    r"\s+-\s+.*\b(remix|rmx|remaster(ed)?|live|acoustic|edit|version|mix|sped|slowed|mono|stereo)\b.*$"
    r"|\s*[\(\[][^\)\]]*\b(remix|rmx|remaster(ed)?|live|acoustic|edit|version|mix|sped|slowed|mono|stereo)\b[^\)\]]*[\)\]]",
    re.I)
_EDITION_RE = re.compile(
    r"\s*[\(\[][^\)\]]*\b(deluxe|edition|expanded|remaster(ed)?|anniversary|bonus|version)\b[^\)\]]*[\)\]]"
    r"|\s+-\s+.*\b(deluxe|edition|expanded|remaster(ed)?|anniversary)\b.*$"
    r"|\s+(deluxe|expanded)(\s+(edition|version))?\s*$", re.I)


@dataclass
class Decision:
    kind: str
    id: str
    status: str                    # auto | suggested | unmatched | manual
    mbid: str | None = None
    confidence: float = 0.0
    fields: dict = field(default_factory=dict)
    candidates: list = field(default_factory=list)

    def as_result(self) -> dict:
        return {"kind": self.kind, "id": self.id, "status": self.status, "mbid": self.mbid,
                "confidence": round(self.confidence, 3), "fields": self.fields,
                "candidates": self.candidates[:5]}


# ---------------------------------------------------------------------------
# Text comparison
# ---------------------------------------------------------------------------

# Letters Unicode doesn't decompose into a base letter plus accent.
_LETTERS = str.maketrans({"œ": "oe", "Œ": "oe", "æ": "ae", "Æ": "ae", "ø": "o", "Ø": "o",
                          "đ": "d", "Đ": "d", "ł": "l", "Ł": "l", "ı": "i",
                          "’": "'", "‘": "'", "‐": "-", "–": "-", "—": "-"})


def fold(s: str | None) -> str:
    """Compare-friendly form: no accents, case, punctuation or extra spaces.
    'Sélène B' -> 'selene b', 'JAY-Z' -> 'jay z', 'Simon & Garfunkel' -> 'simon and garfunkel'."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s.translate(_LETTERS))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.casefold().replace("&", " and ")
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def similarity(a: str | None, b: str | None) -> float:
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return 0.0
    if fa == fb:
        return 1.0
    return SequenceMatcher(None, fa, fb).ratio()


def base_title(title: str) -> str:
    """'Marea (feat. X) - Remastered 2024' -> 'Marea'. Used for searching and
    comparing; the version is judged separately."""
    t = FEAT_RE.sub("", title)
    t = _VERSION_SUFFIX_RE.sub("", t)
    return " ".join(t.split()) or title


def base_album(title: str) -> str:
    t = _EDITION_RE.sub("", title)
    return " ".join(t.split()) or title


def _credits(entity: dict) -> list[tuple[str, str, str]]:
    """[(artist mbid, artist name, credited-as name)] from an artist-credit."""
    out = []
    for c in entity.get("artist-credit") or []:
        a = c.get("artist") or {}
        if a.get("id"):
            out.append((a["id"], a.get("name", ""), c.get("name") or a.get("name", "")))
    return out


def credit_string(entity: dict) -> str:
    return "".join((c.get("name") or "") + (c.get("joinphrase") or "")
                   for c in entity.get("artist-credit") or []) or "?"


def _mmss(ms) -> str:
    if not isinstance(ms, int):
        return "?:??"
    return f"{ms // 60000}:{ms // 1000 % 60:02d}"


def parse_mb_date(value: str | None) -> tuple[str | None, str | None]:
    """'2019' -> ('2019-01-01', 'year'); '2019-05' -> ('2019-05-01', 'month')."""
    if not value:
        return None, None
    parts = value.split("-")
    try:
        nums = [int(p) for p in parts[:3]]
        d = date(nums[0], nums[1] if len(nums) > 1 else 1, nums[2] if len(nums) > 2 else 1)
    except (ValueError, IndexError):
        return None, None
    return d.isoformat(), ["year", "month", "day"][min(len(nums), 3) - 1]


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------

@dataclass
class ArtistEvidence:
    """The artist decision, plus the recordings that won the vote, so the
    artist's top tracks can be matched without searching again."""
    decision: Decision
    recordings_by_track: dict[str, dict] = field(default_factory=dict)


def match_artist_by_recordings(artist: dict, searches: list[tuple[dict, dict]]) -> ArtistEvidence | None:
    """artist: queue item {id, name, rejected}. searches: [(track item, recording
    search JSON)]. Returns None if no recording gave any credible vote."""
    rejected = set(artist.get("rejected") or [])
    votes: dict[str, float] = defaultdict(float)
    names: dict[str, str] = {}
    evidence: dict[str, dict[str, tuple[float, dict]]] = defaultdict(dict)

    for track, result in searches:
        best_for_track: dict[str, float] = {}
        want = base_title(track["title"])
        for rec in result.get("recordings") or []:
            t_sim = similarity(want, base_title(rec.get("title", "")))
            if t_sim < 0.75:
                continue
            mb_score = (rec.get("score") or 0) / 100
            for aid, aname, cname in _credits(rec):
                if aid in rejected:
                    continue
                n_sim = max(similarity(artist["name"], aname), similarity(artist["name"], cname))
                if n_sim < 0.85:
                    continue
                v = t_sim * n_sim * (0.5 + 0.5 * mb_score)
                if v > best_for_track.get(aid, 0):
                    best_for_track[aid] = v
                    names[aid] = aname
                    prev = evidence[aid].get(track["id"])
                    if not prev or v > prev[0]:
                        evidence[aid][track["id"]] = (v, rec)
        for aid, v in best_for_track.items():
            votes[aid] += v

    if not votes:
        return None

    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    top_id, top_v = ranked[0]
    second_v = ranked[1][1] if len(ranked) > 1 else 0.0
    support = top_v / max(len(searches), 1)        # how well, on average, each song backs it
    separation = top_v / (top_v + second_v)        # how clearly it beats the runner-up
    confidence = support * separation
    if len(searches) == 1:
        confidence = min(confidence, 0.85)         # one song is thinner evidence

    status = "auto" if confidence >= ARTIST_AUTO else "suggested" if confidence >= ARTIST_SUGGEST else "unmatched"
    candidates = [{"mbid": aid, "name": names[aid], "votes": round(v, 3)} for aid, v in ranked[:5]]
    decision = Decision("artist", artist["id"], status, top_id if status != "unmatched" else None,
                        confidence, {}, candidates)
    return ArtistEvidence(decision, {tid: rec for tid, (_, rec) in evidence[top_id].items()})


def match_artist_by_name(artist: dict, result: dict) -> Decision:
    """Fallback when no songs matched. Never 'auto': a name alone can't tell
    two artists with the same name apart."""
    rejected = set(artist.get("rejected") or [])
    exact = []
    for a in result.get("artists") or []:
        if a.get("id") in rejected:
            continue
        aliases = [x.get("name") for x in a.get("aliases") or []]
        if fold(a.get("name")) == fold(artist["name"]) or fold(artist["name"]) in map(fold, aliases):
            exact.append(a)
    candidates = [{"mbid": a["id"], "name": a.get("name"), "disambiguation": a.get("disambiguation"),
                   "country": a.get("country"), "score": a.get("score")} for a in exact[:5]]
    if len(exact) == 1 and (exact[0].get("score") or 0) >= 90:
        return Decision("artist", artist["id"], "suggested", exact[0]["id"], 0.55, {}, candidates)
    return Decision("artist", artist["id"], "unmatched", None, 0.0, {}, candidates)


def artist_fields(lookup: dict) -> dict:
    """Fields to store, from GET /artist/{mbid}?inc=genres."""
    country = lookup.get("country")
    if not country:
        codes = (lookup.get("area") or {}).get("iso-3166-1-codes") or []
        country = codes[0] if codes else None
    genres = sorted((g for g in lookup.get("genres") or [] if (g.get("count") or 0) > 0),
                    key=lambda g: -(g.get("count") or 0))[:8]
    label = lookup.get("name", "?")
    if lookup.get("disambiguation"):
        label += f" ({lookup['disambiguation']})"
    if country:
        label += f" [{country}]"
    return {
        "label": label,
        "sort_name": lookup.get("sort-name"),
        "country": country,
        "type": lookup.get("type"),
        "disambiguation": lookup.get("disambiguation") or None,
        "genres": [{"name": g["name"], "count": g.get("count", 1)} for g in genres],
    }


# ---------------------------------------------------------------------------
# Tracks
# ---------------------------------------------------------------------------

def _duration_score(est_ms, mb_ms) -> float:
    if not isinstance(est_ms, int) or not isinstance(mb_ms, int):
        return 0.5  # unknown: neither helps nor hurts
    diff = abs(est_ms - mb_ms)
    if diff <= 3000:
        return 1.0
    return max(0.0, 1 - (diff - 3000) / 27000)


def _version_score(muse_version: str, rec: dict) -> float:
    mb_version = detect_version(f"{rec.get('title', '')} ({rec.get('disambiguation') or ''})")
    if muse_version == mb_version:
        return 1.0
    # MusicBrainz usually treats a remaster as the same recording.
    if muse_version == "remaster" and mb_version == "original":
        return 1.0
    return 0.0


def score_recording(track: dict, rec: dict, artist_mbid: str) -> float:
    """0..1. Title carries most weight; duration confirms it. A wrong version
    (remix vs original, live vs studio) or a duration 30+ seconds off are
    strong signs it's a different recording, so they cut the score hard
    rather than just losing a few points."""
    if artist_mbid not in {aid for aid, _, _ in _credits(rec)}:
        return 0.0
    t_sim = similarity(base_title(track["title"]), base_title(rec.get("title", "")))
    dur = _duration_score(track.get("est_duration_ms"), rec.get("length"))
    score = 0.6 * t_sim + 0.3 * dur + 0.1 * (rec.get("score") or 100) / 100
    if _version_score(track.get("version_type", "original"), rec) == 0:
        score *= 0.55
    if dur == 0:
        score *= 0.75
    return score


def track_fields(rec: dict) -> dict:
    isrcs = rec.get("isrcs") or []
    return {
        "label": f"{rec.get('title', '?')} — {credit_string(rec)} ({_mmss(rec.get('length'))})",
        "isrc": isrcs[0] if isrcs else None,
        "length_ms": rec.get("length") if isinstance(rec.get("length"), int) else None,
    }


def match_track(track: dict, recordings: list[dict]) -> Decision:
    """track: queue item {id, title, version_type, est_duration_ms, artist_mbid, rejected}."""
    rejected = set(track.get("rejected") or [])
    scored = sorted(((score_recording(track, r, track["artist_mbid"]), r) for r in recordings
                     if r.get("id") not in rejected), key=lambda sr: -sr[0])
    scored = [(s, r) for s, r in scored if s > 0]
    candidates = [{"mbid": r["id"], "title": r.get("title"), "length": r.get("length"),
                   "score": round(s, 3)} for s, r in scored[:5]]
    if not scored:
        return Decision("track", track["id"], "unmatched", None, 0.0, {}, candidates)
    best_score, best = scored[0]
    status = "auto" if best_score >= TRACK_AUTO else "suggested" if best_score >= TRACK_SUGGEST else "unmatched"
    if status == "unmatched":
        return Decision("track", track["id"], status, None, best_score, {}, candidates)
    return Decision("track", track["id"], status, best["id"], best_score, track_fields(best), candidates)


# ---------------------------------------------------------------------------
# Albums
# ---------------------------------------------------------------------------

_LANG_IGNORE = {None, "", "mul", "zxx", "und"}


def album_fields_from_releases(rg_id: str, releases: list[dict]) -> dict:
    """Combine every release in one release group into album fields."""
    rg = next((r.get("release-group") for r in releases if r.get("release-group")), {}) or {}
    dates = sorted(r["date"] for r in releases if r.get("date"))
    release_date, precision = parse_mb_date(dates[0] if dates else rg.get("first-release-date"))
    langs = Counter((r.get("text-representation") or {}).get("language") for r in releases)
    for bad in _LANG_IGNORE:
        langs.pop(bad, None)
    secondary = [s.lower() for s in rg.get("secondary-types") or []]
    primary = (rg.get("primary-type") or "").lower()
    album_type = ("compilation" if "compilation" in secondary
                  else primary if primary in ("album", "single", "ep") else "other" if primary else None)
    title = releases[0].get("title", "?") if releases else rg.get("title", "?")
    return {
        "label": f"{title} — {credit_string(releases[0]) if releases else '?'} ({(dates or ['?'])[0][:4]})",
        "release_date": release_date,
        "release_date_precision": precision,
        "album_type": album_type,
        "language": langs.most_common(1)[0][0] if langs else None,
        "cover_url": COVER_URL.format(rg_id),
    }


def match_album(album: dict, releases: list[dict]) -> Decision:
    """album: queue item {id, title, artist_mbid, rejected}. releases: from a
    release search. Releases are grouped into release groups (an album
    across all its editions) and the best-titled group wins."""
    rejected = set(album.get("rejected") or [])
    want = base_album(album["title"])
    groups: dict[str, list[dict]] = defaultdict(list)
    group_score: dict[str, float] = {}
    for rel in releases:
        rg_id = (rel.get("release-group") or {}).get("id")
        if not rg_id or rg_id in rejected:
            continue
        if album["artist_mbid"] not in {aid for aid, _, _ in _credits(rel)}:
            continue
        t_sim = similarity(want, base_album(rel.get("title", "")))
        s = t_sim * (0.85 + 0.15 * (rel.get("score") or 100) / 100)
        if rel.get("status") == "Official":
            s = min(1.0, s + 0.02)
        groups[rg_id].append(rel)
        group_score[rg_id] = max(group_score.get(rg_id, 0), s)

    ranked = sorted(group_score.items(), key=lambda kv: -kv[1])
    candidates = [{"mbid": rg, "title": groups[rg][0].get("title"), "score": round(s, 3)}
                  for rg, s in ranked[:5]]
    if not ranked:
        return Decision("album", album["id"], "unmatched", None, 0.0, {}, candidates)
    rg_id, best = ranked[0]
    status = "auto" if best >= ALBUM_AUTO else "suggested" if best >= ALBUM_SUGGEST else "unmatched"
    if status == "unmatched":
        return Decision("album", album["id"], status, None, best, {}, candidates)
    return Decision("album", album["id"], status, rg_id, best,
                    album_fields_from_releases(rg_id, groups[rg_id]), candidates)
