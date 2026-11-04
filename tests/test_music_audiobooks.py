#!/usr/bin/env python3
"""Audiobooks in a music library, and every audio file a real PMS makes a track of.

Plex has no audiobook library: audiobook players (Chronicle, Prologue, Plexamp's audiobook view) read a
music library whose albums are books. `fixtures/music/chronicle.jsonl` (`probes/record_audiobooks.py`)
is plexav's PMS 1.43.4 answering, in order, every request Chronicle makes for a library sync and a
play -- its paths, parameters and headers, from its source -- for a music library holding one LibriVox
recording twice: the chaptered `.m4b` LibriVox publishes (one track, six chapters) and its six
per-chapter `.mp3` files; then Plex for Android's, Plexamp's and Plex Web's own play-queue and detail
requests, as plexav recorded them, pointed at the `.m4b`. `fixtures/music/audiobook-probes.json` holds
each of those files' ffprobe (what a source pushes) and `fixtures/music/audio-formats.json` one small
file per audio extension with plexav's track for it, `null` where PMS made none (`.tak`).

This pushes the same files as tracks with the recorded probes, named by `display` as their tags name
them (no agent knows a LibriVox recording: plexav's rows are `local://`), serves stand-in bytes of
each file's size, and replays the recording against rex.

    python3 tests/test_music_audiobooks.py
"""
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

import music_rig as M
from music_rig import check, results

RECORDED = [json.loads(line) for line in open(os.path.join(M.FIXTURES, "chronicle.jsonl"))]
BOOK = json.load(open(os.path.join(M.FIXTURES, "audiobook-probes.json")))
FORMATS = json.load(open(os.path.join(M.FIXTURES, "audio-formats.json")))
SECTIONS = [{"key": "4", "type": "artist", "title": "Audiobooks", "agent": "tv.plex.agents.music", "scanner": "Plex Music",
             "language": "en-US", "uuid": "0000000000000000000000000000000000000a04",
             "Location": [{"id": 4, "path": "/data/audiobooks"}]},
            {"key": "5", "type": "artist", "title": "Formats", "agent": "tv.plex.agents.music", "scanner": "Plex Music",
             "language": "en-US", "uuid": "0000000000000000000000000000000000000a05",
             "Location": [{"id": 5, "path": "/data/formats"}]}]
# What rex has no part of: PMS's own analysis (loudness is a source's to push, `loudness`), the cover
# it read out of the file, and its section's uuid.
# `filter` names a chapter picture rex never makes (`mediainfo::build_chapters`).
IGNORE = ("filter", "albumGain", "albumPeak", "albumRange", "gain", "loudness", "lra", "peak", "loudnessAnalysisVersion",
          "Image", "UltraBlurColors", "Genre", "hasThumbnail", "streamIdentifier", "librarySectionUUID",
          "originallyAvailableAt", "year", "parentYear", "Extras", "art", "title1", "title2")


def steps(name):
    return [record for record in RECORDED if record["step"] == name]


def body(record):
    return json.loads(record["res_body"])["MediaContainer"]


def item(entry):
    """A file as its source pushes it: its probe, its tags as `display`, never its name."""
    relative = entry["file"][len("/data/"):]
    tags = {k.lower(): v for k, v in (entry["probe"].get("format", {}).get("tags") or {}).items()}
    pushed = {"id": relative, "path": relative, "kind": "track", "release": relative.rsplit("/", 1)[0],
              "media_info": entry["probe"], "size": entry["size"]}
    if tags.get("track"):
        pushed["track"] = int(tags["track"].split("/")[0])
    display = {key: value for key, value in (("title", tags.get("title")),) if value}
    if tags.get("artist"):
        display["artist"] = {"title": tags["artist"]}
    if tags.get("album"):
        display["album"] = {"title": tags["album"]}
    if display:
        pushed["display"] = display
    return pushed


def stand_ins(root, entries):
    for entry in entries:
        path = os.path.join(root, entry["file"][len("/data/"):])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.truncate(entry["size"])


def main():
    work = tempfile.mkdtemp(prefix="rex-audiobooks-")
    media = os.path.join(work, "media")
    stand_ins(media, BOOK + FORMATS)
    bytes_port, _ = M.range_server(media)
    agent_url, _ = M.agent_server([])
    rex = M.Rex(work, bytes_port, agent_url, extra_sections=SECTIONS, music=False)
    try:
        formats(rex)
        chronicle(rex)
    finally:
        rex.stop()
        shutil.rmtree(work, ignore_errors=True)
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


