#!/usr/bin/env python3
"""Record what plexav's PMS answers for Plezy's music quality presets.

Plezy (38c48f14, `lib/services/plex_client.dart` `_buildMusicTranscodeParams`) asks for a track
below original quality as one progressive MP3: `directPlay=0`, `directStream=0`, `protocol=http`,
`musicBitrate` 320/192/128 and the target `add-transcode-target(type=musicProfile&context=streaming
&protocol=http&container=mp3&audioCodec=mp3)` on the `Generic` platform, a decision and then
`start.mp3` with the same query. This replays exactly that at plexav for its own tracks: the decision
for each codec and bitrate, `start.mp3`'s head and its first bytes, and the session row it makes, then
stops the session. Media bodies are kept by length and first bytes only.

    python3 probes/record_plezy_music_http.py --base http://127.0.0.1:32420 --token-command "<prints the token>"
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "tests", "fixtures", "music", "plezy_http_mp3.json")
TARGET = "add-transcode-target(type=musicProfile&context=streaming&protocol=http&container=mp3&audioCodec=mp3)"
TRACKS = (("32", "15 Step"), ("68", "Opus Tone"), ("69", "Vorbis Tone"), ("70", "ALAC Tone"), ("71", "WAV Tone"))
HEAD = ("content-type", "accept-ranges", "connection", "transfer-encoding", "cache-control", "content-length")


def query(key, bitrate, session):
    """Plezy's parameters in its own order (`_buildMusicTranscodeParams` + `_transcodeClientParams`)."""
    return [("hasMDE", "1"), ("path", "/library/metadata/" + key), ("mediaIndex", "0"), ("partIndex", "0"),
            ("protocol", "http"), ("directPlay", "0"), ("directStream", "0"), ("musicBitrate", bitrate),
            ("session", session), ("X-Plex-Session-Identifier", session + "-id"),
            ("X-Plex-Client-Profile-Extra", TARGET), ("X-Plex-Product", "Plezy"), ("X-Plex-Version", "1.20.0"),
            ("X-Plex-Client-Identifier", "rexprobe-plezy"), ("X-Plex-Platform", "Generic"),
            ("X-Plex-Device", "Linux"), ("X-Plex-Device-Name", "rexprobe")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--token-command", required=True)
    parser.add_argument("--out", default=OUT)
    args = parser.parse_args()
    token = subprocess.run(args.token_command, shell=True, capture_output=True, text=True).stdout.strip()
    if not token:
        sys.exit("no token from --token-command")

    def send(path, pairs, accept=None, limit=None):
        headers = {"X-Plex-Token": token, "Accept-Language": "en"}
        if accept:
            headers["Accept"] = accept
        request = urllib.request.Request(args.base + path + "?" + urllib.parse.urlencode(pairs), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, response.headers, response.read(limit) if limit else response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    out = {"recorded": "plexav PMS 1.43.4, 2026-11-04", "client": "Plezy 38c48f14 _buildMusicTranscodeParams",
           "exchanges": [], "rules": []}
    session = "rexprobe-plezy"
    pairs = query("32", "192", session)
    status, headers, body = send("/music/:/transcode/universal/decision", pairs, "application/json")
    out["exchanges"].append({"path": "/music/:/transcode/universal/decision", "query": pairs, "status": status,
                             "headers": {"Content-Type": headers.get("Content-Type")},
                             "body": body.decode("utf-8", "replace").replace(token, "REDACTED")})
    status, headers, body = send("/music/:/transcode/universal/start.mp3", pairs, limit=65536)
    out["exchanges"].append({"path": "/music/:/transcode/universal/start.mp3", "query": pairs, "status": status,
                             "headers": [(k, v) for k, v in headers.items() if k.lower() in HEAD],
                             "length_read": len(body), "head": body[:10].hex()})
    status, _, body = send("/transcode/sessions", [], "application/json")
    out["exchanges"].append({"path": "/transcode/sessions", "status": status,
                             "body": body.decode("utf-8", "replace").replace(token, "REDACTED")})
    send("/music/:/transcode/universal/stop", [("session", session)])
    for key, title in TRACKS:
        for bitrate in ("320", "192", "128"):
            status, _, body = send("/music/:/transcode/universal/decision", query(key, bitrate, "rexprobe-rule"), "application/json")
            container = json.loads(body)["MediaContainer"] if status == 200 else {}
            media = (container.get("Metadata") or [{}])[0].get("Media", [{}])[0]
            out["rules"].append({"title": title, "musicBitrate": bitrate, "status": status,
                                 "general": container.get("generalDecisionCode"),
                                 "codec": media.get("audioCodec"), "container": media.get("container"),
                                 "bitrate": media.get("bitrate"), "protocol": media.get("protocol")})
    json.dump(out, open(args.out, "w"), indent=1)
    print("%d exchanges, %d rules -> %s" % (len(out["exchanges"]), len(out["rules"]), args.out))


if __name__ == "__main__":
    sys.exit(main())
