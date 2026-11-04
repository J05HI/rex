#!/usr/bin/env python3
"""A source's VobSub sidecar (`.idx` + `.sub`) is the subtitle a real PMS makes of one beside a video.

fun's Usenet library held 690 VobSub files in 333 releases on 2026-11-04 (31 loose, 659 in RAR/7z
archives) and rex took none: the feed refused every `.idx`. The pair here is a real one, Samurai
Champloo E20's forced track as STARS packed it under the release's `Subs/` (1,245 B idx,
28,672 B sub, `id: de`, one cue at 1:47.56), with McBain's four-language `.idx` (da fi no sv), read
unchanged through fun's zurg. What a real PMS answers for them is
`fixtures/subtitles/vobsub_s1422.json` (a 1.42.2, `probes/record_vobsub.py`):

- one stream per pair, `codec`/`format` `idx`, `Deutsch (VOBSUB External)`, its language its
  `.idx`'s (the last named, McBain's Svenska), a forced flag the source's;
- its key serves the `.idx` (`.idx` too), `.sub` the bitmaps to the byte, `encoding=utf-8` those
  with every byte over 0x7f dropped, any other extension a 404; a text sidecar answers only its
  own extension (`.srt`, `.ass`; `.vtt` a 404); an embedded track any extension as itself (501);
- a conversion for Plex Web burns it (the `.idx`'s first track), one for Plex for Android's or
  iOS's Matroska target muxes it in as `dvd_subtitle`.

Needs ffmpeg (`REX_TRANSCODER`, else PATH's).

    python3 tests/test_vobsub_sidecars.py
"""
import hashlib
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse

from server_command import test_ffmpeg
from test_feed import BYTES_TOKEN, FIXTURES, MOUNT, Server, catalog, check, item, results, serve

FFMPEG = test_ffmpeg()
FFPROBE = shutil.which("ffprobe") if FFMPEG else None
if FFMPEG and os.path.exists(os.path.join(os.path.dirname(FFMPEG), "ffprobe")):
    FFPROBE = os.path.join(os.path.dirname(FFMPEG), "ffprobe")
RECORDED = json.load(open(os.path.join(FIXTURES, "subtitles", "vobsub_s1422.json")))
PAIRS = os.path.join(FIXTURES, "subtitles", "vobsub")
IDX = open(os.path.join(PAIRS, "stars-samurai.champloo.e20.1080p-forced.idx"), "rb").read()
SUB = open(os.path.join(PAIRS, "stars-samurai.champloo.e20.1080p-forced.sub"), "rb").read()
FOUR = open(os.path.join(PAIRS, "mcbain.1080p-hd4u.idx"), "rb").read()
SUBS_S1422 = json.load(open(os.path.join(FIXTURES, "transcode", "subtitles_s1422.json")))
RELEASE = "shows/Samurai.Champloo.E20.German.2004.ANiME.DL.1080p.BluRay.PROPER.x264-STARS/23TCfnhFTFruRnNrKEr75qWntwqz.part01.rar"
VIDEO = RELEASE + "/stars-samurai.champloo.e20.1080p.mkv"
PAIR = RELEASE + "/stars-samurai.champloo.e20.1080p-forced"
EMBEDDED = "movies/Vobsub Embedded (2004)/Vobsub Embedded (2004).mkv"
TEXT = "movies/Vobsub Text Controls (2004)"


def recorded_stream(title):
    """The subtitle streams a real PMS listed on `title`, by id."""
    row = RECORDED["items"][title]["json"]
    return [stream for stream in ((row.get("Media") or [{}])[0].get("Part") or [{}])[0].get("Stream", []) if stream["streamType"] == 3]


def recorded_reads(title):
    stream = next(stream for stream in recorded_stream(title) if stream.get("key") or title == "Vobsub Embedded")
    return RECORDED["streams"]["%s #%s" % (title, stream["id"])]


def recorded_decision(title, shape):
    entry = next(entry for entry in RECORDED["decisions"] if entry["title"] == title and entry["shape"] == shape)
    streams = entry["decision"]["MediaContainer"]["Metadata"][0]["Media"][0]["Part"][0]["Stream"]
    return entry, next(stream for stream in streams if stream["streamType"] == 3)


