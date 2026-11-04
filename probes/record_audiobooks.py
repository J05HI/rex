#!/usr/bin/env python3
"""Record what a real PMS answers an audiobook player for a music library of audiobooks.

plexav's `Audiobooks` section (a music library, Plex Music agent, as audiobooks are kept in Plex) holds
one LibriVox recording twice: as the chaptered `.m4b` LibriVox publishes (one track, six QuickTime
chapters) and as its six per-chapter `.mp3` files. This replays, in its order, every request
Chronicle (`local.oss.chronicle`, github.com/fabiogermann/chronicle, `PlexService.kt` and
`PlexInterceptor.kt` at 747e68e9) makes for a library sync and a play -- its exact paths, query
parameters in their order and its `X-Plex-*` headers -- then the play queues and detail requests
Plex for Android, Plex Web and Plexamp recorded on plexav made (`tests/fixtures/music/*.jsonl`),
pointed at the `.m4b`, and keeps every exchange as a harness capture line, a Part read by length.

Progress is recorded with the section's "Store track progress" (`enableTrackOffsets`) as PMS
defaults it, off, and then on. Writes: the timelines and scrobbles below and that one section pref,
which this leaves on (an audiobook player needs it); `--restore` puts the pref back to off.

    python3 probes/record_audiobooks.py --base http://127.0.0.1:32420 --token-file tok --section 4 --out chronicle.jsonl
"""
import argparse
import http.client
import json
import os
import time
import urllib.parse
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
MUSIC = os.path.join(os.path.dirname(HERE), "tests", "fixtures", "music")
PROFILE = ("add-direct-play-profile(type=musicProfile&container=mp4,m4a,m4b,mp3,flac,ogg,opus"
           "&audioCodec=aac,mp3,flac,vorbis,opus&videoCodec=*&subtitleCodec=*)")
