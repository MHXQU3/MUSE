"""A small, polite MusicBrainz client.

* At most one request per second (MusicBrainz blocks clients that go faster).
* Identifies itself with a User-Agent that includes your contact details,
  which MusicBrainz requires.
* Caches every response in a local SQLite file, so re-running enrichment,
  or resuming after a stop, costs no extra requests.
* Retries politely on 503 / 429 / network errors.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

from . import __version__

BASE_URL = "https://musicbrainz.org/ws/2/"
RETRY_STATUSES = {429, 500, 502, 503, 504}

# transport(url, headers) -> (status, response headers, body bytes)
Transport = Callable[[str, dict], tuple[int, dict, bytes]]


def urllib_transport(url: str, headers: dict) -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read() or b""


def lucene_phrase(text: str) -> str:
    """Quote text for a Lucene query: "..." with \\ and " escaped."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MusicBrainzError(RuntimeError):
    pass


class MusicBrainzClient:
    def __init__(self, contact: str, cache_path: str | Path | None = None, *,
                 min_interval: float = 1.1, transport: Transport | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 max_retries: int = 5, cache_days: float = 90):
        self._db = None
        contact = (contact or "").strip()
        if not contact:
            raise ValueError("MusicBrainz requires contact details in every request. "
                             "Pass --contact (an email address or a URL) or set MUSE_CONTACT.")
        self.user_agent = f"MUSE/{__version__} ( {contact} )"
        self.min_interval = min_interval
        self.transport = transport or urllib_transport
        self.clock, self.sleep = clock, sleep
        self.max_retries = max_retries
        self.cache_seconds = cache_days * 86400
        self._last_request = -1e9
        self.requests_made = 0
        self.cache_hits = 0
        if cache_path:
            self._db = sqlite3.connect(str(cache_path))
            self._db.execute("create table if not exists responses ("
                             "url text primary key, status integer, body text, fetched_at real)")

    def close(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    def __del__(self):
        self.close()

    # -- public API ----------------------------------------------------------

    def search(self, entity: str, query: str, limit: int = 10) -> dict:
        return self._get(f"{entity}/", {"query": query, "limit": str(limit), "fmt": "json"}) or {}

    def lookup(self, entity: str, mbid: str, inc: tuple[str, ...] = ()) -> dict | None:
        return self._get(f"{entity}/{mbid}", {"fmt": "json"}, inc)

    # -- internals -----------------------------------------------------------

    def _get(self, path: str, params: dict, inc: tuple[str, ...] = ()) -> dict | None:
        # Normal values are fully encoded (a "+" in a title must not become a
        # space); only inc= uses literal "+" separators, as MusicBrainz expects.
        url = BASE_URL + path + "?" + urllib.parse.urlencode(sorted(params.items()))
        if inc:
            url += "&inc=" + "+".join(urllib.parse.quote(i) for i in inc)
        cached = self._cache_get(url)
        if cached is not None:
            self.cache_hits += 1
            status, body = cached
            return json.loads(body) if status == 200 else None

        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                status, resp_headers, body = self.transport(url, headers)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                status, resp_headers, body = -1, {}, str(exc).encode()
            self.requests_made += 1

            if status == 200:
                self._cache_put(url, 200, body.decode("utf-8"))
                return json.loads(body)
            if status == 404:
                self._cache_put(url, 404, "")
                return None
            if status in RETRY_STATUSES or status == -1:
                if attempt == self.max_retries:
                    break
                retry_after = _retry_after(resp_headers)
                self.sleep(max(retry_after or 0, 2 ** attempt))
                continue
            raise MusicBrainzError(f"MusicBrainz returned {status} for {url}: {body[:200]!r}")
        raise MusicBrainzError(f"MusicBrainz kept failing for {url} (last status {status}). "
                               "It may be busy; try again later.")

    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._last_request = self.clock()

    def _cache_get(self, url: str):
        if not self._db:
            return None
        row = self._db.execute("select status, body, fetched_at from responses where url = ?",
                               (url,)).fetchone()
        if not row or time.time() - row[2] > self.cache_seconds:
            return None
        return row[0], row[1]

    def _cache_put(self, url: str, status: int, body: str) -> None:
        if self._db:
            self._db.execute("insert or replace into responses values (?, ?, ?, ?)",
                             (url, status, body, time.time()))
            self._db.commit()


def _retry_after(headers: dict) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
    return None
