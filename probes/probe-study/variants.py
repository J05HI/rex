#!/usr/bin/env python3
"""Push one real file many times to rex, each copy with a different (mostly false) ffprobe.

Run on zen: python3 variants.py <lab dir> <rex url> <byte-server url>
Each variant is a symlink to a.mp4 or b.mkv, so the bytes are always the real file.
"""
import copy, json, os, subprocess, sys, urllib.request

LAB, REX, SRC = sys.argv[1], sys.argv[2], sys.argv[3]
FFPROBE = os.path.expanduser("~/rex-ffmpeg/root/usr/lib/jellyfin-ffmpeg/ffprobe")
ENV = dict(os.environ, LD_LIBRARY_PATH=os.path.dirname(FFPROBE) + "/lib")


def probe(f):
    out = subprocess.check_output([FFPROBE, "-v", "error", "-of", "json", "-show_format", "-show_streams", f], env=ENV)
    return json.loads(out)


A = probe(f"{LAB}/media/a.mp4")   # h264 high 8-bit 1080p30 + aac stereo, mp4, 120 s
B = probe(f"{LAB}/media/b.mkv")   # hevc main10 1080p30 + eac3 5.1, matroska, 120 s


def v(p): return next(s for s in p["streams"] if s["codec_type"] == "video")
def au(p): return next(s for s in p["streams"] if s["codec_type"] == "audio")


def lie(base, fn):
    p = copy.deepcopy(base)
    fn(p)
    return p


def hevc10(p):
    v(p).update(codec_name="hevc", codec_long_name="H.265 / HEVC", profile="Main 10", codec_tag_string="hev1",
                pix_fmt="yuv420p10le", bits_per_raw_sample="10", level=150)


def h264_8(p):
    v(p).update(codec_name="h264", codec_long_name="H.264 / AVC", profile="High", codec_tag_string="avc1",
                pix_fmt="yuv420p", level=41)
    v(p).pop("bits_per_raw_sample", None)


def res4k(p): v(p).update(width=3840, height=2160, coded_width=3840, coded_height=2160)
def res240(p): v(p).update(width=320, height=240, coded_width=320, coded_height=240)
def dur(sec): return lambda p: (p["format"].update(duration=f"{sec}.000000"), [s.update(duration=f"{sec}.000000") for s in p["streams"]])
def truehd(p): au(p).update(codec_name="truehd", codec_long_name="TrueHD", channels=8, channel_layout="7.1", profile=None)
def aac2(p): au(p).update(codec_name="aac", codec_long_name="AAC", profile="LC", channels=2, channel_layout="stereo")
def mkv(p): p["format"].update(format_name="matroska,webm", format_long_name="Matroska / WebM")
def mp4(p): p["format"].update(format_name="mov,mp4,m4a,3gp,3g2,mj2", format_long_name="QuickTime / MOV")
def bitrate(bps): return lambda p: (p["format"].update(bit_rate=str(bps)), v(p).update(bit_rate=str(bps)))
def prores(p): v(p).update(codec_name="prores", codec_long_name="Apple ProRes", profile="HQ", codec_tag_string="apch", pix_fmt="yuv422p10le")
def no_audio(p): p["streams"] = [s for s in p["streams"] if s["codec_type"] != "audio"]
def no_video(p): p["streams"] = [s for s in p["streams"] if s["codec_type"] != "video"]
def no_format(p): p.pop("format")


def hdr_dv(p):
    hevc10(p)
    v(p).update(color_transfer="smpte2084", color_primaries="bt2020", color_space="bt2020nc",
                side_data_list=[{"side_data_type": "DOVI configuration record", "dv_version_major": 1, "dv_profile": 8,
                                 "dv_level": 6, "rpu_present_flag": 1, "el_present_flag": 0, "bl_present_flag": 1,
                                 "dv_bl_signal_compatibility_id": 1}])


