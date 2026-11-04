"""Read a Spotify Extended Streaming History export and turn it into clean
listening events. Pure Python standard library: no database needed, so this
part can be tested on its own.

Only an explicit allow-list of music fields is copied out of each record.
IP address, user agent, platform, country and username never leave this module.
"""

from __future__ import annotations

import json
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

# Guard rails for uploads.
MAX_FILE_BYTES = 500 * 1024 * 1024          # any single JSON file
MAX_TOTAL_BYTES = 3 * 1024 * 1024 * 1024    # whole export, uncompressed
MAX_MS_PLAYED = 24 * 60 * 60 * 1000         # a single play longer than a day is junk

# Current exports: Streaming_History_Audio_2023-2024_1.json
# Older exports:   endsong_0.json
AUDIO_FILE_RE = re.compile(r"(?:^|/)(Streaming_History_Audio_[^/]*|endsong_\d+)\.json$", re.I)

FEAT_RE = re.compile(r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+([^\)\]]+)[\)\]]", re.I)
# Split on commas and "&" only: "and" appears inside real names.
FEAT_SPLIT_RE = re.compile(r"\s*(?:,|&)\s*")

# Checked in order; first match wins.
VERSION_PATTERNS = [
    ("sped_up", re.compile(r"\bsped[\s-]?up\b", re.I)),
    ("slowed", re.compile(r"\bslowed\b", re.I)),
    ("remix", re.compile(r"\b(re-?mix|rmx)\b", re.I)),
    ("remaster", re.compile(r"\bremaster(ed)?\b", re.I)),
    ("live", re.compile(r"(\s-\s|\()\s*live\b", re.I)),
    ("acoustic", re.compile(r"\bacoustic\b", re.I)),
    ("edit", re.compile(r"\b(radio|clean|single)\s+edit\b|(\s-\s|\()\s*edit\b", re.I)),
]


@dataclass(frozen=True)
class Event:
    """One cleaned play, ready for the staging table."""
    ended_at: datetime
    ms_played: int
    track_name: str
    artist_name: str
    album_name: str | None
    spotify_track_uri: str
    reason_start: str | None
    reason_end: str | None
    shuffle: bool | None
    skipped: bool | None
    offline: bool | None
    incognito: bool | None
    version_type: str
    featured_artists: tuple[str, ...]


@dataclass
class ParseResult:
    events: list[Event] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    stats: Counter = field(default_factory=Counter)
    # A sample of rejected rows (row number + reason) for debugging; no
    # sensitive fields are kept.
    rejected_samples: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {"files": self.files, **dict(sorted(self.stats.items()))}


# ---------------------------------------------------------------------------
# Field helpers
# ---------------------------------------------------------------------------

def _clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value or None


def _bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    return None