class Files:
    """A source serving its files with Range, behind the bearer token, counting reads per path."""

    def __init__(self):
        self.files = {}
        self.reads = {}

    def handler(source):
        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                path = urllib.parse.unquote(self.path)
                source.reads[path] = source.reads.get(path, 0) + 1
                body = source.files.get(path)
                if self.headers.get("Authorization") != "Bearer " + BYTES_TOKEN or body is None:
                    self.send_response(401 if body is not None else 404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if isinstance(body, str):
                    body = open(body, "rb").read()
                found = re.match(r"bytes=(\d+)-(\d*)", self.headers.get("Range") or "")
                if found:
                    first = int(found.group(1))
                    last = int(found.group(2)) if found.group(2) else len(body) - 1
                    self.send_response(206)
                    self.send_header("Content-Range", "bytes %d-%d/%d" % (first, last, len(body)))
                    body = body[first:last + 1]
                else:
                    self.send_response(200)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass
        return Handler


def make_media(work):
    """The recorded titles' videos: 120 s of 1920x1080 black h264 and AAC, one with the pair muxed in
    as an embedded dvd_subtitle (`ger`), one with an embedded SRT and ASS; each with its ffprobe."""
    base = os.path.join(work, "base.mkv")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=24", "-f", "lavfi",
                    "-i", "sine=frequency=440:sample_rate=48000", "-t", "120", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "30", "-pix_fmt", "yuv420p", "-g", "48", "-c:a", "aac", "-b:a", "64k", "-ac", "2", base], check=True)
    pair = os.path.join(work, "pair")
    os.makedirs(pair)
    for name, data in (("x.idx", IDX), ("x.sub", SUB)):
        open(os.path.join(pair, name), "wb").write(data)
    embedded = os.path.join(work, "embedded.mkv")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", base, "-i", os.path.join(pair, "x.idx"), "-map", "0", "-map", "1", "-c", "copy",
                    "-metadata:s:s:0", "language=ger", embedded], check=True)
    srt = os.path.join(work, "c.srt")
    ass = os.path.join(work, "c.ass")
    open(srt, "w").write(RECORDED["files"]["Vobsub Text Controls (2004).en.srt"])
    open(ass, "w").write(RECORDED["files"]["Vobsub Text Controls (2004).fr.ass"])
    text = os.path.join(work, "text.mkv")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", base, "-i", srt, "-i", ass, "-map", "0", "-map", "1", "-map", "2", "-c:v", "copy",
                    "-c:a", "copy", "-c:s:0", "srt", "-c:s:1", "ass", "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=ger", text],
                   check=True)
    probe = lambda path: json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
                                                   capture_output=True, text=True, check=True).stdout)
    return {"base": (base, probe(base)), "embedded": (embedded, probe(embedded)), "text": (text, probe(text))}


def pushed(path, media, subtitles=None, name=None):
    file, probe = media
    entry = dict(item("new"), id=path, path=path, file="%s/%s" % (MOUNT, path), name=name or path.split("/")[1],
                 release=path.rsplit("/", 1)[0], media_info=probe, size=os.path.getsize(file))
    entry.pop("ids", None)
    entry.pop("kind", None)
    if subtitles is not None:
        entry["subtitles"] = subtitles
    return entry


def vobsub(stem, idx=IDX, sub=SUB, **fields):
    return dict({"path": stem + ".idx", "size": len(idx), "sub": stem + ".sub", "sub_size": len(sub)}, **fields)


def subtitle_streams(server, key):
    """The item's subtitles as Plex Web reads them, as the reference's were recorded."""
    status, body = server.request("/library/metadata/%s" % key, headers={"X-Plex-Product": "Plex Web", "X-Plex-Platform": "Chrome"})
    row = (body.get("MediaContainer", {}).get("Metadata") or [{}])[0] if status == 200 else {}
    return [stream for stream in ((row.get("Media") or [{}])[0].get("Part") or [{}])[0].get("Stream", []) if stream["streamType"] == 3]


