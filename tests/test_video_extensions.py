#!/usr/bin/env python3
"""The video files a source can push, by extension, as a real PMS takes them.

`fixtures/video_extensions_s1422.json` is plexscratch42 (1.42.2) scanning one small file per
extension its legacy scanner lists, and a few it does not, with its Plex Movie and Plex TV Series
scanners (2026-11-04). A file is a video when a scanner made a Part of it with a stream ffprobe
names (`fixtures/media/s42-*`, the probe of each Part): those are pushed and must become rows with
the Media's container and the part read's Content-Type that PMS answered. The rest -- no Part (an
.iso, an .ifo, an .f4v), a Part with no stream (the playlists, a DVD's .bup) or one whose video
nothing decodes (.viv) -- are rejected as no video. `.mk3d` is neither scanner's, and is pushed as
the Matroska it is, with a production zurg's probe of a real one.

With an FFmpeg (`REX_TRANSCODER`, else PATH's), a clip of each container FFmpeg writes is pushed
too and asked for Plex Web's own conversion (`fixtures/transcode/scratch_copy_mkv.json`,
`directPlay=0`), which must end in a first segment that decodes: the formats no client direct
plays are played through the transcoder or not at all.

    python3 tests/test_video_extensions.py
"""
import glob
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from server_command import test_ffmpeg
from test_feed import BYTES_TOKEN, MOUNT, Bytes, Server, catalog, check, results, serve
from test_native_transcode import plex_web_query

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURED = json.load(open(os.path.join(HERE, "fixtures", "video_extensions_s1422.json")))
MEDIA = {os.path.basename(path)[:-5]: json.load(open(path))
         for path in glob.glob(os.path.join(HERE, "fixtures", "media", "s42-*.json"))}


def named(probe):
    """Every video stream of the probe has a codec ffprobe names (a .viv's has none)."""
    video = [stream for stream in probe["streams"] if stream.get("codec_type") == "video"]
    return bool(video) and all(stream.get("codec_name") for stream in video)


def videos():
    """Each extension a scanner made a playable Part of: its media fixture and the read PMS answered."""
    out = {}
    for name, fixture in MEDIA.items():  # cycle 54's seventeen Parts and this measurement's
        if named(fixture["probe"]):
            out.setdefault(fixture["extension"].lower(), {"media": fixture})
    for scanner in ("Plex Movie", "Plex TV Series"):
        for extension, entry in MEASURED[scanner].items():
            if entry.get("media") in MEDIA and extension.lower() in out:
                out[extension.lower()]["contentType"] = entry["contentType"]
    return out


def refused():
    """The extensions neither scanner made a playable Part of."""
    taken = set(videos()) | {"mk3d"}
    out = set()
    for scanner in ("Plex Movie", "Plex TV Series"):
        for extension, entry in MEASURED[scanner].items():
            extension = extension.rsplit(".", 1)[-1].lower()
            if extension not in taken and "/" not in extension and "_" not in extension:
                out.add(extension)
    return sorted(out)


