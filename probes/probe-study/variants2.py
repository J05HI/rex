#!/usr/bin/env python3
"""Push m.mkv (h264 + eng AAC/jpn AC3 5.1/commentary AAC + SRT/ASS/forced PGS) eight times with track lies.
variants2.py <lab dir> <rex url> <source url>"""
import copy, json, os, subprocess, sys, urllib.request

LAB, REX, SRC = sys.argv[1:4]
FFPROBE = os.path.expanduser("~/rex-ffmpeg/root/usr/lib/jellyfin-ffmpeg/ffprobe")
ENV = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(FFPROBE) + "/lib")
M = json.loads(subprocess.check_output([FFPROBE, "-v", "error", "-of", "json", "-show_format", "-show_streams",
                                        f"{LAB}/media/m.mkv"], env=ENV))
# indexes: 0 video, 1 eng aac, 2 jpn ac3 5.1, 3 commentary aac, 4 srt, 5 ass, 6 pgs forced
DESC = ("codec_name", "codec_long_name", "profile", "channels", "channel_layout", "tags", "disposition", "bit_rate", "sample_fmt")


def s(p, i): return next(x for x in p["streams"] if x["index"] == i)


def swap_desc(p, i, j):
    a, b = s(p, i), s(p, j)
    for k in DESC:
        a[k], b[k] = b.get(k), a.get(k)


def langs(p):
    s(p, 1)["tags"].update(language="jpn", title="Japanese"); s(p, 2)["tags"].update(language="eng", title="English")


def drop(i): return lambda p: p.update(streams=[x for x in p["streams"] if x["index"] != i])


def ghost_sub(p):
    g = copy.deepcopy(s(p, 4)); g.update(index=7, tags={"language": "fre", "title": "Ghost French"}); p["streams"].append(g)


def forced_on_srt(p):
    s(p, 6)["disposition"]["forced"] = 0; s(p, 4)["disposition"]["forced"] = 1


def default_jpn(p):
    s(p, 1)["disposition"]["default"] = 0; s(p, 2)["disposition"]["default"] = 1


VARIANTS = [
    ("M00", "tracks truth", None),
    ("M01", "audio 2 and 3 described swapped", lambda p: swap_desc(p, 2, 3)),
    ("M02", "audio languages swapped", langs),
    ("M03", "SRT not listed", drop(4)),
    ("M04", "ghost French SRT listed", ghost_sub),
    ("M05", "forced flag on SRT not PGS", forced_on_srt),
    ("M06", "Japanese audio not listed", drop(2)),
    ("M07", "default audio is Japanese", default_jpn),
]


def call(url, body, method="POST"):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=120))


items = []
for code, title, fn in VARIANTS:
    link = f"{LAB}/serve/lab/{code}.mkv"
    if not os.path.lexists(link):
        os.symlink(f"{LAB}/media/m.mkv", link)
    p = copy.deepcopy(M)
    if fn:
        fn(p)
    items.append({"id": f"lab/{code}.mkv", "path": f"lab/{code}.mkv", "release": f"lab-{code}", "name": f"{code} {title}",
                  "media_info": p, "display": {"title": f"{code} {title}", "date": "2026"}})
print(call(f"{REX}/rex/sources/lab", {"url": SRC}, "PUT"))
print(call(f"{REX}/rex/feed/lab/items", {"items": items}))
