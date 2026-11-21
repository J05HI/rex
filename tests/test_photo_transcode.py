#!/usr/bin/env python3
"""`/photo/:/transcode` at a size answers as plexlab answers it (rex#24).

plexlab (PMS 1.43.4) was asked by hand for its poster (Plex's agent, 1920x2880), its clearLogo (an
RGBA PNG) and pictures made for the purpose, at Plex Web's grid size and around it
(`probes/record_photo_transcode.py`, `fixtures/photos/transcode_s1434.json`): the picture fitted
inside the box or covering it (`minSize=1`), never grown without `upscale=1`, upright by its EXIF
orientation, a JPEG with full-resolution chroma at `quality=` (75), a PNG kept a PNG and labelled
`image/jpeg`, `format=png`/`jpeg` choosing. rex used to answer the original whatever was asked.

Plex's artwork is not checked in, so a source here serves stand-ins of the same sizes and formats
and the made pictures as the probe makes them, each one home video's `display` thumb; every
recorded request is replayed for that row's thumb and compared with plexlab's answer. Needs FFmpeg
(REX_TRANSCODER, else PATH's).

    python3 tests/test_photo_transcode.py
"""
import collections
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

import test_feed_display
from server_command import test_ffmpeg
from test_feed import MOUNT, Bytes, Server, catalog, check, results, serve

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = json.load(open(os.path.join(HERE, "fixtures", "photos", "transcode_s1434.json")))
# Not emulated (findings, "The photo transcoder, measured"): WebP and GIF output (no recorded
# client asks; rex for Android's FFmpeg has no WebP encoder) and `blur=` are answered as without
# them; a GIF is served as it is; plexlab's 500s (a negative side, past 10000 pixels) are the
# picture as it is, and a 1-pixel side is fitted rather than refused.
OTHER_FORMATS = ("webp", "gif")


def picture(ffmpeg, source, codec, out):
    subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", source, "-frames:v", "1"] + codec + [out], check=True)
    return open(out, "rb").read()


def with_orientation(jpeg, orientation):
    """The JPEG with an EXIF block carrying `orientation`, right after its SOI, as a camera writes it."""
    tiff = b"MM\x00\x2a" + struct.pack(">I", 8) + struct.pack(">H", 1) + struct.pack(">HHIH", 0x0112, 3, 1, orientation) + b"\x00\x00" + b"\x00" * 4
    app1 = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1 + jpeg[2:]


def stand_ins(ffmpeg, work):
    """Each recorded source's picture: Plex's two by size and format, the probe's own as it makes them."""
    out = lambda name: os.path.join(work, name)
    jpeg = ["-c:v", "mjpeg", "-q:v", "2", "-f", "image2"]
    band = picture(ffmpeg, "color=blue:s=300x200,drawbox=x=0:y=0:w=100:h=200:color=red:t=fill", jpeg, out("band.jpg"))
    made = {
        "poster": picture(ffmpeg, "testsrc2=s=1920x2880", jpeg, out("poster.jpg")),
        "logo": picture(ffmpeg, "color=red@0.5:s=786x132,format=rgba", ["-c:v", "png", "-f", "image2"], out("logo.png")),
        "exif6": with_orientation(band, 6),
        "exif1": band,
        "alpha": picture(ffmpeg, "color=0x00ff00@0.5:s=600x300,format=rgba", ["-c:v", "png", "-f", "image2"], out("alpha.png")),
        "gif": picture(ffmpeg, "color=0x00ff00:s=600x300", ["-f", "gif"], out("still.gif")),
    }
    if b"libwebp" in subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True).stdout:
        made["webp"] = picture(ffmpeg, "color=0x00ff00:s=600x300", ["-c:v", "libwebp", "-f", "webp"], out("still.webp"))
    return made


def describe(body):
    """The bytes' own format, size and what the comparison reads: a JPEG's sampling, SOF and EXIF, a PNG's colour."""
    if body.startswith(b"\x89PNG"):
        width, height, depth, colour = struct.unpack(">IIBB", body[16:26])
        return {"format": "PNG", "size": [width, height], "mode": {2: "RGB", 6: "RGBA"}.get(colour, colour)}
    if not body.startswith(b"\xff\xd8"):
        return {"format": body[:4]}
    at, out = 2, {"format": "JPEG", "exif": False}
    while at + 4 <= len(body) and body[at] == 0xFF:
        marker, length = body[at + 1], struct.unpack(">H", body[at + 2:at + 4])[0]
        if marker == 0xE1 and body[at + 4:at + 10] == b"Exif\x00\x00":
            out["exif"] = True
        if marker in (0xC0, 0xC1, 0xC2):
            height, width, count = struct.unpack(">HHB", body[at + 5:at + 10])
            factors = {body[at + 11 + 3 * i] for i in range(count)}
            out.update(size=[width, height], progressive=marker == 0xC2,
                       # Every component sampled alike is full-resolution chroma (FFmpeg writes 1x2 for all three).
                       subsampling="4:4:4" if len(factors) == 1 else "other", mode="RGB" if count == 3 else count)
        if marker == 0xDA:
            break
        at += 2 + length
    return out


