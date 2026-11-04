#!/usr/bin/env python3
"""Record what a real PMS makes of VobSub (`.idx` + `.sub`) subtitles beside a video, and of one
embedded in it.

Run against a 1.42.2 PMS (plexscratch42's build; one that can download its transcoder's codecs, which
an offline one cannot), with a Personal Media movie library over a folder holding one
generated 120 s 1080p black h264/aac `.mkv` per title and, beside each, a real VobSub pair read off
a release (its `.idx` text and `.sub` bytes unchanged; the pair's own name, or the video's):

    PLACE="<which PMS>" PLEX_URL=http://<its address> PLEX_TOKEN=<its .LocalAdminToken> PAIRS=<the real pairs, c.srt, c.ass> \\
        python3 probes/record_vobsub.py <section key> tests/fixtures/subtitles/vobsub_s1422.json

For each title it records the item as Plex Web reads it (JSON and XML), then for each subtitle
stream its `key`'s answer (status, headers, length, and which of the pair's files the body is, by
sha256; no bytes). For the titles named in DECIDE it selects each VobSub stream (`PUT
/library/parts/{id}?subtitleStreamID=`, `allParts=1` as Plex Web sends it), sends each client's own
conversion request (`tests/fixtures/transcode/subtitles_s1422.json`'s rows: Plex for Android,
Plex for iOS's Generic profile with its extra, Plex Web), keeps the decision and the start answer,
reads the segment at the first cue and says what it holds: its streams (a copied subtitle shows as
one) and its lit frames (the video is black, so a burned subtitle is any light pixel), matched to
the `.idx` track FFmpeg draws alike. Plex Web's direct-play request
(`decision_stream_language_zen.json`'s) is kept too. No token or Plex byte is written.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

URL, TOKEN, PAIRS = os.environ["PLEX_URL"], os.environ["PLEX_TOKEN"], os.environ["PAIRS"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION, OUT = sys.argv[1], sys.argv[2]
# The cue each title's first subtitle is shown at, from its .idx (Samurai Champloo E20's forced
# track: 00:01:47:560; McBain's four tracks all start 00:00:30:160).
CUES = {"Vobsub Pair": 107.6, "Vobsub Embedded": 107.6, "Vobsub Four Languages": 30.2, "Vobsub Tagged": 107.6}
DECIDE = ("Vobsub Pair", "Vobsub Embedded", "Vobsub Four Languages")
# The pair each title carries, in PAIRS, to tell which of its tracks a conversion burned.
IDX = {"Vobsub Pair": "stars-samurai.champloo.e20.1080p-forced.idx", "Vobsub Embedded": "stars-samurai.champloo.e20.1080p-forced.idx",
       "Vobsub Four Languages": "mcbain.1080p-hd4u.idx"}
IDX = {title: os.path.join(PAIRS, name) for title, name in IDX.items()}
SUBS = json.load(open(os.path.join(ROOT, "tests/fixtures/transcode/subtitles_s1422.json")))
SHAPES = {row["shape"]: row["query"] for row in SUBS["rows"] if row["subtitle"] == "5" and row["shape"] in ("android", "ios-generic", "web")}
WEB_DIRECT = json.load(open(os.path.join(ROOT, "tests/fixtures/decision_stream_language_zen.json")))["decision_path"]


def ask(method, path, params=None, accept="application/json", raw=False):
    query = ("&" if "?" in path else "?") + urllib.parse.urlencode(params) if params else ""
    headers = {"Accept": accept, "X-Plex-Token": TOKEN, "X-Plex-Client-Identifier": "rex-vobsub-probe",
               "X-Plex-Product": "Plex Web", "X-Plex-Platform": "Chrome"}
    request = urllib.request.Request(URL + path + query, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            status, body, got = response.status, response.read(), list(response.headers.items())
    except urllib.error.HTTPError as error:
        status, body, got = error.code, error.read(), list(error.headers.items())
    if raw:
        return status, body, got
    try:
        return status, json.loads(body) if body else None
    except ValueError:
        return status, body.decode("utf-8", "replace")


def redact(value):
    """The token never; the section's uuid and the probe's own session names are the copy's."""
    text = json.dumps(value)
    text = text.replace(TOKEN, "<token>")
    text = re.sub(r'"librarySectionUUID": "[^"]+"', '"librarySectionUUID": "<section-uuid>"', text)
    return json.loads(text)