def same_as_recorded(ours, theirs):
    """A real PMS's stream fields, `file` aside (rex lists no sidecar's path, as for an SRT), and
    `canAutoSync`, rex's own projection on every subtitle."""
    wanted = {field: value for field, value in theirs.items() if field not in ("id", "key", "file", "selected")}
    got = {field: value for field, value in ours.items() if field not in ("id", "key", "canAutoSync", "selected")}
    return got == wanted and ours.get("key") == "/library/streams/%s" % ours.get("id"), (got, wanted)


def segment(server, query, cue):
    """The conversion's segment holding `cue`, after its Matroska header when it has one."""
    status, body = server.request("/video/:/transcode/universal/start.m3u8?" + urllib.parse.urlencode(query))
    variant = next((line for line in (body.decode() if isinstance(body, bytes) else "").splitlines() if line and not line.startswith("#")), None)
    if status != 200 or not variant:
        return None, (status, body)
    base = "/video/:/transcode/universal/" + variant.split("?")[0]
    playlist = b""
    for _ in range(60):
        status, playlist = server.request(base)
        if status == 200 and b"#EXTINF" in playlist:
            break
        time.sleep(0.5)
    text = playlist.decode()
    lengths = [float(line.split(":")[1].split(",")[0]) for line in text.splitlines() if line.startswith("#EXTINF")]
    names = [line for line in text.splitlines() if line and not line.startswith("#")]
    at, index = 0.0, 0
    while index + 1 < len(lengths) and at + lengths[index] <= cue:
        at += lengths[index]
        index += 1
    folder = base.rsplit("/", 1)[0] + "/"
    chunks = []
    header = re.search(r'#EXT-X-MAP:URI="([^"]+)"', text)
    if header:
        chunks.append(server.request(folder + header.group(1))[1])
    for _ in range(120):
        status, chunk = server.request(folder + names[index])
        if status == 200 and chunk:
            break
        time.sleep(1)
    chunks.append(chunk if status == 200 else b"")
    return b"".join(chunks), at


def lit_frames(work, data):
    """ffprobe's streams of a segment and how many of its frames hold a light pixel (black video)."""
    path = os.path.join(work, "segment")
    open(path, "wb").write(data)
    streams = json.loads(subprocess.run([FFPROBE, "-v", "error", "-show_entries", "stream=codec_type,codec_name:stream_tags=language",
                                         "-of", "json", path], capture_output=True, text=True).stdout or "{}").get("streams", [])
    frames = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-map", "0:v:0", "-vf", "scale=480:270,format=gray", "-f", "rawvideo", "-"],
                            capture_output=True).stdout
    size = 480 * 270
    lit = sum(1 for at in range(0, len(frames) - size + 1, size) if max(frames[at:at + size]) > 96)
    return streams, lit


def conversion(shape, key, session):
    """A client's own conversion request (`subtitles_s1422.json`'s), pointed at `key`."""
    pairs = next(row["query"] for row in SUBS_S1422["rows"] if row["subtitle"] == "5" and row["shape"] == shape)
    return [(name, "/library/metadata/%s" % key if name == "path" else session
             if name in ("session", "X-Plex-Session-Identifier", "X-Plex-Playback-Session-Id") else value) for name, value in pairs]


