#!/usr/bin/env python3
"""Record which audio files a real PMS makes tracks of, its Media for each, and how it reads its Part.

Run on the lab host beside plexav, whose media folder its PMS sees as `/data`: for every file under
`<media>/<folder>` this keeps ffprobe's JSON as a source pushes it (`-show_format -show_streams
-show_chapters -show_packets -read_intervals %+#1`), the track row PMS made of it in `--section`
(`/library/metadata/{id}?includeChapters=1`), `null` where it made none, and a `Range: bytes=0-99`
read of its Part (status and `Content-Type`). Read-only.

`tests/fixtures/music/audio-formats.json` is the `formats` folder: one ~5 s file per extension, made
with FFmpeg (`sine=frequency=440:duration=5`; `-f ipod` for `.m4b`, `-c:a dca -strict -2` for `.dts`,
`-c:a mp2` for `.mp2`/`.mpa`, `-c:a real_144 -ar 8000 -ac 1 -f rm` for `.rm`/`.rma`), FFmpeg's FATE
samples for what it cannot encode (`musepack/inside-mp8.mpc` as `.mpc`, `inside-mp7.mpc` as `.mp+` and
`.mpp`, `lossless-audio/luckynight-partial.shn` and `.tak`) and a 2 s DSD64 DSF written by hand as
`.dsd` and `.dsf`. `audiobook-probes.json` is the `audiobooks` folder: LibriVox's public-domain
recording `selectedpoemsgordon_2509_librivox` (archive.org) as its `.m4b` and its six `.mp3`s.

    python3 probes/record_audio_formats.py --base http://127.0.0.1:32420 --token-file tok \\
        --media ~/plexav/media --folder formats --section 5 > tests/fixtures/music/audio-formats.json
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--media", required=True)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--section", required=True)
    args = parser.parse_args()
    token = open(args.token_file).read().strip()

    def get(path, headers=None):
        request = urllib.request.Request(args.base + path, headers=dict({"X-Plex-Token": token, "Accept": "application/json"},
                                                                        **(headers or {})))
        return urllib.request.urlopen(request)

    rows = {row["Media"][0]["Part"][0]["file"]: row
            for row in json.load(get("/library/sections/%s/all?type=10" % args.section))["MediaContainer"].get("Metadata", [])}
    out = []
    for root, _, files in os.walk(os.path.join(args.media, args.folder)):
        for name in sorted(files):
            if name.endswith(".jpg"):
                continue
            path = os.path.join(root, name)
            probe = json.loads(subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams",
                                               "-show_chapters", "-show_packets", "-read_intervals", "%+#1", path],
                                              capture_output=True, stdin=subprocess.DEVNULL).stdout or b"{}")
            probe.get("format", {}).pop("filename", None)
            file = "/data" + path[len(args.media.rstrip("/")):]
            row = rows.get(file)
            track = read = None
            if row:
                track = json.load(get("/library/metadata/%s?includeChapters=1" % row["ratingKey"]))["MediaContainer"]["Metadata"][0]
                with get(track["Media"][0]["Part"][0]["key"], {"Range": "bytes=0-99"}) as response:
                    read = {"range": "bytes=0-99", "status": response.status, "contentType": response.headers.get("Content-Type")}
            out.append({"file": file, "size": os.path.getsize(path), "probe": probe, "track": track, "read": read})
    json.dump(out, sys.stdout, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