def ghost_tracks(p):
    a2 = copy.deepcopy(au(p)); a2.update(index=len(p["streams"]), tags={"language": "jpn", "title": "Ghost Japanese 5.1"},
                                         codec_name="ac3", channels=6, channel_layout="5.1(side)")
    s = {"index": len(p["streams"]) + 1, "codec_type": "subtitle", "codec_name": "hdmv_pgs_subtitle",
         "tags": {"language": "eng", "title": "Ghost PGS"}, "disposition": {"default": 1, "forced": 1}}
    p["streams"] += [a2, s]


def size(n): return lambda p: p["format"].update(size=str(n))


# (code, base, file, title, lie)
VARIANTS = [
    ("A00", A, "a.mp4", "truth: H.264/AAC MP4", None),
    ("A01", A, "a.mp4", "says HEVC Main10 (is H.264)", hevc10),
    ("A02", A, "a.mp4", "says ProRes (is H.264)", prores),
    ("A03", A, "a.mp4", "says 4K (is 1080p)", res4k),
    ("A04", A, "a.mp4", "says 240p (is 1080p)", res240),
    ("A05", A, "a.mp4", "says 10 s long (is 120 s)", dur(10)),
    ("A06", A, "a.mp4", "says 2 h long (is 120 s)", dur(7200)),
    ("A07", A, "a.mp4", "says TrueHD 7.1 (is AAC 2.0)", truehd),
    ("A08", A, "a.mp4", "says MKV (is MP4)", mkv),
    ("A09", A, "a.mp4", "says 150 Mbps (is 4 Mbps)", bitrate(150_000_000)),
    ("A10", A, "a.mp4", "says HDR10+DV (is SDR H.264)", hdr_dv),
    ("A11", A, "a.mp4", "ghost JPN audio + forced PGS", ghost_tracks),
    ("A12", A, "a.mp4", "no audio stream listed", no_audio),
    ("A13", A, "a.mp4", "no video stream listed", no_video),
    ("A14", A, "a.mp4", "no format block", no_format),
    ("A15", A, "a.mp4", "says 10x the real size", lambda p: p["format"].update(size=str(int(p["format"]["size"]) * 10))),
    ("A16", A, "a.mp4", "says a tenth of the size", lambda p: p["format"].update(size=str(int(p["format"]["size"]) // 10))),
    ("B00", B, "b.mkv", "truth: HEVC10/EAC3 5.1 MKV", None),
    ("B01", B, "b.mkv", "says H.264/AAC MP4 (is HEVC10/EAC3 MKV)", lambda p: (h264_8(p), aac2(p), mp4(p))),
    ("B02", B, "b.mkv", "says AAC 2.0 (is EAC3 5.1)", aac2),
]


def put(url, body, method="POST"):
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method=method, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=120))


def main():
    os.makedirs(f"{LAB}/serve/lab", exist_ok=True)
    items = []
    for code, base, real, title, fn in VARIANTS:
        name = f"{code}{os.path.splitext(real)[1]}"
        link = f"{LAB}/serve/lab/{name}"
        if not os.path.lexists(link):
            os.symlink(f"{LAB}/media/{real}", link)
        mi = copy.deepcopy(base) if fn is None else lie(base, fn)
        items.append({"id": f"lab/{name}", "path": f"lab/{name}", "release": f"lab-{code}", "name": f"{code} {title}",
                      "media_info": mi, "display": {"title": f"{code} {title}", "date": "2026"}})
    print(put(f"{REX}/rex/sources/lab", {"url": SRC}, "PUT"))
    print(put(f"{REX}/rex/feed/lab/items", {"items": items}))
    # The no-media_info case: confirm it is held.
    print(put(f"{REX}/rex/feed/lab/items", {"items": [{"id": "lab/A99.mp4", "path": "lab/A99.mp4", "display": {"title": "A99 no probe"}}]}))


if __name__ == "__main__":
    main()
