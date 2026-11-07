#!/usr/bin/env python3
"""tracklog.py <plays dir> <app log>: per play, what the app asked ExoPlayer for and what it decoded.
rex logs zen time (UTC); the phone logs local time (UTC+1)."""
import datetime as dt, os, re, sys

D, LOG = sys.argv[1], sys.argv[2]
lines = []
for l in open(LOG, errors="replace"):
    m = re.match(r"11-(\d\d) (\d\d:\d\d:\d\d)", l)
    if m and ("Override added" in l or "audioInputFormat" in l or "Track found" in l and "Subtitle" in l):
        lines.append((dt.datetime.strptime(f"2026-11-{m[1]} {m[2]}", "%Y-%m-%d %H:%M:%S"), l))
for v in sorted(os.listdir(D)):
    for play in sorted(os.listdir(f"{D}/{v}")):
        p = f"{D}/{v}/{play}/rex.log"
        if not os.path.exists(p) or not open(p).read().strip():
            continue
        t = re.match(r"(\d\d:\d\d:\d\d)", open(p).read())[1]
        day = "05" if t >= "12:00:00" else "06"
        start = dt.datetime.strptime(f"2026-11-{day} {t}", "%Y-%m-%d %H:%M:%S") + dt.timedelta(hours=1)
        win = [l for ts, l in lines if start <= ts <= start + dt.timedelta(seconds=25)]
        ov = list(dict.fromkeys(re.search(r"Override added '([^']+)'", l)[1] for l in win if "Override" in l))
        dec = list(dict.fromkeys(f"id{m[1]}:{m[2]}/{m[3]}ch/{m[4]}" for l in win if (m := re.search(r"audioInputFormat .*?id=(\d+), mimeType=audio/([\w-]+), codecs=\w+, channels=(\d+), sample_rate=\d+, language=(\w+)", l))))
        pick = open(f"{D}/{v}/{play}/details.txt").read().splitlines()[-2:] if os.path.exists(f"{D}/{v}/{play}/details.txt") else []
        pick = [x for x in pick if "->" in x]
        print(f"{v} {play:8} pick={'; '.join(pick) or 'none'} | asked={ov} | decoded={dec}")