def formats(rex):
    """Every extension plexav's music scanner made a track of is a track; one it skipped is not."""
    status, answer = rex.push([item(entry) for entry in FORMATS])
    rejected = {entry["id"].rsplit(".", 1)[-1]: entry["reason"] for entry in answer.get("rejected", [])}
    made = sorted(entry["file"].rsplit(".", 1)[-1] for entry in FORMATS if entry["track"])
    skipped = sorted(entry["file"].rsplit(".", 1)[-1] for entry in FORMATS if not entry["track"])
    check("every extension plexav makes a track of is taken (%s)" % " ".join(made),
          not [ext for ext in made if ext in rejected], {ext: rejected[ext] for ext in made if ext in rejected})
    check("one it makes none of is refused (%s)" % " ".join(skipped), all(ext in rejected for ext in skipped), rejected)
    rows = {row["Media"][0]["Part"][0]["file"]: row for row in rex.rows("/library/sections/5/all?type=10")}
    for entry in FORMATS:
        if not entry["track"]:
            continue
        theirs, ours = entry["track"]["Media"][0], (rows.get(entry["file"]) or {}).get("Media", [{}])[0]
        ext = entry["file"].rsplit(".", 1)[-1]
        # plexav's analysis read no stream of its `.dts` (FFmpeg's own DTS encoder's), so it named no codec.
        wanted = {name: theirs.get(name) for name in ("container", "audioCodec") if theirs.get(name)}
        got = {name: ours.get(name) for name in wanted}
        part = (ours.get("Part") or [{}])[0].get("key", "")
        check(".%s is a track whose Media is plexav's (%s) and whose Part key keeps its extension" % (ext, wanted),
              got == wanted and part.endswith("/file." + ext), (got, part))
        status, headers, _ = rex.request(part, headers={"Range": entry["read"]["range"]}, raw=True) if part else (0, {}, b"")
        check(".%s's Part is read as plexav reads it: %s %s" % (ext, entry["read"]["status"], entry["read"]["contentType"]),
              (status, headers.get("Content-Type")) == (entry["read"]["status"], entry["read"]["contentType"]),
              (status, headers.get("Content-Type")))