def pair_files():
    known = {}
    for name in os.listdir(PAIRS):
        data = open(os.path.join(PAIRS, name), "rb").read()
        known[hashlib.sha256(data).hexdigest()] = name
    return known


def segment_facts(chunks, idx=None, starts=0.0):
    """What a segment holds: ffprobe's streams (a copied subtitle shows as one, with its
    language), and for its video every frame's light pixels (the clip is black, so a burned
    subtitle is any light pixel). With the title's `.idx`, the first lit frame is compared with
    FFmpeg's own rendering of each of its tracks at the same time, which says which one was burned."""
    work = tempfile.mkdtemp(prefix="vobsub-seg-")
    path = os.path.join(work, "seg")
    with open(path, "wb") as out:
        for chunk in chunks:
            out.write(chunk)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type,codec_name,width,height:stream_tags=language:format=start_time",
                            "-of", "json", path], capture_output=True, text=True)
    probed = json.loads(probe.stdout or "{}")
    frames = os.path.join(work, "frames.gray")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-vf", "scale=480:270,format=gray", "-f", "rawvideo", frames], capture_output=True)
    data = open(frames, "rb").read() if os.path.exists(frames) else b""
    size = 480 * 270
    lit = [sum(1 for p in data[at:at + size] if p > 96) for at in range(0, len(data) - size + 1, size)]
    facts = {"streams": probed.get("streams", []), "frames": len(lit), "litFrames": sum(1 for n in lit if n), "mostLitPixels": max(lit or [0])}
    first = next((n for n, count in enumerate(lit) if count), None)
    if idx and first is not None:
        start = float(probed.get("format", {}).get("start_time", 0))
        times = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts_time", "-of", "csv=p=0", path],
                               capture_output=True, text=True).stdout.split()
        # On the title's clock: the segment's place in the playlist plus the frame's within it
        # (PMS's MPEG-TS clock runs 10 s ahead of the title's).
        when = starts + (float(times[first]) - start if first < len(times) else 0.0)
        burned = data[first * size:(first + 1) * size]
        tracks = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "s", "-show_entries", "stream=index", "-of", "csv=p=0", idx],
                                    capture_output=True, text=True).stdout.split().__len__())
        distances = []
        for track in range(tracks):
            mine = os.path.join(work, "track%d.gray" % track)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=1920x1080:r=24:d=%.3f" % (when + 1),
                            "-i", idx, "-filter_complex", "[0:v][1:%d]overlay,scale=480:270,format=gray" % track,
                            "-ss", "%.3f" % when, "-frames:v", "1", "-f", "rawvideo", mine], capture_output=True)
            ours = open(mine, "rb").read() if os.path.exists(mine) else b""
            distances.append(round(sum(abs(a - b) for a, b in zip(ours, burned)) / max(len(burned), 1), 3) if len(ours) == len(burned) else None)
        facts["firstLitAt"] = when
        facts["trackDistances"] = distances
    return facts


