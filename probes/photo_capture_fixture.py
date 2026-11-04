#!/usr/bin/env python3
"""Turn a CDP capture of Plex Web on a photo library into `tests/fixtures/photos/web-photos.jsonl`.

The capture (one JSON object per exchange: method, url, req_headers, status, resp_headers, body or
body_len/body_sha256 for a picture, label) was taken on plexscratch (PMS 1.43.4) on 2026-11-04 by
holding a tab of a Chrome open over CDP, its plex.tv resource list rewritten to the one scratch
server, while Plex Web 4.160.0 (served by that PMS) walked Add Library (Photos), the Recommended,
Timeline and Library tabs, an album, a sub-album, the photo viewer (forward, forward, back), the
album's slideshow and a photo at the library's top. The pictures were Pepper&Carrot pages
(David Revoy, CC BY 4.0, peppercarrot.com); no picture's bytes are kept, only their length and hash.

Kept: every exchange with the server but Plex Web's own files (`/web/...`), the sign-in's and the
server-wide ones no photo answer depends on (`OTHER`), one per distinct request in capture order
(Chrome reported some twice). Removed: the server's token wherever it
rides (the query, the headers and the `url=` a `/photo/:/transcode` nests).

    python3 probes/photo_capture_fixture.py CAPTURE.jsonl tests/fixtures/photos/web-photos.jsonl
"""
import json
import re
import sys
import urllib.parse


# Not about photos: the sign-in, a probe of the recorder, the container's file system as Add
# Library's folder picker browses it, and server-wide settings and accounts.
OTHER_LABELS = {"signin", "dbg"}
OTHER_PATHS = ("/services/browse", "/myplex/account", "/accounts/", "/:/prefs")


def scrub(text):
    return re.sub(r"(X-Plex-Token(?:=|%3D|\"\s*:\s*\"))[A-Za-z0-9_\-]{10,}", r"\1REDACTED", text or "")


def main(capture, out):
    seen, kept = set(), []
    for line in open(capture):
        record = json.loads(line)
        parts = urllib.parse.urlsplit(record["url"])
        if parts.path.startswith(("/web/",) + OTHER_PATHS) or "status" not in record or record.get("label") in OTHER_LABELS:
            continue
        query = [[name, "REDACTED" if name == "X-Plex-Token" else scrub(value)]
                 for name, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)]
        request = (record["method"], parts.path, tuple(tuple(pair) for pair in query if pair[0] != "X-Plex-Token"))
        if request in seen:
            continue
        seen.add(request)
        entry = {
            "label": record.get("label"),
            "method": record["method"],
            "path": parts.path,
            "query": query,
            "req_headers": [[name, "REDACTED" if name.lower() == "x-plex-token" else value]
                            for name, value in (record.get("req_headers") or {}).items()],
            "status": record["status"],
            "res_headers": [[name, value] for name, value in (record.get("resp_headers") or {}).items()],
        }
        if "body_len" in record:
            entry.update(res_len=record["body_len"], res_sha256=record["body_sha256"])
        else:
            entry["res_body"] = scrub(record.get("body"))
        kept.append(entry)
    with open(out, "w") as fh:
        for entry in kept:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print("%d exchanges -> %s" % (len(kept), out))


if __name__ == "__main__":
    main(*sys.argv[1:3])