# PlexInterceptor's headers, in its order; the client id and session are this recorder's.
CHRONICLE = [("Accept", "application/json"), ("X-Plex-Platform", "Android"), ("X-Plex-Provides", "player"),
             ("X-Plex-Client-Identifier", "rexprobe-chronicle-0001"), ("X-Plex-Version", "0.62.10"),
             ("X-Plex-Product", "Chronicle"), ("X-Plex-Platform-Version", "16"),
             ("X-Plex-Session-Identifier", "rexprobe-session-0001"), ("X-Plex-Client-Name", "Chronicle"),
             ("X-Plex-Device", "Chronicle Android"), ("X-Plex-Device-Name", "Pixel 6"),
             ("X-Plex-Client-Profile-Extra", PROFILE)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--section", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--restore", action="store_true")
    args = parser.parse_args()
    token = open(args.token_file).read().strip()
    base = urllib.parse.urlparse(args.base)
    out = open(args.out, "w")

    def call(step, method, path, query=(), headers=CHRONICLE, media=False):
        url = path + ("?" + urllib.parse.urlencode(list(query), quote_via=urllib.parse.quote) if query else "")
        connection = http.client.HTTPConnection(base.hostname, base.port, timeout=120)
        sent = list(headers) + [("X-Plex-Token", token)]
        connection.request(method, url, headers=dict(sent))
        response = connection.getresponse()
        body = response.read() if method != "HEAD" else b""
        connection.close()
        kind = response.getheader("Content-Type") or ""
        text = None if media else body.decode("utf-8", "replace")
        record = {"step": step, "t": time.time(), "method": method, "path": path, "query": [list(q) for q in query],
                  "req_headers": [[k, "REDACTED" if k == "X-Plex-Token" else v] for k, v in sent], "req_body": None,
                  "status": response.status, "reason": response.reason,
                  "res_headers": [[k, "" if k == "Date" else v] for k, v in response.getheaders()],
                  "res_body": text.replace(token, "REDACTED") if text else None, "res_len": len(body)}
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        print("%-28s %s %s %s" % (step, response.status, method, url[:110]))
        return json.loads(text) if text and text.lstrip().startswith("{") else None

    section = args.section
    machine = call("identity", "GET", "/identity")["MediaContainer"]["machineIdentifier"]
    if args.restore:
        call("restore-pref", "PUT", "/library/sections/%s/prefs" % section, [("enableTrackOffsets", "0")])
        return
    call("pref-default", "PUT", "/library/sections/%s/prefs" % section, [("enableTrackOffsets", "0")])
    # Chronicle's library sync (BookRepository, TrackRepository, CollectionsRepository).
    call("sections", "GET", "/library/sections")
    page = [("X-Plex-Container-Start", "0"), ("X-Plex-Container-Size", "100")]
    albums = call("albums", "GET", "/library/sections/%s/all" % section, [("type", "9")] + page)["MediaContainer"]["Metadata"]
    tracks = call("tracks", "GET", "/library/sections/%s/all" % section, [("type", "10")] + page)["MediaContainer"]["Metadata"]
    call("collections", "GET", "/library/sections/%s/collections" % section, [("includeCollections", "1")] + page)
    for album in albums:
        call("album", "GET", "/library/metadata/%s" % album["ratingKey"], [("includeChapters", "1")])
        call("album-children", "GET", "/library/metadata/%s/children" % album["ratingKey"])
    for track in tracks:
        call("track-chapters", "GET", "/library/metadata/%s" % track["ratingKey"], [("includeChapters", "1")])
    book = next(t for t in tracks if t["Media"][0]["Part"][0]["file"].endswith(".m4b"))
    mp3 = next(t for t in tracks if t["Media"][0]["Part"][0]["file"].endswith(".mp3"))
    call("track-plain", "GET", "/library/metadata/%s" % book["ratingKey"])
    # Chronicle's play: the decision per track, then ExoPlayer's reads of the Part it names.
    for track in (book, mp3):
        decision = call("decision", "GET", "/music/:/transcode/universal/decision",
                        [("path", "/library/metadata/%s" % track["ratingKey"]), ("protocol", "http"),
                         ("session", str(uuid.uuid4())), ("hasMDE", "1"), ("directPlay", "1"), ("directStream", "1"),
                         ("musicBitrate", "320"), ("maxAudioBitrate", "320")])
        # PMS 1.43.4 refuses this request (400, "hasMDE was specified along with directPlay without
        # specifying a valid media and part index"); Chronicle then plays the listing's Part key.
        part = (decision["MediaContainer"]["Metadata"][0]["Media"][0]["Part"][0]["key"] if decision
                else track["Media"][0]["Part"][0]["key"])
        call("part-read", "GET", part, headers=CHRONICLE + [("Range", "bytes=0-")], media=True)
        call("part-seek", "GET", part, headers=CHRONICLE + [("Range", "bytes=100000-")], media=True)
        call("part-download", "GET", part, [("download", "1")], media=True)
    uri = "server://%s/com.plexapp.plugins.library/library/metadata/%s" % (machine, book["parentRatingKey"])
    # Retrofit writes its Booleans as words: PMS 1.43.4 answers `repeat=false` 400 (`repeat=0` with the
    # same words elsewhere is 200), so Chronicle's queue is never made and its timelines carry -1.
    queue = call("playqueue", "POST", "/playQueues", [("uri", uri), ("type", "audio"), ("repeat", "false"),
                                                      ("own", "true"), ("includeChapters", "true")])
    item = next(m["playQueueItemID"] for m in queue["MediaContainer"]["Metadata"]
                if m["ratingKey"] == book["ratingKey"]) if queue else -1

    def timeline(step, at, state):
        call(step, "GET", "/:/timeline", [("ratingKey", book["ratingKey"]), ("time", str(at)),
                                         ("key", "/library/metadata/%s" % book["ratingKey"]),
                                         ("duration", str(book["duration"])), ("state", state), ("hasMDE", "1"),
                                         ("identifier", "com.plexapp.plugins.library"), ("playbackTime", str(at)),
                                         ("playQueueItemID", str(item))])

    def reread(step):
        call(step + "-track", "GET", "/library/metadata/%s" % book["ratingKey"], [("includeChapters", "1")])
        call(step + "-album", "GET", "/library/metadata/%s" % book["parentRatingKey"], [("includeChapters", "1")])
        call(step + "-children", "GET", "/library/metadata/%s/children" % book["parentRatingKey"])
        call(step + "-albums", "GET", "/library/sections/%s/all" % section, [("type", "9")] + page)

    timeline("timeline-off-playing", 30000, "playing")
    timeline("timeline-off-paused", 95000, "paused")
    reread("off")
    call("pref-on", "PUT", "/library/sections/%s/prefs" % section, [("enableTrackOffsets", "1")])
    timeline("timeline-on-playing", 30000, "playing")
    timeline("timeline-on-paused", 95000, "paused")
    reread("on")
    timeline("timeline-on-stopped", 125000, "stopped")
    reread("stopped")
    # The other clients' chapter-bearing requests, as recorded on plexav, pointed at the .m4b book.
    def recorded(name, method, path):
        for line in open(os.path.join(MUSIC, name)):
            record = json.loads(line)
            if record["method"] == method and record["path"] == path:
                return record
    swap = lambda value: value.replace("8b9a5a573725219f3a79001e70fcd435b9600995", machine) \
        .replace("/library/metadata/31", "/library/metadata/" + book["parentRatingKey"])
    for name, label in (("droid-music-play.jsonl", "droid"), ("plexamp-play.jsonl", "plexamp"), ("web-music-play.jsonl", "web")):
        record = recorded(name, "POST", "/playQueues")
        headers = [(k, v) for k, v in record["req_headers"] if k.lower().startswith("x-plex") and k != "X-Plex-Token"
                   and k != "X-Plex-Device-Notification-Token"] + [("Accept", "application/json")]
        query = [(k, swap(v)) for k, v in record["query"] if k not in ("X-Plex-Token", "X-Plex-Device-Notification-Token")]
        call(label + "-playqueue", "POST", "/playQueues", query, headers)
    record = recorded("web-music-play.jsonl", "GET", "/library/metadata/31")
    headers = [("Accept", "application/json")]
    query = [(k, v) for k, v in record["query"] if k != "X-Plex-Token"]
    call("web-track", "GET", "/library/metadata/%s" % book["ratingKey"], query, headers)
    call("web-album", "GET", "/library/metadata/%s" % book["parentRatingKey"], query, headers)
    call("hubs", "GET", "/hubs/sections/%s" % section, [("count", "6"), ("excludeFields", "summary")], headers)
    # Played to the end: the scrobbles Chronicle sends for the track and then the book, and its unscrobble.
    call("scrobble-track", "GET", "/:/scrobble", [("key", book["ratingKey"]), ("identifier", "com.plexapp.plugins.library")])
    reread("scrobbled")
    call("scrobble-album", "GET", "/:/scrobble", [("key", mp3["parentRatingKey"]), ("identifier", "com.plexapp.plugins.library")])
    call("scrobbled-mp3-children", "GET", "/library/metadata/%s/children" % mp3["parentRatingKey"])
    call("unscrobble-album", "GET", "/:/unscrobble", [("key", mp3["parentRatingKey"]), ("identifier", "com.plexapp.plugins.library")])
    call("unscrobble-track", "GET", "/:/unscrobble", [("key", book["ratingKey"]), ("identifier", "com.plexapp.plugins.library")])
    reread("unscrobbled")


if __name__ == "__main__":
    main()
