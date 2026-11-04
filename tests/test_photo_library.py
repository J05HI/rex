#!/usr/bin/env python3
"""A source's pictures (`kind: photo`) are a photo library Plex Web browses, views and plays as a slideshow.

plexscratch's PMS 1.43.4 was given a photo library through Plex Web 4.160.0's Add Library (Photos)
on 2026-11-04 and walked by it: Recommended, Timeline and Library, an album and a sub-album, the
viewer forward and back, the album's slideshow and a picture at the library's top
(`fixtures/photos/web-photos.jsonl`, `probes/photo_capture_fixture.py`). This suite pushes the same
folders (`photo_rig.py`: stand-ins of the same names and sizes) to rex from a source that declares
its `photos` directory a photo library, replays every recorded request in order, and compares each
answer with plexscratch's as `harness/scripts/replay.py` does. The folder named like a comic book
(`Witch Test v01 (2019) (Digital).cbz`) is pushed where it was added on plexscratch, before Plex Web
opened it.

    python3 tests/test_photo_library.py [-v]
"""
import json
import re
import shutil
import sys

import music_rig as M
import photo_rig as P
from music_rig import check, results

VERBOSE = "-v" in sys.argv
# What differs by construction: the library's own id and what embeds it, keys inside paths, a
# slideshow's queue and the Timeline's moments (stamped when each server saw the files).
IGNORE = ("librarySectionID", "librarySectionKey", "composite", "fastKey", "hubKey", "hubIdentifier", "playQueueSourceURI",
          "startsAt", "endsAt", "key", "playQueueSelectedMetadataItemID",
          # Plex Web reads the queue while its timelines are still arriving: plexscratch named the
          # picture before the one on screen as often as that one.
          "playQueueSelectedItemID", "playQueueSelectedItemOffset",
          # A setting's help text is rex's own wording (CLAUDE.md, the sidecars).
          "Setting@summary")
# Not emulated: a picture open in a viewer is no session in Now Playing, and the Add Library
# dialog's own reads (agents, scanners, the folder picker) are answered by the ordinary routes.
SKIPPED = ("/status/sessions", "/system/", "/:/plugins/", "/library/sections/prefs", "/updater/", "/activities", "/identity",
           "/media/providers")


def by_stamp(record):
    """The Timeline's pictures and Recently Added are ordered by when each server saw each file,
    newest first: compared as sets, the order checked on rex's own stamps."""
    return record["path"].startswith("/hubs/sections/") or (record["path"].endswith("/all") and any(name == "clusterZoomLevel" for name, _ in record["query"]))


def sorted_rows(text):
    if not text or not text.startswith("{"):
        return text
    body = json.loads(text)
    container = body.get("MediaContainer", {})
    for holder in [container] + container.get("Hub", []):
        if "Metadata" in holder:
            holder["Metadata"].sort(key=lambda row: row.get("title", ""))
    return json.dumps(body)


def newest_first(text):
    body = json.loads(text)["MediaContainer"]
    rows = body.get("Metadata") or [row for hub in body.get("Hub", []) for row in hub.get("Metadata", [])]
    stamps = [row["addedAt"] for row in rows]
    return stamps == sorted(stamps, reverse=True)