def read(server, key):
    request = urllib.request.Request("http://127.0.0.1:%d%s" % (server.port, key),
                                     headers={"X-Plex-Token": server.token, "Range": "bytes=0-9", "Accept": "*/*"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.headers.get("Content-Type")
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type")


def pushed(path, probe, size):
    return {"id": path, "path": path, "file": "%s/%s" % (MOUNT, path), "release": path.rsplit("/", 1)[0],
            "size": size, "media_info": probe}


# Each container FFmpeg has a muxer for, written as the lab host wrote its probe of it.
VIDEO, SD = ["-c:v", "libx264", "-preset", "veryfast"], ["-s", "720x576", "-r", "25"]
CLIPS = {
    "3g2": ["-c:v", "mpeg4", "-c:a", "aac", "-ac", "1", "-f", "3g2"],
    "3gp": ["-c:v", "mpeg4", "-c:a", "aac", "-ac", "1", "-f", "3gp"],
    "asf": ["-c:v", "wmv2", "-c:a", "wmav2", "-f", "asf"],
    "avc": VIDEO + ["-an", "-f", "h264"],
    "bivx": ["-c:v", "mpeg4", "-vtag", "DX50", "-c:a", "libmp3lame", "-f", "avi"],
    "dv": SD + ["-c:v", "dvvideo", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "-ac", "2", "-f", "dv"],
    "dvr-ms": ["-c:v", "wmv2", "-c:a", "wmav2", "-f", "asf"],
    "evo": ["-c:v", "mpeg2video", "-c:a", "ac3", "-f", "vob"],
    "m2t": ["-c:v", "mpeg2video", "-c:a", "mp2", "-f", "mpegts"],
    "m2v": ["-c:v", "mpeg2video", "-an", "-f", "mpeg2video"],
    "mk3d": VIDEO + ["-c:a", "aac", "-metadata:s:v:0", "stereo_mode=side_by_side_left", "-f", "matroska"],
    "ogm": ["-c:v", "libtheora", "-c:a", "libvorbis", "-f", "ogg"],
    "ogv": ["-c:v", "libtheora", "-c:a", "libvorbis", "-f", "ogg"],
    "qt": VIDEO + ["-c:a", "aac", "-f", "mov"],
    "rm": ["-c:v", "rv20", "-c:a", "ac3", "-f", "rm"],
    "rmvb": ["-c:v", "rv20", "-c:a", "ac3", "-f", "rm"],
    "tp": ["-c:v", "mpeg2video", "-c:a", "ac3", "-f", "mpegts"],
    "vdr": SD + ["-c:v", "mpeg2video", "-c:a", "mp2", "-f", "vob"],
    "vob": SD + ["-c:v", "mpeg2video", "-c:a", "ac3", "-f", "vob"],
    "wtv": SD + ["-c:v", "mpeg2video", "-c:a", "mp2", "-f", "wtv"],
    "xvid": ["-c:v", "mpeg4", "-vtag", "XVID", "-c:a", "libmp3lame", "-f", "avi"],
}


def files_server(files):
    """A source's byte server over real files: `files` maps a request path to a file on disk."""
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            path = files.get(urllib.parse.unquote(self.path))
            if self.headers.get("Authorization") != "Bearer " + BYTES_TOKEN or not path:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            data = open(path, "rb").read()
            first, last = 0, len(data) - 1
            spec = self.headers.get("Range") or ""
            if spec.startswith("bytes="):
                start, _, end = spec[6:].partition("-")
                first, last = int(start or 0), min(int(end) if end else len(data) - 1, len(data) - 1)
            self.send_response(206 if spec else 200)
            self.send_header("Content-Length", str(last - first + 1))
            if spec:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (first, last, len(data)))
            self.end_headers()
            if self.command != "HEAD":
                try:
                    self.wfile.write(data[first:last + 1])
                except (BrokenPipeError, ConnectionResetError):
                    pass  # FFmpeg and rex close a read once they have what they asked for

        do_HEAD = do_GET
    return Handler


def conversions(ffmpeg, scratch):
    ffprobe = os.path.join(os.path.dirname(ffmpeg), "ffprobe")
    ffprobe = ffprobe if os.path.exists(ffprobe) else shutil.which("ffprobe")
    files, probes = {}, {}
    for extension, args in sorted(CLIPS.items()):
        local = os.path.join(scratch, "clip.%s" % extension)
        made = subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=24:d=8",
                               "-f", "lavfi", "-i", "sine=f=440:d=8:sample_rate=48000", *args, "-shortest", local],
                              capture_output=True, text=True)
        if made.returncode:
            print("SKIP  .%s: this FFmpeg writes no such clip: %s" % (extension, made.stderr.strip()[-160:]))
            continue
        path = "movies/Converted %s/Converted %s.%s" % (extension, extension, extension)
        files["/dav/" + path] = local
        probes[path] = (extension, json.loads(subprocess.run(
            [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", local],
            capture_output=True, text=True, check=True).stdout))
    work = os.path.join(scratch, "rex")
    os.makedirs(work)
    catalog(work)
    server = Server(work, serve(files_server(files)), keys={"REX_TRANSCODER": ffmpeg})
    try:
        status, answer = server.feed("POST", "items", {"items": [
            pushed(path, probe, os.path.getsize(files["/dav/" + path])) for path, (_, probe) in probes.items()]})
        check("FFmpeg's clips are all videos", status == 200 and not (answer or {}).get("rejected"), (status, answer))
        keys = server.keys()
        for path, (extension, _) in sorted(probes.items()):
            key, session = keys.get(path), "videoext-%s" % extension
            query = plex_web_query(key, session) + [("X-Plex-Token", server.token)]
            status, body = server.request("/video/:/transcode/universal/decision?" + urllib.parse.urlencode(query))
            container = (body or {}).get("MediaContainer") or {} if status == 200 else {}
            check(".%s: Plex Web's request converts (1001)" % extension,
                  container.get("generalDecisionCode") == 1001 and container.get("transcodeDecisionCode") == 1001,
                  (status, {k: v for k, v in container.items() if "Decision" in k}))
            server.request("/video/:/transcode/universal/start.m3u8?" + urllib.parse.urlencode(query))
            base = "/video/:/transcode/universal/session/%s/base/" % session
            status, playlist = server.request(base + "index.m3u8?X-Plex-Token=" + server.token)
            status, segment = server.request(base + "00000.ts?X-Plex-Token=" + server.token)
            local = os.path.join(scratch, "%s-0.ts" % extension)
            open(local, "wb").write(segment if isinstance(segment, bytes) else b"")
            decoded = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-of", "json", local],
                                     capture_output=True, text=True)
            codecs = sorted(stream.get("codec_name") for stream in json.loads(decoded.stdout or "{}").get("streams", []))
            check(".%s: its first segment decodes as h264" % extension, status == 200 and "h264" in codecs,
                  (status, codecs, decoded.stderr[-200:]))
            server.request("/video/:/transcode/universal/stop?session=%s&X-Plex-Token=%s" % (session, server.token))
    finally:
        server.stop()


