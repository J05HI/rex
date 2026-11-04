#!/usr/bin/env python3
"""Record what a real PMS shows of what Plex's agent says about a title: theme music, clear
logos, square art, the whole cast and a film's producers, read through the clients' own requests.

Run on the lab host against plexlab (its harness clips: Big Buck Bunny 1, Firefly 6, its Season 1
7 and The Train Job 8), which an unclaimed PMS answers on loopback with no token:

    python3 probes/record_agent_description.py http://127.0.0.1:32400 > agent_description.json

JSON answers are kept whole (they carry no credential: no token is sent); any other body only as
its length and SHA-256, never Plex's artwork or audio bytes. The headers kept are those an
artwork read is measured by.
"""
import hashlib
import json
import sys
import urllib.error
import urllib.request

# Plex Web 4.160's detail, grid and children requests (tests/fixtures/reference-walk.jsonl), and
# Plex for Android's detail request (tests/fixtures/music/droid-music-browse.jsonl).
WEB = ("includeConcerts=1&includeExtras=1&includeOnDeck=1&includePopularLeaves=1&includePreferences=1"
       "&includeReviews=1&includeChapters=1&includeStations=1&includeExternalMedia=1&asyncAugmentMetadata=1"
       "&asyncCheckFiles=1&asyncRefreshAnalysis=1&asyncRefreshLocalMediaAgent=1")
DROID = "includeExtras=1&includeOnDeck=1&includeRelated=1&includeRelatedCount=15&includeReviews=1&includeExternalMetadata=1"
GRID = "includeCollections=1&includeExternalMedia=1&includeAdvanced=1&includeMeta=1&X-Plex-Container-Start=0&X-Plex-Container-Size=50"
CHILDREN = "excludeAllLeaves=1&X-Plex-Container-Start=0&X-Plex-Container-Size=20"
KEPT = ("content-type", "content-range", "content-length", "cache-control", "accept-ranges", "x-plex-local-asset")

REQUESTS = [
    ("detail", "GET", "/library/metadata/6?" + WEB, {}),
    ("detail", "GET", "/library/metadata/7?" + WEB, {}),
    ("detail", "GET", "/library/metadata/8?" + WEB, {}),
    ("detail", "GET", "/library/metadata/1?" + WEB, {}),
    ("detail", "GET", "/library/metadata/8?" + DROID, {}),
    ("detail", "GET", "/library/metadata/1?" + DROID, {}),
    ("listing", "GET", "/library/sections/2/all?" + GRID, {}),
    ("listing", "GET", "/library/sections/1/all?" + GRID, {}),
    ("listing", "GET", "/library/metadata/6/children?" + CHILDREN, {}),
    ("listing", "GET", "/library/metadata/7/children?" + CHILDREN, {}),
    ("listing", "GET", "/library/sections/2/network", {}),
    ("listing", "GET", "/library/metadata/6/squareArts", {}),
    ("listing", "GET", "/library/metadata/1/squareArts", {}),
    ("listing", "GET", "/library/metadata/7/squareArts", {}),
    # Plex Web's theme read is an <audio> element's: `Range: bytes=0-`.
    ("bytes", "GET", "/library/metadata/6/theme/{show}", {"Range": "bytes=0-"}),
    ("bytes", "GET", "/library/metadata/6/theme/{show}", {}),
    ("bytes", "HEAD", "/library/metadata/6/theme/{show}", {}),
    ("bytes", "GET", "/library/metadata/6/theme", {}),
    ("bytes", "GET", "/library/metadata/6/clearLogo/{show}", {}),
    ("bytes", "GET", "/library/metadata/6/squareArt/{show}", {}),
    ("bytes", "GET", "/library/metadata/1/clearLogo/{movie}", {}),
    ("bytes", "GET", "/library/metadata/1/clearLogo/{movie}", {"Range": "bytes=0-99"}),
    ("bytes", "GET", "/library/metadata/1/squareArt/{movie}", {}),
    ("bytes", "GET", "/library/metadata/1/squareArt", {}),
    # A season borrowing its show's, and a film with none of its own.
    ("bytes", "GET", "/library/metadata/7/theme/{show}", {}),
    ("bytes", "GET", "/library/metadata/7/clearLogo/{show}", {}),
    ("bytes", "GET", "/library/metadata/7/squareArt/{show}", {}),
    ("bytes", "GET", "/library/metadata/1/theme/{movie}", {}),
]


def fetch(base, method, path, headers):
    request = urllib.request.Request(base + path, method=method, headers=dict({"Accept": "application/json"}, **headers))
    try:
        response = urllib.request.urlopen(request, timeout=30)
        return response.status, response.headers, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers, error.read()


def main():
    base = sys.argv[1].rstrip("/")
    _, _, body = fetch(base, "GET", "/library/metadata/6", {})
    show = json.loads(body)["MediaContainer"]["Metadata"][0]["theme"].rsplit("/", 1)[1]
    _, _, body = fetch(base, "GET", "/library/metadata/1", {})
    movie = json.loads(body)["MediaContainer"]["Metadata"][0]["thumb"].rsplit("/", 1)[1]
    exchanges = []
    for kind, method, path, headers in REQUESTS:
        path = path.format(show=show, movie=movie)
        status, answer_headers, body = fetch(base, method, path, headers)
        entry = {"kind": kind, "method": method, "path": path, "headers": headers, "status": status,
                 "response_headers": [[name, value] for name, value in answer_headers.items() if name.lower() in KEPT]}
        if (answer_headers.get("Content-Type") or "").startswith("application/json"):
            entry["body"] = json.loads(body)
        else:
            entry["length"] = len(body)
            entry["sha256"] = hashlib.sha256(body).hexdigest()
        exchanges.append(entry)
    json.dump({"reference": "plexlab, PMS 1.43.4.10903 (unclaimed since 2026-10-22; its library matched while claimed)",
               "versions": {"show": show, "movie": movie}, "exchanges": exchanges}, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