def pixels(ffmpeg, body, work):
    """The colour at the top and bottom middle, decoded as FFmpeg reads the answer (no EXIF left to turn it)."""
    path = os.path.join(work, "answer.bin")
    open(path, "wb").write(body)
    width, height = describe(body)["size"]
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", path, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                         capture_output=True).stdout
    at = lambda x, y: list(raw[(y * width + x) * 3:(y * width + x) * 3 + 3])
    return at(width // 2, 2), at(width // 2, height - 3)


def near(ours, theirs):
    return len(ours) == 3 and all(abs(a - b) <= 40 for a, b in zip(ours, theirs))


def fetch(server, path):
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (server.port, path), headers={"X-Plex-Token": server.token})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.headers.get("Content-Type"), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type"), error.read()


def transcode(server, query, url):
    return fetch(server, "/photo/:/transcode?" + urllib.parse.urlencode(list(query) + [("url", url)]))


def kept(work):
    folder = os.path.join(work, "artwork-cache")
    return sorted(name for name in os.listdir(folder) if not name.endswith(".partial")) if os.path.isdir(folder) else []


class Listener:
    """A loopback port that counts who dials it: what a playlist handed to FFmpeg would open."""

    def __init__(self):
        self.socket = socket.socket()
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(8)
        self.port, self.dialled = self.socket.getsockname()[1], 0
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                connection, _ = self.socket.accept()
            except OSError:
                return
            self.dialled += 1
            connection.close()