def play(query, cue, idx=None):
    """start.m3u8 for `query`, its variant, and the segment holding `cue`: what each answered."""
    status, body, _ = ask("GET", "/video/:/transcode/universal/start.m3u8", query, accept="*/*", raw=True)
    text = body.decode("utf-8", "replace")
    found = {"start": {"status": status, "body": text}}
    variant = next((line for line in text.splitlines() if line and not line.startswith("#")), None)
    if status != 200 or not variant:
        return found
    base = "/video/:/transcode/universal/" + variant.split("?")[0]
    for _ in range(60):
        status, body, _ = ask("GET", base, accept="*/*", raw=True)
        if status == 200 and b"#EXTINF" in body:
            break
        time.sleep(1)
    playlist = body.decode("utf-8", "replace")
    found["media_playlist"] = {"status": status, "head": "\n".join(playlist.splitlines()[:8])}
    if status != 200:
        return found
    lengths = [float(line.split(":")[1].split(",")[0]) for line in playlist.splitlines() if line.startswith("#EXTINF")]
    names = [line for line in playlist.splitlines() if line and not line.startswith("#")]
    header = re.search(r'#EXT-X-MAP:URI="([^"]+)"', playlist)
    at, index = 0.0, 0
    while index + 1 < len(lengths) and at + lengths[index] <= cue:
        at += lengths[index]
        index += 1
    chunks = []
    folder = base.rsplit("/", 1)[0] + "/"
    if header:
        status, head, headers = ask("GET", folder + header.group(1), accept="*/*", raw=True)
        found["header"] = {"name": header.group(1), "status": status, "length": len(head), "headers": headers}
        chunks.append(head)
    for _ in range(90):
        status, chunk, headers = ask("GET", folder + names[index], accept="*/*", raw=True)
        if status == 200 and chunk:
            break
        time.sleep(2)
    found["segment"] = {"name": names[index], "starts": at, "status": status, "length": len(chunk), "headers": headers}
    chunks.append(chunk)
    found["segment"]["holds"] = segment_facts(chunks, idx, at)
    return found


def compact(record):
    """The fixture: each read's status, length and which file it is (else its digest, and a short
    text body as it is), the headers once per stream, Date dropped; each query as sent."""
    for reads in record["streams"].values():
        seen = False
        for label, read in reads.items():
            headers = [pair for pair in read.pop("headers", []) if pair[0] != "Date"]
            if not seen and label == "GET (bare)":
                read["headers"], seen = headers, True
            read.pop("head", None)
            if read.get("is"):
                read.pop("sha256", None)
                read.pop("text", None)
            elif read.get("text") is None:
                read.pop("text", None)
    for entry in record["decisions"]:
        for part in ("header", "segment"):
            if part in entry:
                entry[part]["headers"] = [pair for pair in entry[part]["headers"] if pair[0] != "Date"]
    return record


