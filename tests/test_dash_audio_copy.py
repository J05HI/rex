#!/usr/bin/env python3
"""DTS/TrueHD conversion preserves video and serves the native player's manifest.

Generated media only. The recorded DASH request is replayed through the normal
source interface; packet hashes prove that video was copied, not re-encoded.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
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
import time
import urllib.parse
import xml.etree.ElementTree as ET

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
        # `tail_gate`: (file, byte offset, entered, resume) slows a read of that file to a crawl
        # from that byte until released (a source that stops dead makes rex give the read up and
        # FFmpeg end, which frees every chunk), so a check can ask what rex serves meanwhile.
        gate = getattr(self.server, "tail_gate", None)
        name = urllib.parse.unquote(self.path).rsplit("/", 1)[-1]
        bounds = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        first = int(bounds[1]) if bounds else 0
        last = int(bounds[2]) if bounds and bounds[2] else os.path.getsize(nt.MEDIA[name]) - 1
        writer = self.wfile
        if gate and name == gate[0] and first < gate[1] <= last:
            _, cutoff, entered, resume = gate

            class GatedWriter:
                header = True
                position = first

                def write(self, data):
                    if self.header:
                        self.header = False
                        return writer.write(data)
                    at = 0
                    if self.position < cutoff:
                        at = min(len(data), cutoff - self.position)
                        writer.write(data[:at])
                    while at < len(data):
                        entered.set()
                        if resume.is_set():
                            writer.write(data[at:])
                            break
                        writer.write(data[at:at + 16384])
                        at += 16384
                        time.sleep(0.5)
                    self.position += len(data)
                    return len(data)

                def __getattr__(self, attribute):
                    return getattr(writer, attribute)

            self.wfile = GatedWriter()
        try:
            super().do_GET()
        finally:
            self.wfile = writer


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


FIXTURE = json.load(open(os.path.join(nt.ROOT, "tests", "fixtures", "transcode", "dash_manifest_session.json")))["exchanges"]


def recorded(method, running):
    """What PMS 1.43.4 answered a session-only start.mpd: (status, content type) for a running
    DASH session or an unknown id, by method."""
    for exchange in FIXTURE:
        query = dict(exchange["query"])
        if exchange["path"].endswith("start.mpd") and set(query) == {"session"} and exchange["method"] == method:
            if running == (query["session"] == "manifest-reopen-witness" and exchange["status"] == 200):
                return exchange["status"], exchange["headers"]["content-type"]
    raise AssertionError((method, running))
@contextmanager
def raw_start(source, port, query, close_header=False):
    """Connect and send a start request, pausing on index read."""
    entered, resume = threading.Event(), threading.Event()
    source.pauses.put((entered, resume))
    client = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        hdr = "Connection: close\r\n" if close_header else ""
        target = START + "?" + urllib.parse.urlencode(query)
        client.sendall(f"GET {target} HTTP/1.1\r\nHost: localhost\r\n{hdr}\r\n".encode())
        assert entered.wait(10), "start did not read the index"
        yield client, resume
    finally:
        client.close()
        resume.set()


def packet_hashes(path):
    """Read every video packet hash in order to detect any change to the copy."""
    return [p["data_hash"] for p in packet_entries(path, "data_hash", stream="v:0", extra=("-show_data_hash", "sha256"))]


def packet_entries(path, entries, stream=None, extra=()):
    """Read packet metadata entries via ffprobe."""
    cmd = [nt.FFPROBE, "-v", "error"]
    if stream:
        cmd += ["-select_streams", stream]
    cmd += ["-show_packets", *extra, "-show_entries", "packet=" + entries, "-of", "json", path]
    return json.loads(subprocess.check_output(cmd))["packets"]


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
    def make_media(key, name, dur_ms, args):
        path = os.path.join(scratch, name)
        nt.MEDIA[name] = path
        subprocess.run([nt.FFMPEG, "-v", "error", "-y", *args, path], check=True, stderr=subprocess.PIPE)
        item = file_row(key, name, nt.probe(path)["streams"])
        item["duration"] = item["Media"][0]["duration"] = item["Media"][0]["Part"][0]["duration"] = dur_ms
        return item

    items = [
        make_media(key, codec + ".mkv", 90000, [
            "-f", "lavfi", "-i", "testsrc2=s=320x180:r=%s:d=90" % ("30000/1001" if codec == "dense" else "24000/1001" if codec == "uneven" else "25"), "-f", "lavfi", "-i", "sine=d=90",
            "-c:v", enc, "-preset", "ultrafast", "-g", str(gop), "-sc_threshold", "0",
            *(["-force_key_frames", "expr:gte(t,n_forced*1.002)", "-bf", "2"] if codec == "uneven" else []),
            "-c:a", "truehd" if codec in ("hevc", "uneven") else "dca", "-strict", "-2", "-shortest",
        ])
        for key, codec, enc, gop in (("1", "h264", "libx264", 300), ("2", "hevc", "libx265", 300),
                                     ("5", "dense", "libx264", 30), ("6", "uneven", "libx264", 250))
    ]
    items.append(make_media("3", "sparse.mkv", 36000, [
        "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=36", "-f", "lavfi", "-i", "sine=d=36",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "1000", "-sc_threshold", "0",
        "-c:a", "dca", "-strict", "-2", "-shortest",
    ]))
    forces_496 = ",".join(f"{4.96 + 5.0 * i:.2f}" for i in range(18))
    items.append(make_media("7", "shift-496.mkv", 90000, [
        "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=90", "-f", "lavfi", "-i", "sine=d=90",
        "-c:v", "libx264", "-preset", "ultrafast", "-g", "1000", "-force_key_frames", forces_496,
        "-sc_threshold", "0", "-c:a", "dca", "-strict", "-2", "-shortest",
    ]))
    for key, name, delay in (("8", "late-video", 0.48), ("9", "late-video-long", 1.2)):
        forces_492 = ",".join(f"{delay + 4.92 * (i + 1):.2f}" for i in range(18))
        items.append(make_media(key, name + ".mkv", 90000, [
            "-f", "lavfi", "-i", "testsrc2=s=160x90:r=25:d=90", "-f", "lavfi", "-i", "sine=d=90",
            "-vf", "setpts=PTS+%s/TB" % delay,
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "1000", "-force_key_frames", forces_492,
            "-sc_threshold", "0", "-c:a", "dca", "-strict", "-2", "-shortest",
        ]))
    # A timestamp discontinuity: the video
    # resumes a minute later while audio continues on the original clock.
    items.append(make_media("4", "timestamp-gap.mkv", 91000, [
        "-f", "lavfi", "-i", "testsrc2=s=160x90:r=24:d=30", "-f", "lavfi", "-i", "sine=d=91",
        "-vf", "setpts=PTS+60.703/TB*gte(T\\,20)", "-fps_mode", "passthrough", "-c:v", "libx264",
        "-preset", "medium", "-g", "24", "-bf", "2", "-sc_threshold", "0", "-c:a", "dca", "-strict", "-2",
    ]))
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
        for key, codec in (("1", "h264"), ("2", "hevc"), ("5", "dense"), ("6", "uneven"),
                           ("7", "shift-496"), ("8", "late-video"), ("9", "late-video-long")):
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
            with raw_start(source, port, query) as (client, _):
                client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                  struct.pack("hh" if os.name == "nt" else "ii", 1, 0))
            def removed():
                status, _, body = nt.get(port, "/transcode/sessions", authenticated)
                assert status == 200, body
                return not json.loads(body)["MediaContainer"].get("TranscodeSession")
            nt.poll_until(removed, 10)
            # A write-side half-close still receives a manifest. An older request
            # replaced during index I/O receives 404 without stopping the winner.
            for replace in (False, True):
                with raw_start(source, port, query, close_header=True) as (client, resume):
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
                if replace:
                    assert nt.get(port, START, lookup)[2] == winner, "disconnect stopped the replacement"
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
            if codec == "h264":
                # A fitted chunk is served as it closes, not once the next one has closed too: hold
                # the source at 30 s, between the first cut (24 s, the keyframe after the 13 s slot)
                # and the second (36 s), and ask for segment 0 meanwhile.
                packets = json.loads(subprocess.check_output([nt.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_packets",
                                                              "-show_entries", "packet=pts_time,pos", "-of", "json", nt.MEDIA["h264.mkv"]]))["packets"]
                gate_entered, gate_resume = threading.Event(), threading.Event()
                source.tail_gate = ("h264.mkv", next(int(p["pos"]) for p in packets if float(p["pts_time"]) >= 30), gate_entered, gate_resume)
            with pending_start(source, port, other_query) as older:
                status, _, manifest = nt.get(port, START, query)
                assert status == 200, (status, manifest)
            assert older.result()[0] == 404, "older start replaced the newer session"
            if codec == "h264":
                asked = time.monotonic()
                status, _, data = nt.get(port, "/video/:/transcode/universal/session/%s/0/0.m4s" % session, timeout=45)
                waited = time.monotonic() - asked
                assert status == 200 and waited < 20 and gate_entered.is_set() and not gate_resume.is_set(), (status, round(waited, 1), gate_entered.is_set(), "segment 0 waited for the chunk after it")
                assert b"moof" in data, "segment 0 is not a fragment"
                gate_resume.set()
                source.tail_gate = None
            assert status == 200 and b"<MPD" in manifest, (status, manifest)
            # A player can reopen the manifest using only the session id: each answer as PMS 1.43.4
            # recorded it (dash_manifest_session.json), for a running session and an unknown one.
            status, headers, reopened = nt.get(port, "/video/:/transcode/universal/start.mpd", lookup)
            assert reopened == manifest, (status, reopened)
            for method, running in (("GET", True), ("HEAD", True), ("GET", False), ("HEAD", False)):
                want = recorded(method, running)
                status, headers, body = nt.get(port, START, lookup if running else [("session", "missing")], method=method)
                assert (status, headers.get("Content-Type")) == want, (method, running, status, headers.get("Content-Type"), want)
            assert nt.get(port, "/video/:/transcode/universal/start.mpd", lookup, method="POST")[0] == 401
            unauthenticated = [(k, v) for k, v in query if k != "X-Plex-Token"]
            assert nt.get(port, "/video/:/transcode/universal/start.mpd", unauthenticated)[0] == 401
            template = ET.fromstring(manifest).find(".//{urn:mpeg:dash:schema:mpd:2011}SegmentTemplate")
            if codec in ("uneven", "shift-496", "late-video", "late-video-long"):
                timeline = template.find("{urn:mpeg:dash:schema:mpd:2011}SegmentTimeline")
                assert timeline is not None, manifest
                durations = [int(entry.attrib["d"]) / 1000 for entry in timeline]
                starts = [sum(durations[:index]) for index in range(len(durations))]
            else:
                length = int(template.attrib["duration"]) / int(template.attrib["timescale"])
                assert length == (1.001 if codec == "dense" else 12), length
                starts = [index * length for index in range(math.ceil(90 / length))]
            paths = []
            for rep in (0, 1):
                path = os.path.join(scratch, "%s-%d.mp4" % (codec, rep))
                paths.append(path)
                prev_end = None
                chunk0_bytes = None
                with open(path, "wb") as f:
                    for index in range(len(starts)):
                        status, _, data = nt.get(port, "/video/:/transcode/universal/session/%s/%d/%d.m4s" % (session, rep, index), timeout=45)
                        assert status == 200, (status, rep, index)
                        if index == 0:
                            chunk0_bytes = data
                        f.write(data)
                        if codec in ("uneven", "shift-496", "late-video", "late-video-long"):
                            mfhd = data.index(b"mfhd")
                            assert int.from_bytes(data[mfhd + 8:mfhd + 12], "big") == index + 1, "fragment sequence restarted"
                        if codec in ("uneven", "shift-496", "late-video", "late-video-long"):
                            chunk_file = os.path.join(scratch, "chunk-check.m4s")
                            with open(chunk_file, "wb") as cf:
                                cf.write(data)
                            pkts = packet_entries(chunk_file, "pts_time,duration_time,flags")
                            first = float(pkts[0]["pts_time"])
                            cue = starts[index]
                            if rep == 0 and index > 0:
                                assert abs(first - cue) < 0.125, (codec, index, first, cue)
                            if rep == 1:
                                last = float(pkts[-1]["pts_time"])
                                last_dur = float(pkts[-1]["duration_time"])
                                if index > 0:
                                    assert first >= cue - last_dur, (index, first, cue, last_dur)
                                    assert abs(first - prev_end) < 0.001, (index, first, prev_end)
                                prev_end = last + last_dur
                # Native players read each segment in small ranges. Reuse the same
                # socket across both tracks, as measured on PMS 1.43.5.
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=45)
                segment = "/video/:/transcode/universal/session/%s/%d/0.m4s" % (session, rep)
                connection.request("GET", segment, headers={"Range": "bytes=0-499"})
                response = connection.getresponse()
                assert response.status == 206 and response.getheader("Connection") == "Keep-Alive"
                first = response.read()
                transport = connection.sock
                assert transport is not None
                connection.request("GET", segment, headers={"Range": "bytes=500-999"})
                response = connection.getresponse()
                assert response.status == 206
                second = response.read()
                assert connection.sock is transport, "segment ranges reconnected"
                assert first + second == open(path, "rb").read(1000)
                total_chunk = len(chunk0_bytes)
                # HEAD on segment: 200, Content-Length, empty body
                connection.request("HEAD", segment)
                resp_head = connection.getresponse()
                assert resp_head.status == 200
                assert int(resp_head.getheader("Content-Length")) == total_chunk
                assert resp_head.read() == b""
                assert connection.sock is transport, "HEAD broke transport"
                # Boundary range: single first byte
                connection.request("GET", segment, headers={"Range": "bytes=0-0"})
                resp_single = connection.getresponse()
                assert resp_single.status == 206
                assert resp_single.getheader("Content-Range") == f"bytes 0-0/{total_chunk}"
                assert resp_single.read() == chunk0_bytes[:1]
                assert connection.sock is transport
                # Boundary range: suffix range (last 500 bytes)
                connection.request("GET", segment, headers={"Range": "bytes=-500"})
                resp_suffix = connection.getresponse()
                assert resp_suffix.status == 206
                assert resp_suffix.getheader("Content-Range") == f"bytes {total_chunk - 500}-{total_chunk - 1}/{total_chunk}"
                assert resp_suffix.read() == chunk0_bytes[-500:]
                assert connection.sock is transport
                # Out-of-bounds range returns 416
                connection.request("GET", segment, headers={"Range": f"bytes={total_chunk}-{total_chunk + 100}"})
                resp_416 = connection.getresponse()
                assert resp_416.status == 416
                assert resp_416.read()
                assert connection.sock is transport, "416 broke transport"
                connection.request("GET", segment, headers={"Range": "bytes=0-499", "Connection": "close"})
                response = connection.getresponse()
                assert response.getheader("Connection") == "close" and response.read() == first
                connection.close()
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
                packets = packet_entries(seek, "pts_time")
                times = [float(p["pts_time"]) for p in packets if "pts_time" in p]
                # Input seeking starts at the preceding keyframe. Codec delay can put
                # the last packet just before the slot boundary; the next slot follows.
                target = starts[6]
                length = starts[6] - starts[5]
                assert times and target - length - 0.5 <= min(times) <= target + 0.5 and max(times) >= target - 0.5, (min(times), max(times), length)
                decoded_hash(seek, "v" if rep == 0 else "a")
            nt.get(port, "/video/:/transcode/universal/stop", lookup + [("X-Plex-Token", token)])
            assert nt.get(port, START, lookup)[0] == 400
            assert nt.get(port, START, lookup, method="HEAD")[0] == 400
            print(codec + ": copied packets, converted audio, full playback and seek passed")
        for key, name in (("3", "sparse"), ("4", "timestamp-gap")):
            tail_entered, tail_resume = threading.Event(), threading.Event()
            if name == "timestamp-gap":
                packets = packet_entries(nt.MEDIA[name + ".mkv"], "pts_time,pos", stream="v:0")
                cutoff = next(int(packet["pos"]) for packet in packets if float(packet["pts_time"]) >= 80)
                source.tail_gate = ("timestamp-gap.mkv", cutoff, tail_entered, tail_resume)
            session = "long-gap-" + name
            query = nt.dash_query(key, session, directStream="1", directStreamAudio="0",
                                  **{"X-Plex-Client-Profile-Extra": extra, "X-Plex-Token": token})
            assert nt.get(port, "/video/:/transcode/universal/decision", query)[0] == 200
            status, _, manifest = nt.get(port, START, query)
            assert status == 200, (status, manifest)
            timeline = ET.fromstring(manifest).findall(".//{urn:mpeg:dash:schema:mpd:2011}SegmentTimeline")[0]
            slots = [(int(entry.attrib["t"]), int(entry.attrib["d"])) for entry in timeline]
            assert max(length for _, length in slots) > 20_000 and b'minBufferTime="PT2S"' in manifest, manifest
            if name == "timestamp-gap":
                assert slots[0][0] == 0 and 5000 <= slots[0][1] <= 5100, slots
                assert len(slots) > 2 and slots[-2][0] >= 80_000, slots
            first_started = time.monotonic()
            paths = []
            for rep in (0, 1):
                path = os.path.join(scratch, "%s-%d.mp4" % (name, rep))
                paths.append(path)
                with open(path, "wb") as f:
                    for index in range(len(slots)):
                        status, _, data = nt.get(port, "/video/:/transcode/universal/session/%s/%d/%d.m4s" % (session, rep, index), timeout=45)
                        assert status == 200, (name, status, rep, index)
                        f.write(data)
                        if rep == 0 and index == 0:
                            print("%s: first segment in %.3fs, media span %.3fs" % (name, time.monotonic() - first_started, slots[0][1] / 1000))
                            if name == "timestamp-gap":
                                assert tail_entered.wait(5), "tail gate was not exercised"
                                assert not tail_resume.is_set(), "tail was released before the opening segment"
                                tail_resume.set()
                                source.tail_gate = None
            assert packet_hashes(paths[0]) == packet_hashes(nt.MEDIA[name + ".mkv"]), "long gap changed video packets"
            assert decoded_hash(paths[0]) == decoded_hash(nt.MEDIA[name + ".mkv"]), "long gap changed video frames"
            assert nt.probe(paths[1])["streams"][0]["codec_name"] == "aac"
            decoded_hash(paths[1], "a")
            # A fresh run seeks directly to the video after the discontinuity.
            if name == "timestamp-gap":
                # Read Rex's public MPD with a DASH client, not only joined files.
                url = "http://127.0.0.1:%d%s?session=%s" % (port, START, session)
                dash_client = os.environ.get("REX_DASH_CLIENT", nt.FFMPEG)
                demuxers = subprocess.check_output([dash_client, "-hide_banner", "-demuxers"], stderr=subprocess.DEVNULL)
                if re.search(rb"\bD\s+dash\s", demuxers):
                    played = subprocess.run([
                        dash_client, "-v", "error", "-xerror", "-i", url,
                        "-map", "0:v:0", "-map", "0:a:0", "-fps_mode", "passthrough", "-f", "null", "-",
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
                    assert played.returncode == 0, played.stderr.decode(errors="replace")
                    print("timestamp-gap: public DASH manifest decoded by FFmpeg")
                else:
                    print("SKIP public MPD reader: FFmpeg has no DASH demuxer (set REX_DASH_CLIENT)")
                assert nt.get(port, START, query)[0] == 200
                index = next(index for index, (begin, _) in enumerate(slots) if begin >= 80_000)
                for rep in (0, 1):
                    status, _, data = nt.get(port, "/video/:/transcode/universal/session/%s/%d/%d.m4s" % (session, rep, index), timeout=45)
                    assert status == 200, (status, data[:100])
                    seek = os.path.join(scratch, "timestamp-gap-seek-%d.mp4" % rep)
                    with open(seek, "wb") as f:
                        f.write(data)
                    decoded_hash(seek, "v" if rep == 0 else "a")
                    packets = packet_entries(seek, "pts_time")
                    first = min(float(packet["pts_time"]) for packet in packets if "pts_time" in packet)
                    assert abs(first - slots[index][0] / 1000) < 0.2, (rep, first, slots[index])
                if re.search(rb"\bD\s+dash\s", demuxers):
                    sought = subprocess.run([
                        dash_client, "-v", "error", "-xerror", "-ss", "85", "-i", url, "-t", "2",
                        "-map", "0:v:0", "-map", "0:a:0", "-fps_mode", "passthrough", "-f", "null", "-",
                    ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45)
                    assert sought.returncode == 0, sought.stderr.decode(errors="replace")
                    print("timestamp-gap: public DASH manifest seek decoded by FFmpeg")
            assert nt.get(port, STOP, [("session", session), ("X-Plex-Token", token)])[0] == 200
            print(name + ": starts, copies video and converts audio across a gap over 20 s")
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