def parse_ts(value) -> datetime | None:
    """Spotify's ts is ISO 8601 in UTC, e.g. 2024-03-01T18:22:10Z.
    It marks when playback STOPPED, not when it started."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if not (2006 <= dt.year <= 2100):  # Spotify launched in 2006
        return None
    return dt.astimezone(timezone.utc)


def detect_version(title: str) -> str:
    for name, pattern in VERSION_PATTERNS:
        if pattern.search(title):
            return name
    return "original"


def featured_artists(title: str) -> tuple[str, ...]:
    """'Song (feat. A & B)' -> ('A', 'B'). Approximate: it's a fallback until
    proper credits come from MusicBrainz."""
    names: list[str] = []
    for match in FEAT_RE.finditer(title):
        for name in FEAT_SPLIT_RE.split(match.group(1)):
            name = _clean_text(name)
            if name and name not in names:
                names.append(name)
    return tuple(names)


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------

def classify(raw: dict) -> tuple[str, Event | None]:
    """Return (category, event). event is only set for category 'music'.

    Categories: music, podcast, audiobook, local_file, no_metadata,
    invalid_timestamp, invalid_duration, not_a_record.
    """
    if not isinstance(raw, dict):
        return "not_a_record", None

    ended_at = parse_ts(raw.get("ts"))
    if ended_at is None:
        return "invalid_timestamp", None

    ms = raw.get("ms_played")
    if isinstance(ms, bool) or not isinstance(ms, int) or not (0 <= ms <= MAX_MS_PLAYED):
        return "invalid_duration", None

    if raw.get("spotify_episode_uri") or raw.get("episode_name"):
        return "podcast", None
    if raw.get("audiobook_uri") or raw.get("audiobook_chapter_uri") or raw.get("audiobook_title"):
        return "audiobook", None

    uri = _clean_text(raw.get("spotify_track_uri"))
    track = _clean_text(raw.get("master_metadata_track_name"))
    artist = _clean_text(raw.get("master_metadata_album_artist_name"))
    if uri and uri.startswith("spotify:local:"):
        return "local_file", None
    if not uri or not uri.startswith("spotify:track:") or not track or not artist:
        return "no_metadata", None

    return "music", Event(
        ended_at=ended_at,
        ms_played=ms,
        track_name=track,
        artist_name=artist,
        album_name=_clean_text(raw.get("master_metadata_album_album_name")),
        spotify_track_uri=uri,
        reason_start=_clean_text(raw.get("reason_start")),
        reason_end=_clean_text(raw.get("reason_end")),
        shuffle=_bool(raw.get("shuffle")),
        skipped=_bool(raw.get("skipped")),
        offline=_bool(raw.get("offline")),
        incognito=_bool(raw.get("incognito_mode")),
        version_type=detect_version(track),
        featured_artists=featured_artists(track),
    )


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------

def _read_zip(path: Path) -> Iterator[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as zf:
        members = [m for m in zf.infolist() if not m.is_dir() and AUDIO_FILE_RE.search(m.filename)]
        total = sum(m.file_size for m in members)
        if total > MAX_TOTAL_BYTES:
            raise ValueError(f"{path.name}: export is {total / 1e9:.1f} GB uncompressed, over the limit")
        for m in sorted(members, key=lambda m: m.filename):
            if m.file_size > MAX_FILE_BYTES:
                raise ValueError(f"{m.filename}: file too large ({m.file_size / 1e6:.0f} MB)")
            # Read into memory; nothing is extracted to disk, so paths inside
            # the ZIP can't escape anywhere.
            yield m.filename, zf.read(m)


def iter_export_files(paths: Iterable[str | Path]) -> Iterator[tuple[str, list]]:
    """Accepts the export ZIP, the extracted folder, or individual JSON files.
    Yields (file name, list of raw records) for each audio history file."""
    for p in map(Path, paths):
        if p.is_dir():
            sources = [(str(f.relative_to(p)), f) for f in sorted(p.rglob("*.json"))
                       if AUDIO_FILE_RE.search(f.as_posix())]
            blobs = ((name, f.read_bytes()) for name, f in sources if f.stat().st_size <= MAX_FILE_BYTES)
        elif p.suffix.lower() == ".zip":
            blobs = _read_zip(p)
        elif p.suffix.lower() == ".json":
            if p.stat().st_size > MAX_FILE_BYTES:
                raise ValueError(f"{p.name}: file too large")
            blobs = iter([(p.name, p.read_bytes())])
        else:
            raise ValueError(f"{p}: expected a .zip, a .json file or a folder")

        for name, blob in blobs:
            try:
                data = json.loads(blob.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"{name}: not valid JSON ({exc})") from exc
            if not isinstance(data, list):
                raise ValueError(f"{name}: expected a JSON list of plays")
            yield name, data


def parse_export(paths: Iterable[str | Path], max_rejected_samples: int = 20) -> ParseResult:
    result = ParseResult()
    seen: set[tuple[datetime, str]] = set()

    for name, records in iter_export_files(paths):
        result.files.append(name)
        for i, raw in enumerate(records):
            result.stats["rows_read"] += 1
            category, event = classify(raw)
            if category != "music":
                result.stats[f"skipped_{category}"] += 1
                if category.startswith(("invalid", "not_a")) and len(result.rejected_samples) < max_rejected_samples:
                    result.rejected_samples.append({"file": name, "row": i, "reason": category})
                continue
            key = (event.ended_at, event.spotify_track_uri)
            if key in seen:
                result.stats["duplicates_in_export"] += 1
                continue
            seen.add(key)
            result.events.append(event)

    if result.stats["rows_read"] == 0:
        raise ValueError("No Spotify audio history files found. Expected files named "
                         "Streaming_History_Audio_*.json (or endsong_*.json).")

    _unify_artist_spelling(result)
    result.events.sort(key=lambda e: e.ended_at)
    result.stats["music_events"] = len(result.events)
    if result.events:
        result.stats["first_play"] = result.events[0].ended_at.isoformat()  # type: ignore[assignment]
        result.stats["last_play"] = result.events[-1].ended_at.isoformat()  # type: ignore[assignment]
    return result


def _key(name: str) -> str:
    return " ".join(name.split()).lower()


def _unify_artist_spelling(result: ParseResult) -> None:
    """If one artist appears as 'Nayla Rif' and 'nayla  rif', use the most
    common spelling everywhere, so the artist gets created with a clean name."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for e in result.events:
        counts[_key(e.artist_name)][e.artist_name] += 1
        for f in e.featured_artists:
            counts[_key(f)][f] += 1
    canonical = {k: c.most_common(1)[0][0] for k, c in counts.items()}
    variants = sum(1 for c in counts.values() if len(c) > 1)
    if not variants:
        return
    result.stats["artist_spelling_variants_merged"] = variants

    def fix(e: Event) -> Event:
        return Event(**{**e.__dict__,
                        "artist_name": canonical[_key(e.artist_name)],
                        "featured_artists": tuple(canonical[_key(f)] for f in e.featured_artists)})

    result.events = [fix(e) for e in result.events]