def main():
    known = pair_files()
    status, body = ask("GET", "/library/sections/%s/all" % SECTION)
    titles = {row["title"]: row["ratingKey"] for row in body["MediaContainer"].get("Metadata", [])}
    record = {"_provenance": None, "items": {}, "streams": {}, "decisions": []}
    for title, key in sorted(titles.items()):
        status, body = ask("GET", "/library/metadata/%s" % key)
        row = body["MediaContainer"]["Metadata"][0]
        _, xml = ask("GET", "/library/metadata/%s" % key, accept="application/xml")
        record["items"][title] = {"json": row, "xml": xml}
        for media in row.get("Media", []):
            for part in media.get("Part", []):
                for stream in part.get("Stream", []):
                    if stream.get("streamType") != 3:
                        continue
                    reads = {}
                    # Plex Web and the apps read a sidecar at its key; Plezy appends the extension
                    # it reads the codec as (`.srt`, `.ass`, `.vtt`, `.sup` for PGS, `.sub` for any
                    # bitmap) and `encoding=utf-8`.
                    for suffix in ("", ".srt", ".vtt", ".ass", ".ssa", ".smi", ".txt", ".sub", ".idx", ".sup", ".xyz"):
                        for query in ("", "?encoding=utf-8"):
                            path = "/library/streams/%s%s%s" % (stream["id"], suffix, query)
                            status, data, headers = ask("GET", path, accept="*/*", raw=True)
                            text = data.decode("utf-8", "replace") if len(data) < 2048 and b"\0" not in data else None
                            reads["GET %s%s" % (suffix or "(bare)", query)] = {
                                "status": status, "headers": headers, "length": len(data),
                                "is": known.get(hashlib.sha256(data).hexdigest()), "sha256": hashlib.sha256(data).hexdigest(),
                                "head": data[:48].decode("latin-1"), "text": text}
                    status, data, headers = ask("HEAD", "/library/streams/%s" % stream["id"], accept="*/*", raw=True)
                    reads["HEAD"] = {"status": status, "headers": headers}
                    record["streams"]["%s #%s" % (title, stream["id"])] = reads
    for title in DECIDE:
        row = record["items"].get(title, {}).get("json")
        if not row:
            continue
        part = row["Media"][0]["Part"][0]
        for stream in [stream for stream in part["Stream"] if stream.get("streamType") == 3][:2]:
            ask("PUT", "/library/parts/%s" % part["id"], {"subtitleStreamID": stream["id"], "allParts": 1})
            shapes = dict(SHAPES)
            for shape, pairs in shapes.items():
                session = "vobsub-%s-%s" % (shape, stream["id"])
                query = []
                for name, value in pairs:
                    if name == "path":
                        value = "/library/metadata/%s" % row["ratingKey"]
                    elif name in ("session", "X-Plex-Session-Identifier", "X-Plex-Playback-Session-Id"):
                        value = session
                    elif name == "X-Plex-Client-Identifier":
                        value = "rexvobsub-" + shape
                    query.append((name, value))
                status, decision = ask("GET", "/video/:/transcode/universal/decision", query)
                entry = {"title": title, "subtitle": stream["id"], "shape": shape, "query": query,
                         "decision_status": status, "decision": decision}
                entry.update(play(query, CUES.get(title, 0), IDX.get(title)))
                ask("GET", "/video/:/transcode/universal/stop", {"session": session})
                record["decisions"].append(entry)
            direct = WEB_DIRECT.replace("%2Flibrary%2Fmetadata%2F1098", urllib.parse.quote("/library/metadata/%s" % row["ratingKey"], safe=""))
            direct = direct.replace("<s>", "vobsub-direct-%s" % stream["id"]).replace("<L>", "en").replace("<c>", "rexvobsub-web")
            status, decision = ask("GET", direct)
            record["decisions"].append({"title": title, "subtitle": stream["id"], "shape": "web-direct",
                                        "path": direct, "decision_status": status, "decision": decision})
        ask("PUT", "/library/parts/%s" % part["id"], {"subtitleStreamID": 0, "allParts": 1})
    record["_provenance"] = (
        "Recorded %s by probes/record_vobsub.py on %s, over its loopback hop with its .LocalAdminToken, in a "
        "Personal Media (tv.plex.agents.none) movie library: offline, the Plex Movie agent adds no local subtitle at "
        "all, and a PMS that cannot download its transcoder's codecs answers every conversion's playlist 500 "
        "('Decoder install failed: h264'). Each title is a generated 120 s 1920x1080 black h264 + AAC .mkv, made "
        "byte-unique (PMS takes same-hash files for one part), with real VobSub pairs beside it read unchanged off "
        "releases through fun's zurg: Samurai Champloo E20's forced track (STARS, inside the release's RAR under "
        "Subs/; 1,245 B idx, 28,672 B sub, `id: de`), The 100 S02E01's forced track (RSG, loose, named for the group "
        "not the video; 1,380 B / 36,864 B, `id: de`) and McBain (HD4U, loose; 84,745 B / 12,879,872 B, da fi no sv "
        "in one pair; listed as four streams until the part is analysed, then as one, the first's id with the last's "
        "language, reproduced on plexscratch42 with PUT .../analyze). `Vobsub Embedded` is the clip with Champloo's "
        "pair muxed in by mkvmerge (dvd_subtitle, ger, zlib), `Vobsub Text Controls` an embedded SRT and ASS and the "
        "`.en.srt`/`.fr.ass` in `files`. `streams`: every subtitle's key read bare and with each extension, with and "
        "without `encoding=utf-8` (as Plezy asks), each body by length and which file it is (else its digest); "
        "`decisions`: each client's own conversion request (subtitles_s1422.json's rows) with that stream selected, "
        "the start answer and the segment at the first cue: its streams, its lit frames (the video is black) and, for "
        "a burn, how far the first lit frame is from FFmpeg's own rendering of each of the .idx's tracks; Plex Web's "
        "direct-play request (decision_stream_language_zen.json's) too." % (time.strftime("%Y-%m-%d"), os.environ.get("PLACE", "a PMS")))
    record["files"] = {"Vobsub Text Controls (2004).en.srt": open(os.path.join(PAIRS, "c.srt")).read(),
                       "Vobsub Text Controls (2004).fr.ass": open(os.path.join(PAIRS, "c.ass")).read()}
    json.dump(redact(compact(record)), open(OUT, "w"), indent=1, ensure_ascii=False)
    print("wrote", OUT)


main()