def chronicle(rex):
    # In plexav's scan order (its keys): the .mp3 album first, then the .m4b.
    status, answer = rex.push([item(entry) for entry in sorted(BOOK, key=lambda entry: entry["file"].endswith(".m4b"))])
    check("the LibriVox book is pushed: the .m4b and its six .mp3 chapters", answer.get("created") == 7, answer)
    ours = {row["Media"][0]["Part"][0]["file"]: row for row in rex.rows("/library/sections/4/all?type=10")}
    theirs = {row["Media"][0]["Part"][0]["file"]: row for row in body(steps("tracks")[0])["Metadata"]}
    mapping, parts = {}, {}
    for file, row in theirs.items():
        mine = ours.get(file)
        if not mine:
            continue
        for field in ("ratingKey", "parentRatingKey", "grandparentRatingKey"):
            mapping[str(row[field])] = str(mine[field])
        parts[str(row["Media"][0]["Part"][0]["id"])] = mine["Media"][0]["Part"][0]["key"]
    book = next(file for file in theirs if file.endswith(".m4b"))

    def send(record):
        path = M.rewrite(record["path"], mapping)
        part = next((key for theirs_id, key in parts.items() if record["path"].startswith("/library/parts/%s/" % theirs_id)), None)
        if part:
            path = part
        query = [(name, M.rewrite(value, mapping)) for name, value in record["query"]]
        query = [(name, value if name not in ("key", "ratingKey") else mapping.get(value, value)) for name, value in query]
        headers = {name: value for name, value in record["req_headers"] if name != "X-Plex-Token"}
        url = path + ("?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote) if query else "")
        return rex.request(url, record["method"], None, headers, raw=True)

    def same(record, name, ignore=()):
        status, headers, raw = send(record)
        reference = M.rewrite(record["res_body"], mapping)
        if status == 200 and raw[:1] == b"{":
            # Rows in plexav's order: two releases titled alike are listed by when each was made,
            # and plexav scanned the .mp3 folder first where rex takes a push in path order.
            ours = json.loads(raw)
            order = [mapping.get(row.get("ratingKey")) for row in json.loads(reference)["MediaContainer"].get("Metadata") or []]
            rows = ours["MediaContainer"].get("Metadata")
            if rows and sorted(order) == sorted(row.get("ratingKey") for row in rows):
                rows.sort(key=lambda row: order.index(row.get("ratingKey")))
            raw = json.dumps(ours).encode()
        differences = M.compare(reference, raw, ignore=IGNORE + tuple(ignore)) if status == 200 else []
        return check(name, status == record["status"] and not differences, (status, differences[:12]))

    if not check("each of plexav's tracks has a row here", all(file in ours for file in theirs), sorted(ours)):
        return
    # The sync: what Chronicle lists and reads of each book.
    same(steps("albums")[0], "albums listed as plexav lists them for Chronicle (type=9, paged)")
    same(steps("tracks")[0], "tracks listed as plexav lists them for Chronicle (type=10, paged)")
    same(steps("collections")[0], "an audiobook library's collections, none, as plexav answers them")
    for record in steps("album"):
        same(record, "a book's detail with includeChapters=1 (%s)" % record["path"])
    for record in steps("album-children"):
        same(record, "a book's tracks (%s/children)" % record["path"].rsplit("/", 2)[-2])
    for record in steps("track-chapters"):
        file = next((f for f, row in theirs.items() if record["path"].endswith("/" + row["ratingKey"])), "")
        same(record, "a track's detail with includeChapters=1: %s" % file.rsplit("/", 1)[-1])
    chapters = body(steps("track-chapters")[-1])["Metadata"][0]["Chapter"]
    status, headers, raw = send(steps("track-chapters")[-1])
    mine = json.loads(raw)["MediaContainer"]["Metadata"][0].get("Chapter", []) if status == 200 else []
    check("the .m4b's six chapters: plexav's titles, indexes and offsets in ms",
          [(c["tag"], c["index"], c["startTimeOffset"], c["endTimeOffset"]) for c in mine]
          == [(c["tag"], c["index"], c["startTimeOffset"], c["endTimeOffset"]) for c in chapters],
          [(c.get("tag"), c.get("index"), c.get("startTimeOffset"), c.get("endTimeOffset")) for c in mine])
    same(steps("track-plain")[0], "without includeChapters its detail names the source (chapterSource) and no Chapter")
    # The play: the decision plexav refuses (Chronicle then plays the listing's Part), the Part reads.
    for record in steps("decision"):
        status, headers, raw = send(record)
        check("Chronicle's decision (hasMDE with directPlay and no media index) is plexav's 400", status == 400, (status, raw[:200]))
    for record in steps("part-read") + steps("part-seek") + steps("part-download"):
        status, headers, raw = send(record)
        # plexav serves an MP3 without its ID3v2 tag (this one's 1,833 bytes, which its Part's `size`
        # still counts); rex serves the source's bytes as they are, so an MP3's range is not compared.
        names = ("Content-Type", "Content-Disposition") if record["path"].endswith(".mp3") else \
            ("Content-Type", "Content-Range", "Content-Disposition")
        wanted = {name: value for name, value in record["res_headers"] if name in names}
        got = {name: headers.get(name) for name in wanted}
        check("a Part read %s %s: %s as plexav answers it %s" % (record["path"].rsplit("/", 1)[-1], dict(record["query"]) or
                                                               dict(record["req_headers"]).get("Range"), record["status"], wanted),
              status == record["status"] and got == wanted, (status, got))
    status, headers, raw = send(steps("playqueue")[0])
    check("Chronicle's play queue (repeat=false) is plexav's 400", status == 400, (status, raw[:200]))
    # Progress: kept on the track only with the library's "Store track progress" on.
    for name in ("pref-default", "timeline-off-playing", "timeline-off-paused"):
        status, headers, raw = send(steps(name)[0])
        if name.startswith("timeline"):
            # With the setting off plexav answers the paused beat's 95000 and shows it on no row;
            # rex keeps no position there at all, so it answers -1 (no position) for it.
            reference = steps(name)[0]["res_body"].replace('"viewOffset":95000', '"viewOffset":-1')
            check("%s answers plexav's %s" % (name, reference),
                  status == 200 and not M.compare(reference, raw), raw[:200])
    for name in ("off-track", "off-album", "off-children", "off-albums"):
        same(steps(name)[0], "with Store track progress off, a paused timeline keeps no position (%s)" % name)
    status, headers, raw = send(steps("off-track")[0])
    check("with it off the track has no viewOffset, as plexav's has none",
          status == 200 and "viewOffset" not in json.loads(raw)["MediaContainer"]["Metadata"][0], raw[:300])
    send(steps("pref-on")[0])
    for name in ("timeline-on-playing", "timeline-on-paused"):
        status, headers, raw = send(steps(name)[0])
        check("%s answers plexav's %s" % (name, steps(name)[0]["res_body"]),
              status == 200 and not M.compare(steps(name)[0]["res_body"], raw), raw[:200])
    for name in ("on-track", "on-album", "on-children", "on-albums"):
        same(steps(name)[0], "with it on, the track keeps its position, the book none (%s)" % name)
    status, headers, raw = send(steps("on-track")[0])
    check("with it on the track resumes at plexav's 95000",
          status == 200 and json.loads(raw)["MediaContainer"]["Metadata"][0].get("viewOffset") == 95000, raw[:300])
    send(steps("timeline-on-stopped")[0])
    for name in ("stopped-track", "stopped-children"):
        same(steps(name)[0], "a stop keeps the stopped position (%s)" % name)
    # The other clients' play queues and detail, chapters included where they ask for them.
    for name in ("droid-playqueue", "plexamp-playqueue", "web-playqueue", "web-track"):
        same(steps(name)[0], "%s answers the .m4b as plexav does, chapters where asked" % name,
             ignore=("playQueueLastAddedItemID", "playQueueVersion", "Stream", "Stations"))
    # Played to its end: Chronicle scrobbles the track, then the book; and takes both back.
    send(steps("scrobble-track")[0])
    for name in ("scrobbled-track", "scrobbled-album", "scrobbled-children"):
        same(steps(name)[0], "a scrobbled track is played, its book counts it (%s)" % name)
    send(steps("scrobble-album")[0])
    same(steps("scrobbled-mp3-children")[0], "a scrobbled book marks every track played")
    send(steps("unscrobble-album")[0])
    send(steps("unscrobble-track")[0])
    for name in ("unscrobbled-track", "unscrobbled-children"):
        same(steps(name)[0], "unscrobbled, the track is unplayed again (%s)" % name)


if __name__ == "__main__":
    sys.exit(main())
