"""A stand-in for the MusicBrainz web service, for tests.

It builds a small MusicBrainz-shaped catalogue from the invented artists in
the test export and answers the same URLs the real client calls, with JSON in
the same shape as the real API. It is deliberately awkward:

* "Kalis" exists twice: the Belgian rapper from the export, and a decoy
  Polish metal band with the same name. Only the rapper's songs match.
* "نور الهدى" isn't findable at all (only by MBID, under a Latin name).
* "Mira Solace" exists, but none of her songs do: name-only match.
* MusicBrainz titles drop "(feat. ...)", remasters share the original
  recording, sped-up versions are their own recording, and every artist has
  a decoy 7-minute live recording of their first song.
* "Lil' Vesper" (a second spelling in MUSE) is an alias of "Lil Vesper".
"""

from __future__ import annotations

import json
import re
import sys
import urllib.parse
import uuid
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "importer"))

from generate_test_export import ARTISTS  # noqa: E402
from muse_import.matching import base_title, fold  # noqa: E402

NS = uuid.UUID("6f1c2b8e-1d2a-4c55-9a3e-6b7f0c9d1e21")
GENRES = {
    "us_hiphop": [("hip hop", 12), ("trap", 8), ("pop rap", 2)],
    "fr_rap": [("french hip hop", 10), ("cloud rap", 4)],
    "es_urban": [("reggaeton", 9), ("latin trap", 6)],
    "north_africa": [("raï", 7), ("chaabi", 3)],
    "other": [("indie pop", 5)],
}
LANG = {"US": "eng", "GB": "eng", "SE": "eng", "FR": "fra", "BE": "fra", "ES": "spa",
        "EG": "ara", "DZ": "fra", "MA": "ara"}
ARABIC_ARTIST = "نور الهدى"
NOUR_MBID = str(uuid.uuid5(NS, "artist:Nour El Houda"))


def mbid(*parts) -> str:
    return str(uuid.uuid5(NS, ":".join(parts)))


