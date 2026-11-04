#!/usr/bin/env python3
"""Record plexav's music filter values as Plezy reads them.

Plezy (38c48f14, `fetchLibraryFiltersWithValues`) takes a music library's filters from
`/library/sections/{id}/all?includeMeta=1&type=` and, when one is opened, fetches its `key`
(`getFilterValues`): `/library/sections/1/mood?type=9` and the like. This asks plexav for every tag
axis its music library's Meta names -- mood, style, format, subformat and source, each per type --
and keeps the answers whole.

    python3 probes/record_music_axes.py --base http://127.0.0.1:32420 --token-command "<prints the token>"
"""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "tests", "fixtures", "music", "axes.json")
LEAVES = ("mood", "style", "format", "subformat", "source")
HEADERS = {"Accept": "application/json", "X-Plex-Product": "Plezy", "X-Plex-Version": "1.20.0",
           "X-Plex-Client-Identifier": "rexprobe-plezy", "X-Plex-Platform": "Linux"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--token-command", required=True)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    token = subprocess.run(args.token_command, shell=True, capture_output=True, text=True).stdout.strip()
    if not token:
        sys.exit("no token from --token-command")

    def get(path):
        request = urllib.request.Request(args.base + path, headers=dict(HEADERS, **{"X-Plex-Token": token}))
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode()

    keys = []
    for kind in ("8", "9", "10"):
        _, body = get("/library/sections/1/all?includeMeta=1&X-Plex-Container-Start=0&X-Plex-Container-Size=0&type=" + kind)
        for meta in json.loads(body)["MediaContainer"]["Meta"]["Type"]:
            for entry in meta.get("Filter") or []:
                leaf = re.match(r"/library/sections/1/(\w+)\?", entry["key"])
                if leaf and leaf.group(1) in LEAVES and entry["key"] not in keys:
                    keys.append(entry["key"])
    out = {"recorded": "plexav PMS 1.43.4, 2026-11-04", "client": "Plezy 38c48f14 getFilterValues", "exchanges": []}
    for key in keys:
        status, body = get(key)
        out["exchanges"].append({"path": key, "status": status, "body": body.replace(token, "REDACTED")})
    json.dump(out, open(args.out, "w"), indent=1)
    print("%d exchanges -> %s" % (len(out["exchanges"]), args.out))


if __name__ == "__main__":
    sys.exit(main())
