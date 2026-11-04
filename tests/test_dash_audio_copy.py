#!/usr/bin/env python3
"""DTS/TrueHD conversion preserves video and serves the native player's manifest.

Generated media only. The recorded DASH request is replayed through the normal
source interface; packet hashes prove that video was copied, not re-encoded.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import math
import os
import queue
import re
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import urllib.parse

import test_native_transcode as nt
from test_http_mkv import file_row


START = "/video/:/transcode/universal/start.mpd"
STOP = "/video/:/transcode/universal/stop"


class Source(nt.Zurg):
    def do_GET(self):
        if self.headers.get("Range") == "bytes=0-65535":
            try:
                entered, resume = self.server.pauses.get_nowait()
            except queue.Empty:
                pass
            else:
                entered.set()
                assert resume.wait(15), "index read was not released"
        super().do_GET()


@contextmanager
def pending_start(source, port, query):
    """Hold an index read while another request stops or replaces the session."""
    entered, resume = threading.Event(), threading.Event()
    source.pauses.put((entered, resume))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(nt.get, port, START, query)
        try:
            assert entered.wait(10), "start did not read the index"
            yield pending
        finally:
            resume.set()


def packet_hashes(path):
    """Read every video packet hash in order to detect any change to the copy."""
    out = subprocess.check_output([
        nt.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_packets",
        "-show_data_hash", "sha256", "-show_entries", "packet=data_hash", "-of", "json", path,
    ])
    return [p["data_hash"] for p in json.loads(out)["packets"]]


def decoded_hash(path, kind="v"):
    """Decode a complete track and hash its frames, failing on decoder errors."""
    # Decode the served MP4 tracks directly. Remuxing HEVC into Matroska first
    # introduces timestamp errors in the output muxer, unrelated to decoding.
    result = subprocess.run([
        nt.FFMPEG, "-v", "error", "-xerror", "-i", path, "-map", "0:%s:0" % kind,
        "-fps_mode", "passthrough", "-f", "hash", "-",
    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    assert result.returncode == 0 and not result.stderr, (path, result.returncode, result.stderr.decode(errors="replace"))
    assert re.fullmatch(rb"SHA256=[0-9a-f]{64}\s*", result.stdout), result.stdout
    return result.stdout.strip()


def run(scratch):
    items = []
    for key, codec, encoder in (("1", "h264", "libx264"), ("2", "hevc", "libx265")):
        path = os.path.join(scratch, codec + ".mkv")
        nt.MEDIA[codec + ".mkv"] = path
        subprocess.run([
            nt.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=25:d=90",
            "-f", "lavfi", "-i", "sine=d=90", "-c:v", encoder, "-preset", "ultrafast",
            "-g", "300", "-sc_threshold", "0", "-c:a", "dca" if codec == "h264" else "truehd",
            "-strict", "-2", "-shortest", path,
        ], check=True, stderr=subprocess.PIPE)
        item = file_row(key, codec + ".mkv", nt.probe(path)["streams"])
        item["duration"] = item["Media"][0]["duration"] = item["Media"][0]["Part"][0]["duration"] = 90000
        items.append(item)
    path = os.path.join(scratch, "sparse.mkv")
    nt.MEDIA["sparse.mkv"] = path
    subprocess.run([
        nt.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=36",
        "-f", "lavfi", "-i", "sine=d=36", "-c:v", "libx264", "-preset", "ultrafast",
        "-g", "1000", "-sc_threshold", "0", "-c:a", "dca", "-strict", "-2", "-shortest", path,
    ], check=True, stderr=subprocess.PIPE)
    sparse = file_row("3", "sparse.mkv", nt.probe(path)["streams"])
    sparse["duration"] = sparse["Media"][0]["duration"] = sparse["Media"][0]["Part"][0]["duration"] = 36000
    items.append(sparse)
    catalog = os.path.join(scratch, "catalog.json")
    with open(catalog, "w") as f:
        json.dump({"machineIdentifier": "0000000000000000000000000000000000000009",
                   "sections": [{"key": "1", "type": "movie", "title": "Clips", "uuid": "9" * 40}],
                   "items": items}, f)
    source = socketserver.ThreadingTCPServer(("127.0.0.1", nt.free_port()), Source)
    source.pauses = queue.Queue()
    source.daemon_threads = True
    threading.Thread(target=source.serve_forever, daemon=True).start()
    token = "dash-audio-test-token"
    port, process, log = nt.start_server(catalog, source.server_address[1], True, scratch, token=token)
    try:
        assert nt.get(port, "/library/sections")[0] == 401
        for key, codec in (("1", "h264"), ("2", "hevc")):
            session = "audio-copy-" + codec
            extra = "append-transcode-target-codec(type=videoProfile&context=streaming&protocol=dash&videoCodec=h264,hevc&audioCodec=aac)"
            query = nt.dash_query(key, session, directStream="1", directStreamAudio="0",
                                  **{"X-Plex-Client-Profile-Extra": extra, "X-Plex-Token": token})
            status, _, body = nt.get(port, "/video/:/transcode/universal/decision", query)
            assert status == 200, body
            media = json.loads(body)["MediaContainer"]["Metadata"][0]["Media"][0]
            streams = media["Part"][0]["Stream"]
            assert any(s.get("streamType") == 1 and s.get("decision") == "copy" for s in streams), media
            assert any(s.get("streamType") == 2 and s.get("decision") == "transcode" for s in streams), media
            lookup = [("session", session)]
            authenticated = lookup + [("X-Plex-Token", token)]
            # A reset connection cannot receive the manifest and must not launch FFmpeg.
            entered, resume = threading.Event(), threading.Event()
            source.pauses.put((entered, resume))
            client = socket.create_connection(("127.0.0.1", port), timeout=10)
            try:
                target = START + "?" + urllib.parse.urlencode(query)
                client.sendall(("GET %s HTTP/1.1\r\nHost: localhost\r\n\r\n" % target).encode())
                assert entered.wait(10), "start did not read the index"
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                  struct.pack("hh" if os.name == "nt" else "ii", 1, 0))
            finally:
                client.close()
                resume.set()
            def removed():
                status, _, body = nt.get(port, "/transcode/sessions", authenticated)
                assert status == 200, body
                return not json.loads(body)["MediaContainer"].get("TranscodeSession")
            nt.poll_until(removed, 10)
            assert "TRANSCODE %s run" % session not in open(log).read(), "reset start launched FFmpeg"
            # A write-side half-close still receives a manifest. An older request
            # replaced during index I/O receives 404 without stopping the winner.
            for replace in (False, True):
                entered, resume = threading.Event(), threading.Event()
                source.pauses.put((entered, resume))
                client = socket.create_connection(("127.0.0.1", port), timeout=10)
                try:
                    target = START + "?" + urllib.parse.urlencode(query)
                    client.sendall(("GET %s HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n" % target).encode())
                    assert entered.wait(10), "start did not read the index"
                    if replace:
                        status, _, winner = nt.get(port, START, query)
                        assert status == 200, winner
                    client.shutdown(socket.SHUT_WR)
                    resume.set()
                    answer = bytearray()
                    while data := client.recv(65536):
                        answer.extend(data)
                    assert answer.startswith(b"HTTP/1.1 404" if replace else b"HTTP/1.1 200"), answer
                    if not replace:
                        assert b"<MPD" in answer, answer
                finally:
                    client.close()
                    resume.set()
                if replace:
                    assert nt.get(port, START, lookup)[2] == winner, "disconnect stopped the replacement"
                    assert nt.get(port, STOP, authenticated)[0] == 200
                else:
                    assert nt.get(port, START, lookup)[0] == 200
                    assert nt.get(port, STOP, authenticated)[0] == 200
                assert nt.get(port, START, lookup)[0] == 400
            # A first start is stoppable while its source index is still being read.
            with pending_start(source, port, query) as cancelled:
                assert nt.get(port, START, lookup)[0] == 400, "unfitted manifest was exposed"
                assert nt.get(port, STOP, authenticated)[0] == 200
            assert cancelled.result()[0] == 404, "stopped start was resurrected"
            assert nt.get(port, START, lookup)[0] == 400
            # Replacing an already running session is cancellable during index I/O too.
            assert nt.get(port, START, query)[0] == 200
            with pending_start(source, port, query) as restarted:
                assert nt.get(port, START, lookup)[0] == 400, "old manifest survived replacement"
                assert nt.get(port, "/video/:/transcode/universal/session/%s/0/0.m4s" % session)[0] == 404
                assert nt.get(port, STOP, authenticated)[0] == 200
            assert restarted.result()[0] == 404, "stopped replacement was resurrected"
            assert nt.get(port, START, lookup)[0] == 400
            # An older read finishing later must not replace the newer session.
            # Give it the other codec so even its plan differs from the winner's.
            other_key = "2" if key == "1" else "1"
            other_query = [(name, "/library/metadata/" + other_key if name == "path" else value) for name, value in query]
            assert nt.get(port, "/video/:/transcode/universal/decision", other_query)[0] == 200
            with pending_start(source, port, other_query) as older:
                status, _, manifest = nt.get(port, START, query)
                assert status == 200, (status, manifest)
            assert older.result()[0] == 404, "older start replaced the newer session"
            assert status == 200 and b"<MPD" in manifest, (status, manifest)
            # A player can reopen the manifest using only the session id.
            status, _, reopened = nt.get(port, "/video/:/transcode/universal/start.mpd", lookup)
            assert status == 200 and reopened == manifest, (status, reopened)
            assert nt.get(port, "/video/:/transcode/universal/start.mpd", lookup, method="HEAD")[0] == 200
            assert nt.get(port, START, [("session", "missing")])[0] == 400
            assert nt.get(port, START, [("session", "missing")], method="HEAD")[0] == 400
            assert nt.get(port, "/video/:/transcode/universal/start.mpd", lookup, method="POST")[0] == 401
            unauthenticated = [(k, v) for k, v in query if k != "X-Plex-Token"]
            assert nt.get(port, "/video/:/transcode/universal/start.mpd", unauthenticated)[0] == 401
            length = int(re.search(rb'<SegmentTemplate timescale="1" duration="(\d+)"', manifest)[1])
            assert length > 0, length
            paths = []
            for rep in (0, 1):
                path = os.path.join(scratch, "%s-%d.mp4" % (codec, rep))
                paths.append(path)
                with open(path, "wb") as f:
                    for index in range(math.ceil(90 / length)):
                        status, _, data = nt.get(port, "/video/:/transcode/universal/session/%s/%d/%d.m4s" % (session, rep, index), timeout=45)
                        assert status == 200, (status, rep, index)
                        f.write(data)
            output = os.path.join(scratch, codec + "-out.mkv")
            subprocess.run([nt.FFMPEG, "-v", "error", "-y", "-i", paths[0], "-i", paths[1],
                            "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", output], check=True, timeout=30)
            probe = nt.probe(output)
            assert float(probe["format"]["duration"]) >= 89, probe
            assert next(s for s in probe["streams"] if s["codec_type"] == "audio")["codec_name"] == "aac", probe
            original = packet_hashes(nt.MEDIA[codec + ".mkv"])
            copied = packet_hashes(output)
            assert copied == original, "video packets changed, moved, duplicated or went missing"
            assert decoded_hash(paths[0]) == decoded_hash(nt.MEDIA[codec + ".mkv"]), "decoded video frames changed"
            decoded_hash(paths[1], "a")
            # A fresh session requests the last slot first, forcing a seek/restart.
            assert nt.get(port, START, query)[0] == 200
            for rep in (0, 1):
                status, _, data = nt.get(port, "/video/:/transcode/universal/session/%s/%d/6.m4s" % (session, rep), timeout=45)
                assert status == 200, (status, rep)
                seek = os.path.join(scratch, "seek-%d.mp4" % rep)
                with open(seek, "wb") as f:
                    f.write(data)
                packets = json.loads(subprocess.check_output([
                    nt.FFPROBE, "-v", "error", "-show_packets", "-show_entries", "packet=pts_time",
                    "-of", "json", seek,
                ]))["packets"]
                times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
                # Input seeking starts at the preceding keyframe. Codec delay can put
                # the last packet just before the slot boundary; the next slot follows.
                target = 6 * length
                assert times and target - length - 0.5 <= min(times) <= target + 0.5 and max(times) >= target - 0.5, (min(times), max(times), length)
                decoded_hash(seek, "v" if rep == 0 else "a")
            nt.get(port, "/video/:/transcode/universal/stop", lookup + [("X-Plex-Token", token)])
            assert nt.get(port, START, lookup)[0] == 400
            assert nt.get(port, START, lookup, method="HEAD")[0] == 400
            print(codec + ": copied packets, converted audio, full playback and seek passed")
        # A sparse index must fail before launch rather than creating one title-sized
        # chunk or falling back to video conversion on an audio-only setup.
        query = nt.dash_query("3", "sparse-index", directStream="1", directStreamAudio="0",
                              **{"X-Plex-Client-Profile-Extra": extra, "X-Plex-Token": token})
        assert nt.get(port, "/video/:/transcode/universal/decision", query)[0] == 200
        status, _, body = nt.get(port, START, query)
        assert status == 400, (status, body)
        assert "indexed video gap exceeds the 20 s DASH copy limit" in open(log).read()
        assert nt.get(port, START, [("session", "sparse-index")])[0] == 400
        assert "TRANSCODE sparse-index run" not in open(log).read(), "oversized gap launched FFmpeg"
        # The refusal releases the session id for a normal file.
        query = [(k, "/library/metadata/1" if k == "path" else v) for k, v in query]
        assert nt.get(port, "/video/:/transcode/universal/decision", query)[0] == 200
        assert nt.get(port, START, query)[0] == 200
        assert nt.get(port, STOP, [("session", "sparse-index"), ("X-Plex-Token", token)])[0] == 200
        print("sparse index: refused before launch; session id remains reusable")
        # PMS returns an HLS session's master playlist even through start.mpd.
        session = "hls-reopen"
        query = nt.plex_web_query("1", session, **{"X-Plex-Token": token, "X-Plex-Incomplete-Segments": "1"})
        assert nt.get(port, "/video/:/transcode/universal/decision", query)[0] == 200
        status, headers, master = nt.get(port, "/video/:/transcode/universal/start.m3u8", query)
        assert status == 200 and headers.get("Content-Type") == "application/vnd.apple.mpegurl", (status, headers, master)
        suffix = b"?X-Plex-Incomplete-Segments=1"
        assert master.startswith(b"#EXTM3U\n") and master.endswith(b"session/hls-reopen/base/index.m3u8" + suffix + b"\n"), master
        # The current lookup controls the suffix, independently of the start.
        for incomplete in (False, True, False):
            lookup = [("session", session)] + ([("X-Plex-Incomplete-Segments", "1")] if incomplete else [])
            status, headers, reopened = nt.get(port, START, lookup)
            expected = master if incomplete else master.replace(suffix, b"")
            assert status == 200 and headers.get("Content-Type") == "application/vnd.apple.mpegurl" and reopened == expected, (status, headers, reopened)
            status, headers, body = nt.get(port, START, lookup, method="HEAD")
            assert status == 200 and headers.get("Content-Type") == "application/vnd.apple.mpegurl" and not body, (status, headers, body)
        assert nt.get(port, STOP, [("session", session), ("X-Plex-Token", token)])[0] == 200
        print("hls: tokenless manifest reopen and current lookup flags passed")
        assert "converting it from segment" not in open(log).read(), open(log).read()
        assert "seek to segment 6" in open(log).read(), "test did not exercise a seek"
    except Exception:
        print(open(log).read())
        raise
    finally:
        process.terminate()
        process.wait()
        source.shutdown()
        source.server_close()


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="rex-dash-audio-") as scratch:
        run(scratch)