def main():
    if not FFMPEG or not FFPROBE:
        print("SKIP: no ffmpeg (set REX_TRANSCODER)")
        return 0
    work = tempfile.mkdtemp(prefix="rex-vobsub-")
    media = make_media(work)
    source = Files()
    source.files["/dav/" + VIDEO] = media["base"][0]
    source.files["/dav/" + PAIR + ".idx"] = IDX
    source.files["/dav/" + PAIR + ".sub"] = SUB
    source.files["/dav/" + EMBEDDED] = media["embedded"][0]
    for name, data in ((TEXT + "/Vobsub Text Controls (2004).mkv", media["text"][0]),
                       (TEXT + "/Vobsub Text Controls (2004).en.srt", RECORDED["files"]["Vobsub Text Controls (2004).en.srt"].encode()),
                       (TEXT + "/Vobsub Text Controls (2004).fr.ass", RECORDED["files"]["Vobsub Text Controls (2004).fr.ass"].encode()),
                       ("shows/McBain/mcbain.1080p-hd4u.idx", FOUR)):
        source.files["/dav/" + name] = data
    port = serve(source.handler())
    catalog(work)
    server = Server(work, port, keys={"REX_TRANSCODER": FFMPEG, "REX_TRANSCODE_HW": "none",
                                      "REX_TRANSCODE_DIR": os.path.join(work, "segments")})
    try:
        # -- the stream a real PMS lists -----------------------------------------------------
        status, body = server.feed("POST", "items", {"items": [pushed(VIDEO, media["base"], [vobsub(PAIR)])]})
        key = server.keys().get(VIDEO)
        streams = subtitle_streams(server, key) if key else []
        check("a pushed .idx with its .sub is taken", status == 200 and key and len(streams) == 1, (status, body, streams))
        stream = streams[0] if streams else {}
        print("      the stream: %s" % json.dumps(stream, ensure_ascii=False))
        ok, detail = same_as_recorded(stream, recorded_stream("Vobsub Pair")[0])
        check("it is the stream a real PMS lists for the pair: idx, Deutsch off its .idx, VOBSUB External", ok, detail)
        check("its .idx was read at the push, its .sub was not",
              source.reads.get("/dav/" + PAIR + ".idx") == 1 and not source.reads.get("/dav/" + PAIR + ".sub"), source.reads)
        status, body = server.feed("POST", "items", {"items": [pushed(VIDEO, media["base"], [vobsub(PAIR)])]})
        check("the same push again reads nothing and keeps it", body.get("unchanged") == 1 and source.reads.get("/dav/" + PAIR + ".idx") == 1
              and [s.get("id") for s in subtitle_streams(server, key)] == [stream.get("id")], (body, source.reads))

        tagged = pushed(VIDEO, media["base"], [vobsub(PAIR, language="en", forced=True)])
        server.feed("POST", "items", {"items": [tagged]})
        ok, detail = same_as_recorded((subtitle_streams(server, key) or [{}])[0], recorded_stream("Vobsub Tagged (2004)")[0])
        check("the source's language gives way to its .idx's, its forced flag is kept (Movie.en.forced.idx)", ok, detail)
        four = [vobsub("shows/McBain/mcbain.1080p-hd4u", idx=FOUR)]
        server.feed("POST", "items", {"items": [pushed(VIDEO, media["base"], four)]})
        ok, detail = same_as_recorded((subtitle_streams(server, key) or [{}])[0], recorded_stream("Vobsub Four Languages")[0])
        check("an .idx of four languages is one stream of its last (McBain: Svenska)", ok and len(subtitle_streams(server, key)) == 1, detail)

        bad = [{"path": PAIR + ".idx"}, {"path": PAIR + ".idx", "sub": PAIR + ".srt"}, {"path": PAIR + ".sub"}, vobsub(PAIR)]
        status, body = server.feed("POST", "items", {"items": [pushed(VIDEO, media["base"], bad)]})
        log = open(os.path.join(work, "server.log"), errors="replace").read()
        check("an .idx without its .sub, or with a sub that is no .sub, and a bare .sub are left out, logged",
              status == 200 and len(subtitle_streams(server, key)) == 1 and "needs its .sub" in log and "sub must be the path of its .sub" in log
              and "%s.sub: not a subtitle format" % PAIR in log, (body, subtitle_streams(server, key)))

        # -- what its key answers, as a real PMS answers it ------------------------------------
        sid = (subtitle_streams(server, key) or [{}])[0].get("id")
        reads = recorded_reads("Vobsub Pair")
        wrong = []
        for label, recorded in reads.items():
            method, _, rest = label.partition(" ")
            suffix, _, query = rest.replace("(bare)", "").partition("?")
            status, raw = server.request("/library/streams/%s%s%s" % (sid, suffix, "?" + query if query else ""), method)
            raw = raw if isinstance(raw, bytes) else json.dumps(raw).encode()
            expect = {"stars-samurai.champloo.e20.1080p-forced.idx": IDX, "stars-samurai.champloo.e20.1080p-forced.sub": SUB}.get(recorded.get("is"))
            same = status == recorded["status"] and (method == "HEAD" or status != 200 or (raw == expect if expect is not None
                                                                                           else hashlib.sha256(raw).hexdigest() == recorded.get("sha256")))
            if not same:
                wrong.append((label, status, recorded["status"], len(raw), recorded.get("length")))
        check("its key, .idx, .sub, .sub?encoding=utf-8 and every other extension answer as the reference's (%d reads)" % len(reads),
              not wrong, wrong)
        status, raw = server.request("/library/streams/%s.sub" % sid)
        check("the .sub is the bitmaps to the byte", status == 200 and raw == SUB, (status, len(raw)))

        server.feed("POST", "items", {"items": [pushed(TEXT + "/Vobsub Text Controls (2004).mkv", media["text"], [
            {"path": TEXT + "/Vobsub Text Controls (2004).en.srt", "language": "en"},
            {"path": TEXT + "/Vobsub Text Controls (2004).fr.ass", "language": "fr"}])]})
        text_key = server.keys().get(TEXT + "/Vobsub Text Controls (2004).mkv")
        ours = {(stream.get("codec"), bool(stream.get("key"))): stream["id"] for stream in subtitle_streams(server, text_key)}
        theirs = {(stream.get("codec"), bool(stream.get("key"))): stream["id"] for stream in recorded_stream("Vobsub Text Controls (2004)")}
        wrong = []
        for kind, recorded_id in theirs.items():
            for label, recorded in RECORDED["streams"]["Vobsub Text Controls (2004) #%s" % recorded_id].items():
                method, _, rest = label.partition(" ")
                suffix = rest.replace("(bare)", "").partition("?")[0]
                # rex reads an embedded text track itself (subtitle sync); the extensions are what is measured here.
                if not kind[1] and suffix == "":
                    continue
                status, raw = server.request("/library/streams/%s%s" % (ours.get(kind), suffix), method)
                if kind[1] and status != recorded["status"] or not kind[1] and (status == 404) != (recorded["status"] == 404):
                    wrong.append((kind, label, status, recorded["status"]))
        check("an SRT or ASS sidecar answers only its own extension, an embedded track any (%d reads)" % sum(
              len(RECORDED["streams"]["Vobsub Text Controls (2004) #%s" % i]) for i in theirs.values()), not wrong, wrong)

        server.feed("POST", "items", {"items": [pushed(EMBEDDED, media["embedded"], [])]})
        embedded_key = server.keys().get(EMBEDDED)
        embedded = subtitle_streams(server, embedded_key)
        wrong = [(label, status) for label in recorded_reads("Vobsub Embedded")
                 for status in [server.request("/library/streams/%s%s" % ((embedded or [{}])[0].get("id"), label.partition(" ")[2].replace("(bare)", "").partition("?")[0]),
                                               label.partition(" ")[0])[0]] if status != 501]
        check("an embedded VobSub is the 501 by every extension, as the reference's", embedded and embedded[0].get("codec") == "vobsub" and not wrong, wrong)

        # -- conversions ---------------------------------------------------------------------
        server.feed("POST", "items", {"items": [pushed(VIDEO, media["base"], [vobsub(PAIR)])]})
        sid = (subtitle_streams(server, key) or [{}])[0].get("id")
        part = server.row(key)["Media"][0]["Part"][0]["id"]
        server.request("/library/parts/%s?subtitleStreamID=%s&allParts=1" % (part, sid), "PUT")
        entry, theirs = recorded_decision("Vobsub Pair", "web-direct")
        status, body = server.request(entry["path"].replace("%2Flibrary%2Fmetadata%2F23", urllib.parse.quote("/library/metadata/%s" % key, safe="")))
        container = (body or {}).get("MediaContainer", {}) if isinstance(body, dict) else {}
        ours = next((stream for stream in container.get("Metadata", [{}])[0].get("Media", [{}])[0].get("Part", [{}])[0].get("Stream", [])
                     if stream.get("streamType") == 3), {})
        check("Plex Web's direct-play request: Direct play OK, the pair ignored, as the reference's",
              container.get("mdeDecisionCode") == entry["decision"]["MediaContainer"]["mdeDecisionCode"]
              and ours.get("decision") == theirs.get("decision") == "ignore" and ours.get("codec") == "idx",
              (container.get("mdeDecisionCode"), ours.get("decision"), ours.get("codec")))
        for shape in ("web", "android"):
            entry, theirs = recorded_decision("Vobsub Pair", shape)
            session = "vobsub-%s" % shape
            status, body = server.request("/video/:/transcode/universal/decision?" + urllib.parse.urlencode(conversion(shape, key, session)))
            container = (body or {}).get("MediaContainer", {}) if isinstance(body, dict) else {}
            ours = next((stream for stream in container.get("Metadata", [{}])[0].get("Media", [{}])[0].get("Part", [{}])[0].get("Stream", [])
                         if stream.get("streamType") == 3), {})
            fields = ("burn", "codec", "format", "decision", "location", "languageTag")
            check("%s: the decision's row for the pair is the reference's (%s)" % (shape, theirs.get("decision")),
                  status == 200 and container.get("transcodeDecisionCode") == entry["decision"]["MediaContainer"]["transcodeDecisionCode"]
                  and all(ours.get(field) == theirs.get(field) for field in fields) and ours.get("key") == "/library/streams/%s" % sid,
                  ({field: ours.get(field) for field in fields}, {field: theirs.get(field) for field in fields}))
            data, starts = segment(server, conversion(shape, key, session), 107.6)
            streams, lit = lit_frames(work, data or b"")
            kinds = [(stream.get("codec_name"), (stream.get("tags") or {}).get("language")) for stream in streams if stream.get("codec_type") == "subtitle"]
            if shape == "web":
                check("Plex Web's segment at the cue has the subtitle burned into its black picture, as the reference's",
                      data and lit > 0 and not kinds, (starts, lit, kinds))
            else:
                check("Plex for Android's Matroska segment carries it as dvd_subtitle (deu), its picture untouched, as the reference's",
                      data and kinds == [("dvd_subtitle", "deu")] and lit == 0, (starts, lit, kinds))
            server.request("/video/:/transcode/universal/stop?session=%s" % session)

        part = server.row(embedded_key)["Media"][0]["Part"][0]["id"]
        server.request("/library/parts/%s?subtitleStreamID=%s&allParts=1" % (part, (embedded or [{}])[0].get("id")), "PUT")
        for shape in ("web",):
            entry, theirs = recorded_decision("Vobsub Embedded", shape)
            session = "embedded-%s" % shape
            status, body = server.request("/video/:/transcode/universal/decision?" + urllib.parse.urlencode(conversion(shape, embedded_key, session)))
            container = (body or {}).get("MediaContainer", {}) if isinstance(body, dict) else {}
            ours = next((stream for stream in container.get("Metadata", [{}])[0].get("Media", [{}])[0].get("Part", [{}])[0].get("Stream", [])
                         if stream.get("streamType") == 3), {})
            fields = ("burn", "codec", "format", "decision", "location")
            check("%s: an embedded VobSub's decision row is the reference's (%s)" % (shape, theirs.get("decision")),
                  status == 200 and all(ours.get(field) == theirs.get(field) for field in fields),
                  ({field: ours.get(field) for field in fields}, {field: theirs.get(field) for field in fields}))
            data, starts = segment(server, conversion(shape, embedded_key, session), 107.6)
            streams, lit = lit_frames(work, data or b"")
            kinds = [stream.get("codec_name") for stream in streams if stream.get("codec_type") == "subtitle"]
            if shape == "web":
                check("Plex Web burns an embedded VobSub", data and lit > 0 and not kinds, (starts, lit, kinds))
            else:
                check("Plex for Android's Matroska segment carries the embedded VobSub copied, as the reference's",
                      data and kinds == ["dvd_subtitle"] and lit == 0, (starts, lit, kinds))
            server.request("/video/:/transcode/universal/stop?session=%s" % session)
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    failed = results.count(False)
    print("\n%d/%d VobSub sidecar checks passed" % (len(results) - failed, len(results)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