class FakeMusicBrainz:
    def __init__(self, events):
        """events: parsed Event objects from the test export (gives the songs,
        albums and durations each artist really has)."""
        self.requests: list[str] = []
        self.user_agents: set[str] = set()
        self.artists: dict[str, dict] = {}
        self.recordings: list[dict] = []
        self.releases: list[dict] = []
        self.release_groups: dict[str, dict] = {}

        scene_of = {a: s for a, _, s in ARTISTS}
        country_of = {a: c for a, c, _ in ARTISTS}
        for name, country, scene in ARTISTS:
            if name == ARABIC_ARTIST:
                continue
            aliases = [{"name": "Lil' Vesper"}] if name == "Lil Vesper" else []
            self.artists[mbid("artist", name)] = {
                "id": mbid("artist", name), "name": name, "sort-name": name, "type": "Person",
                "country": country, "disambiguation": "rapper" if name == "Kalis" else "",
                "aliases": aliases, "genres": [{"name": g, "count": c} for g, c in GENRES[scene]],
            }
        decoy = mbid("artist", "Kalis", "metal")
        self.artists[decoy] = {"id": decoy, "name": "Kalis", "sort-name": "Kalis", "type": "Group",
                               "country": "PL", "disambiguation": "Polish metal band", "aliases": [],
                               "genres": [{"name": "black metal", "count": 6}]}
        self.artists[NOUR_MBID] = {"id": NOUR_MBID, "name": "Nour El Houda", "sort-name": "El Houda, Nour",
                                   "type": "Person", "country": "MA", "disambiguation": "", "aliases": [],
                                   "genres": [{"name": "chaabi", "count": 4}]}
        self.decoy_kalis = decoy

        # Songs and albums, from what the export says each artist has.
        durations: dict[tuple, int] = defaultdict(int)
        albums: dict[tuple, set] = defaultdict(set)
        for e in events:
            k = (e.artist_name, e.track_name, e.album_name)
            if e.reason_end == "trackdone":
                durations[k] = max(durations[k], e.ms_played)
            albums[(e.artist_name, e.album_name)].add(e.track_name)

        made = set()
        for (artist, title, album), length in durations.items():
            if artist in (ARABIC_ARTIST, "Mira Solace") or artist not in scene_of:
                continue
            aid = mbid("artist", artist)
            t = base_title(title)
            if "Sped Up" in title:
                t += " (sped up)"
            key = (artist, fold(t))
            if key in made or not length:
                continue
            made.add(key)
            rg = mbid("rg", artist, album)
            self.recordings.append(self._recording(mbid("rec", artist, t), t, aid, artist, length, rg, album))
        # Decoys: the decoy Kalis has songs with different titles; each real
        # artist has a 7-minute live recording of one song.
        self.recordings.append(self._recording(mbid("rec", "decoy"), "Hammer of Winter", decoy, "Kalis", 400_000,
                                               mbid("rg", "decoy"), "Frost"))
        for aid, a in list(self.artists.items()):
            first = next((r for r in self.recordings if r["artist-credit"][0]["artist"]["id"] == aid), None)
            if first and aid != decoy:
                self.recordings.append(self._recording(
                    mbid("rec", a["name"], "live"), first["title"], aid, a["name"], 420_000,
                    mbid("rg", a["name"], "live"), "Live in Lyon", disambiguation="live, 2025"))

        for (artist, album), _tracks in albums.items():
            if artist in (ARABIC_ARTIST, "Mira Solace") or artist not in scene_of or not album:
                continue
            aid = mbid("artist", artist)
            rg_id = mbid("rg", artist, album)
            title = album.replace(" Deluxe", " (Deluxe Edition)")
            rg = {"id": rg_id, "title": title, "primary-type": "Album" if "EP" not in album else "EP",
                  "secondary-types": [], "first-release-date": "2023-03-10"}
            self.release_groups[rg_id] = rg
            for i, (date, status) in enumerate([("2023-03-10", "Official"), ("2024", "Official")]):
                self.releases.append({
                    "id": mbid("rel", artist, album, str(i)), "title": title, "status": status,
                    "date": date, "country": country_of[artist],
                    "text-representation": {"language": LANG[country_of[artist]] if i == 0 else None,
                                            "script": "Latn"},
                    "artist-credit": [{"name": artist, "joinphrase": "",
                                       "artist": {"id": aid, "name": artist}}],
                    "release-group": {k: rg[k] for k in ("id", "title", "primary-type", "secondary-types")},
                })

    @staticmethod
    def _recording(rid, title, aid, aname, length, rg_id, album, disambiguation=""):
        return {
            "id": rid, "title": title, "length": length, "disambiguation": disambiguation,
            "isrcs": [f"TEST{abs(hash(rid)) % 10**8:08d}"],
            "artist-credit": [{"name": aname, "joinphrase": "", "artist": {"id": aid, "name": aname}}],
            "releases": [{"id": rg_id, "title": album, "release-group": {"id": rg_id}}],
        }

    # -- the transport ------------------------------------------------------

    def __call__(self, url: str, headers: dict):
        self.requests.append(url)
        self.user_agents.add(headers.get("User-Agent", ""))
        parsed = urllib.parse.urlparse(url)
        params = dict(urllib.parse.parse_qsl(parsed.query))
        path = parsed.path.removeprefix("/ws/2/").rstrip("/")
        if "/" in path:
            entity, entity_id = path.split("/", 1)
            return self._lookup(entity, entity_id)
        return 200, {}, json.dumps(self._search(path, params["query"], int(params.get("limit", 10)))).encode()

    def _lookup(self, entity, entity_id):
        if entity == "artist" and entity_id in self.artists:
            return 200, {}, json.dumps(self.artists[entity_id]).encode()
        if entity == "recording":
            rec = next((r for r in self.recordings if r["id"] == entity_id), None)
            if rec:
                return 200, {}, json.dumps(rec).encode()
        if entity == "release-group" and entity_id in self.release_groups:
            rg = dict(self.release_groups[entity_id])
            rg["releases"] = [{k: v for k, v in r.items() if k != "release-group"}
                              for r in self.releases if r["release-group"]["id"] == entity_id]
            rg["artist-credit"] = rg["releases"][0]["artist-credit"] if rg["releases"] else []
            return 200, {}, json.dumps(rg).encode()
        return 404, {}, b'{"error": "Not Found"}'

    @staticmethod
    def _terms(query):
        """'recording:"A \\"b\\"" AND arid:123' -> {'recording': 'A "b"', 'arid': '123'}"""
        terms = {}
        for key, quoted, bare in re.findall(r'(\w+):(?:"((?:[^"\\]|\\.)*)"|(\S+))', query):
            terms[key] = re.sub(r"\\(.)", r"\1", quoted) if quoted else bare
        return terms

    @staticmethod
    def _phrase_hits(phrase, text) -> int:
        want, have = fold(phrase).split(), fold(text).split()
        if not want or not all(w in have for w in want):
            return 0
        return 100 if want == have else 80

    def _search(self, entity, query, limit):
        terms = self._terms(query)
        if entity == "artist":
            hits = []
            for a in self.artists.values():
                names = [a["name"]] + [x["name"] for x in a["aliases"]]
                score = max(self._phrase_hits(terms["artist"], n) for n in names)
                if score:
                    hits.append({**a, "score": score})
            return {"count": len(hits), "offset": 0, "artists": hits[:limit]}

        if entity == "recording":
            hits = []
            for r in self.recordings:
                score = self._phrase_hits(terms["recording"], r["title"])
                credit = r["artist-credit"][0]
                if "arid" in terms and credit["artist"]["id"] != terms["arid"]:
                    continue
                if "artist" in terms:
                    a = self.artists[credit["artist"]["id"]]
                    if not any(self._phrase_hits(terms["artist"], n)
                               for n in [a["name"]] + [x["name"] for x in a["aliases"]]):
                        continue
                if score:
                    hits.append({**r, "score": score})
            hits.sort(key=lambda h: -h["score"])
            return {"count": len(hits), "offset": 0, "recordings": hits[:limit]}

        if entity == "release":
            hits = []
            for rel in self.releases:
                if rel["artist-credit"][0]["artist"]["id"] != terms.get("arid"):
                    continue
                score = self._phrase_hits(terms["release"], rel["title"].replace(" (Deluxe Edition)", ""))
                if score:
                    hits.append({**rel, "score": score})
            return {"count": len(hits), "offset": 0, "releases": hits[:limit]}
        raise AssertionError(f"unexpected search {entity}")