def main():
    source = Bytes()
    bytes_port = serve(source.handler())
    work = tempfile.mkdtemp(prefix="rex-video-extensions-")
    catalog(work)
    server = Server(work, bytes_port)
    try:
        wanted = videos()
        items = {}
        for extension, entry in sorted(wanted.items()):
            media = entry["media"]
            path = "movies/Videoext %s/Videoext %s.%s" % (extension, extension, extension)
            items[path] = (extension, media["Media"]["container"], entry.get("contentType"),
                           media["Media"]["Part"][0]["key"].rsplit("/", 1)[-1])
            source.files.add("/dav/" + path)
            body = pushed(path, media["probe"], media["Media"]["Part"][0]["size"])
            status, answer = server.feed("POST", "items", {"items": [body]})
            check("a .%s file is a video, as a real PMS makes a Part of one" % extension,
                  status == 200 and not (answer or {}).get("rejected"), (status, answer))
        mk3d = MEASURED["mk3d"]
        path = "movies/%s/%s" % (mk3d["release"], mk3d["file"])
        items[path] = ("mk3d", "mkv", "application/octet-stream", None)
        source.files.add("/dav/" + path)
        status, answer = server.feed("POST", "items", {"items": [pushed(path, mk3d["probe"], mk3d["size"])]})
        check("a .mk3d file is a video, the Matroska it is", status == 200 and not (answer or {}).get("rejected"),
              (status, answer))
        # A DVD's title VOB in its VIDEO_TS folder, upper case as a disc names it: the TV scanner's Part.
        dvd = MEDIA[MEASURED["Plex TV Series"]["vob"]["media"]]
        path = "shows/Videoext Dvdshow/Season 01/VIDEO_TS/VTS_01_1.VOB"
        items[path] = ("VOB", dvd["Media"]["container"], MEASURED["Plex TV Series"]["VIDEO_TS/VTS_01_1.VOB"]["contentType"], None)
        source.files.add("/dav/" + path)
        status, answer = server.feed("POST", "items", {"items": [pushed(path, dvd["probe"], dvd["Media"]["Part"][0]["size"])]})
        check("a VIDEO_TS folder's VTS_01_1.VOB is a video", status == 200 and not (answer or {}).get("rejected"),
              (status, answer))

        keys = server.keys()
        for path, (extension, container, content_type, key_name) in sorted(items.items(), key=lambda pair: pair[1][0]):
            row = server.row(keys.get(path))
            media = (row.get("Media") or [{}])[0]
            part = (media.get("Part") or [{}])[0]
            check(".%s is the container %s PMS names" % (extension, container),
                  media.get("container") == container and part.get("container") == container,
                  (media.get("container"), part.get("container")))
            if key_name:  # PMS keeps the file's own extension in the Part key, `.dvr-ms` too
                check(".%s's Part key ends in %s, as PMS's does" % (extension, key_name),
                      (part.get("key") or "").endswith("/" + key_name), part.get("key"))
            if content_type:
                answer = read(server, part.get("key", "/none"))
                check(".%s is read as %s" % (extension, content_type), answer == (206, content_type), answer)

        for extension in refused():
            path = "movies/Videoext %s/Videoext %s.%s" % (extension, extension, extension)
            status, answer = server.feed("POST", "items", {"items": [pushed(path, MEDIA["s42-1099-0"]["probe"], 1000)]})
            check("a .%s file is no video, as no scanner makes a playable Part of one" % extension,
                  status == 200 and (answer or {}).get("rejected") == [{"id": path, "reason": "not a video file"}],
                  (status, answer))
        check("the measurement names what it refuses", {"iso", "bup", "ifo", "asx", "sdp", "strm", "xsp", "viv", "f4v",
                                                         "mp4v"} <= set(refused()), refused())
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)
    ffmpeg = test_ffmpeg()
    if ffmpeg:
        scratch = tempfile.mkdtemp(prefix="rex-video-conversions-")
        try:
            conversions(ffmpeg, scratch)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
    else:
        print("SKIP  conversions: no FFmpeg (REX_TRANSCODER or PATH)")
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