def main():
    ffmpeg = test_ffmpeg()
    if not ffmpeg:
        print("SKIP: needs FFmpeg (REX_TRANSCODER or PATH's)")
        return 0
    source = Bytes()
    bytes_port = serve(source.handler())
    work = tempfile.mkdtemp(prefix="rex-photo-transcode-")
    catalog(work)
    made = stand_ins(ffmpeg, work)
    listener = Listener()
    made["playlist"] = ("#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1,\nhttp://127.0.0.1:%d/x.ts\n#EXT-X-ENDLIST\n" % listener.port).encode()
    base = "http://127.0.0.1:%d/dav" % bytes_port
    home = json.loads(json.dumps(test_feed_display.HOME).replace("{SOURCE}", base).replace("{MOUNT}", MOUNT))
    source.files.update(["/dav/_thumbs/birthday-1ce30e.jpg"])
    items = []
    for number, (name, body) in enumerate(sorted(made.items()), 1):
        path = "/dav/_thumbs/%s" % name
        source.files.add(path)
        source.bodies[path] = body
        source.types[path] = "image/jpeg"
        item = json.loads(json.dumps(home))
        item.update(id="photo-%s" % name, path="shows/home-videos/%s.mov" % name, file="%s/shows/home-videos/%s.mov" % (MOUNT, name),
                    episode=number)
        item["display"] = dict(item["display"], title=name, thumb=base + "/_thumbs/" + name)
        items.append(item)
    keys = {"REX_TRANSCODER": ffmpeg, "REX_ARTWORK_CACHE_MB": "64"}
    server = Server(work, bytes_port, keys=keys)
    try:
        status, body = server.feed("POST", "items", {"items": items})
        check("each picture's home video is taken", status == 200 and (body or {}).get("created") == len(items), (status, body))
        ids = server.keys()
        thumbs = {name: server.row(ids["photo-" + name]).get("thumb") for name in made}
        check("each is its row's own thumb", all(thumbs.values()), thumbs)

        # The ultrablur colours read the picture itself, so nothing is fitted for them.
        status, _, colours = fetch(server, "/services/ultrablur/colors?url=" + urllib.parse.quote(thumbs["poster"], safe=""))
        check("ultrablur's colours of the poster are answered from the picture itself, nothing fitted",
              status == 200 and not kept(work), (status, colours[:200], kept(work)))

        answers = collections.defaultdict(dict)
        for answer in FIXTURE["answers"]:
            name, query = answer["source"], [tuple(pair) for pair in answer["query"]]
            if name not in made:
                continue
            asked = dict(query)
            status, kind, body = transcode(server, query, thumbs[name])
            label = "%s %s" % (name, urllib.parse.urlencode(query))
            answers[name][urllib.parse.urlencode(query)] = (status, kind, body)
            ours = describe(body) if status == 200 else {}
            if answer["status"] != 200 or (name == "gif"):
                expected = answer["status"] != 200 and asked.get("width") == "1"
                check("%s: plexlab's %s, here the picture as it is%s" % (label, answer["status"], " (fitted)" if expected else ""),
                      status == 200 and (expected or body == made[name]), (status, kind, ours))
                continue
            theirs = dict(answer)
            if asked.get("format") in OTHER_FORMATS:
                theirs.update(type="image/jpeg", format="JPEG")
            same = (status == 200 and kind == theirs["type"] and ours.get("format") == theirs["format"]
                    and ours.get("size") == theirs["size"] and ours.get("mode") == theirs["mode"])
            if ours.get("format") == "JPEG":
                same = same and ours["subsampling"] == "4:4:4" and not ours["progressive"] and not ours["exif"]
            if same and "top" in answer:
                top, bottom = pixels(ffmpeg, body, work)
                same = near(top, answer["top"]) and near(bottom, answer["bottom"])
                ours.update(top=top, bottom=bottom)
            check("%s: %s %s %s as plexlab's" % (label, theirs["type"], theirs["format"], "x".join(map(str, theirs["size"]))), same,
                  (status, kind, ours))

        # Plex Web's grid: a 240x360 tile, a small fraction of the poster.
        status, kind, grid = answers["poster"]["width=240&height=360&minSize=1&upscale=1"]
        check("Plex Web's grid tile is a fraction of the original (plexlab: 18 KB of 632 KB)", len(grid) * 10 < len(made["poster"]),
              (len(grid), len(made["poster"])))

        # quality=: what plexlab read as one quality is one answer here, and a higher one never smaller.
        read = {}
        for answer in FIXTURE["answers"]:
            asked = dict(map(tuple, answer["query"]))
            if answer["source"] == "poster" and "quality" in asked and answer["status"] == 200:
                read[urllib.parse.urlencode([tuple(pair) for pair in answer["query"]])] = answer["quality"]
        by_quality = collections.defaultdict(set)
        for query, quality in read.items():
            by_quality[quality].add(answers["poster"][query][2])
        by_quality[75].add(grid)
        check("each quality plexlab read alike (-1, 0, 128, 256 as none; 101, 127, 255 as 100; 129 as 1) is one answer here",
              all(len(bodies) == 1 for bodies in by_quality.values()), {q: len(b) for q, b in by_quality.items()})
        sizes = [len(next(iter(by_quality[quality]))) for quality in sorted(by_quality)]
        check("a higher quality is never a smaller picture", sizes == sorted(sizes), dict(zip(sorted(by_quality), sizes)))

        # The playlist is no picture: never handed to FFmpeg, so nothing it names is dialled.
        status, kind, body = transcode(server, [("width", "240"), ("height", "360")], thumbs["playlist"])
        check("a source's 'picture' that is a playlist is answered as it is and dials nothing it names",
              status == 200 and body == made["playlist"] and listener.dialled == 0, (status, kind, body[:40], listener.dialled))

        # Kept: asked again, the same bytes without asking the source; and on disk across a restart.
        asked = len([seen for seen in source.seen if seen[1] == "/dav/_thumbs/poster"])
        status, kind, again = transcode(server, [("width", "240"), ("height", "360"), ("minSize", "1"), ("upscale", "1")], thumbs["poster"])
        check("the grid tile asked again is the same tile, kept", again == grid, (status, len(again)))
        check("the fitted tiles are kept on disk under REX_ARTWORK_CACHE_MB", len(kept(work)) > 10, kept(work)[:3])
        server.stop()
        server = Server(work, bytes_port, keys=keys)
        status, kind, again = transcode(server, [("width", "240"), ("height", "360"), ("minSize", "1"), ("upscale", "1")], thumbs["poster"])
        later = len([seen for seen in source.seen if seen[1] == "/dav/_thumbs/poster"])
        check("after a restart the tile comes off the disk, the source not asked", again == grid and later == asked,
              (status, len(again), asked, later))
    finally:
        server.stop()
        listener.socket.close()
    shutil.rmtree(work, ignore_errors=True)
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