def main():
    work, items, rex, handler = P.start()
    comics = [item for item in items if ".cbz/" in item["path"]]
    try:
        status, body = rex.push([item for item in items if item not in comics])
        check("a source's pictures are taken as photos", status == 200 and body.get("created") == len(items) - len(comics), body)
        status, body = rex.request("/library/sections")
        sections = [row for row in body["MediaContainer"]["Directory"] if row.get("type") == "photo"]
        check("its `photos` directory is a photo library", len(sections) == 1, body)
        if not sections:
            return finish()
        section = sections[0]["key"]
        created = next(record for record in P.load() if record["method"] == "POST" and record["path"] == "/library/sections")
        theirs = json.loads(next(r for r in P.load() if r["path"] == "/library/sections" and r["method"] == "GET")["res_body"])
        theirs = next(row for row in theirs["MediaContainer"]["Directory"] if row["type"] == "photo")
        # `allowSync` is a claimed server's (plexscratch is unclaimed and answers false for every library).
        differences = M.compare(json.dumps(theirs), json.dumps(sections[0]),
                                ignore=("scannedAt", "contentChangedAt", "updatedAt", "createdAt", "path", "refreshing", "key", "id", "allowSync"))
        check("listed as Plex Web's Add Library made it (agent, scanner, language, group)", not differences and created, differences)

        # Plex for Android and Plex Web find a server's libraries in `/media/providers`.
        status, body = rex.request("/media/providers")
        content = [d for p in body["MediaContainer"]["MediaProvider"] for f in p.get("Feature", []) if f.get("type") == "content"
                   for d in f.get("Directory", []) if d.get("type") == "photo"]
        recorded = json.loads(next(r for r in P.load() if r["path"] == "/media/providers" and r["status"] == 200)["res_body"])
        theirs = [d for p in recorded["MediaContainer"]["MediaProvider"] for f in p.get("Feature", []) if f.get("type") == "content"
                  for d in f.get("Directory", []) if d.get("type") == "photo"]
        differences = M.compare(json.dumps(theirs), json.dumps(content), ignore=("id", "key", "uuid", "updatedAt", "scannedAt", "refreshing"))
        check("`/media/providers` lists the photo library as plexscratch's", len(content) == 1 and not differences, (content, differences))

        mapping, part_map = P.key_map(rex, section)
        queues, queue_items = {}, {}
        pushed_comics = False
        failures = {}
        for record in P.load():
            if record["label"] == "addlib" or record["path"].startswith(SKIPPED) or record["path"] in ("/", "/library/sections"):
                continue
            if record["label"] == "web-cbzfolder" and not pushed_comics:
                status, body = rex.push(comics)
                pushed_comics = True
                check("a folder named like a comic book is an album of its pictures", body.get("created") == len(comics), body)
                mapping, part_map = P.key_map(rex, section)
            record = swap_queues(record, queues, queue_items)
            status, headers, out = P.send(rex, record, mapping, part_map, section)
            name = describe(record)
            if record["method"] == "POST" and record["path"] == "/playQueues" and status == 200:
                learn_queue(record, out, queues, queue_items)
            if "res_len" in record:
                kind = dict((k.lower(), v) for k, v in headers.items()).get("content-type", "")
                ok = status == record["status"] and kind.startswith("image/") and len(out or b"") > 0
                if not ok:
                    failures.setdefault(name, (status, kind, dict(record["query"]).get("url")))
                continue
            reference = record.get("res_body") or ""
            if by_stamp(record):
                reference, mine = sorted_rows(reference), sorted_rows(out.decode() if isinstance(out, bytes) else json.dumps(out))
                if status == 200 and not newest_first(mine):
                    failures.setdefault(name + " (order)", (status, "not newest first"))
            else:
                mine = out
            differences = M.compare(reference, mine, ignore=IGNORE)
            if status != record["status"] or differences:
                failures.setdefault(name, (status, differences[:8]))
        for name in BEHAVIOURS:
            seen = [key for key in failures if key.startswith(name)]
            check("Plex Web's %s answers as plexscratch's" % name, not seen, {key: failures[key] for key in seen})
        unexpected = [key for key in failures if not key.startswith(tuple(BEHAVIOURS))]
        check("nothing else Plex Web asked differs", not unexpected, {key: failures[key] for key in unexpected})
        if VERBOSE:
            for key, value in failures.items():
                print("   ", key, value)
    finally:
        rex.stop()
        shutil.rmtree(work, ignore_errors=True)
    return finish()


def finish():
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


# The behaviours the walk exercises, by the request that shows each.
BEHAVIOURS = {
    "GET /library/sections/{s}/all": "the library's top: albums and pictures in title order",
    "GET /library/sections/{s}/cluster": "the Timeline's moments",
    "GET /library/sections/{s}/prefs": "the library's settings",
    "GET /hubs/sections/{s}": "Recommended: recently added pictures",
    "GET /library/metadata/{k}/children": "an album's albums and pictures, page 2 before page 10",
    "GET /library/metadata/{k}": "an album's or picture's detail",
    "GET /hubs/metadata/{k}/related": "an album's related shelf",
    "POST /playQueues": "a slideshow of an album, from a picture or the album, or of the library",
    "GET /playQueues/{q}": "the slideshow's queue following the viewer",
    "GET /:/timeline": "the viewer's timeline",
    "GET /photo/:/transcode": "tiles and the viewer's picture",
}


def describe(record):
    path = re.sub(r"/library/sections/\d+", "/library/sections/{s}", record["path"])
    path = re.sub(r"/hubs/sections/\d+", "/hubs/sections/{s}", path)
    path = re.sub(r"/(metadata)/\d+", r"/\1/{k}", path)
    path = re.sub(r"/playQueues/\d+", "/playQueues/{q}", path)
    return "%s %s" % (record["method"], path)


def learn_queue(record, out, queues, queue_items):
    """plexscratch's queue and item ids to rex's, by position, from a slideshow's creation."""
    theirs = json.loads(record["res_body"])["MediaContainer"]
    ours = json.loads(out)["MediaContainer"]
    queues[str(theirs["playQueueID"])] = str(ours["playQueueID"])
    for mine, their in zip(ours.get("Metadata", []), theirs.get("Metadata", [])):
        queue_items[str(their["playQueueItemID"])] = str(mine["playQueueItemID"])


def swap_queues(record, queues, queue_items):
    record = dict(record)
    record["path"] = re.sub(r"^/playQueues/(\d+)", lambda m: "/playQueues/" + queues.get(m.group(1), m.group(1)), record["path"])
    record["query"] = [[name, queue_items.get(value, value) if name == "playQueueItemID" else value] for name, value in record["query"]]
    body = record.get("res_body")
    if body and body.startswith("{") and "playQueueItemID" in body:
        # Compared on rex's ids: plexscratch's item ids swapped where rex has them.
        record["res_body"] = re.sub(r'"(playQueueItemID|playQueueSelectedItemID)":(\d+)',
                                    lambda m: '"%s":%s' % (m.group(1), queue_items.get(m.group(2), m.group(2))), body)
    return record


if __name__ == "__main__":
    if not (shutil.which("ffmpeg") and shutil.which("ffprobe")):
        print("SKIP: needs ffmpeg and ffprobe to make the stand-in pictures")
        sys.exit(0)
    sys.exit(main())
