#!/usr/bin/env python3
"""Record what a real PMS's `/photo/:/transcode` answers at a size: geometry, type, codec, quality.

Run on the lab host against plexlab (PMS 1.43.4, unclaimed, so loopback needs no token):

    python3 probes/record_photo_transcode.py --pms http://127.0.0.1:32400 --item 3 \\
        --serve 172.31.7.1 tests/fixtures/photos/transcode_s1434.json

The requests are issued by hand, not by a client: Plex Web's own grid request
(`width=240&height=360&minSize=1&upscale=1`, 76 times in `reference-walk.jsonl`) and variations of
each parameter around it. Their pictures:

* `poster`, `logo`: the item's own poster (Plex's agent, a 1920x2880 JPEG) and clearLogo (an RGBA
  PNG), named by the item's paths;
* `remote`, `tmdb`: the same poster's absolute urls from the item's poster list (metadata-static
  and TMDB's `original`), fetched by PMS itself;
* `exif6`, `exif1`, `alpha`, `webp`, `gif`: pictures made here (`synthetic()`) and served to PMS
  over http from `--serve` (an address on PMS's own network): a 300x200 JPEG, red in its left third
  and blue elsewhere, with EXIF orientation 6 and without; a 600x300 half-transparent green PNG, and
  the same as WebP and GIF.

Plex's own artwork is kept by sha256 and length only (CLAUDE.md). For each answer: status, type,
the bytes' real format, size and mode, the JPEG's IJG quality (the quality whose scaled standard
table it carries), chroma subsampling, progressive or not, EXIF orientation, and for the synthetic
pictures the colour at the top and bottom middle. Needs Pillow. GETs only.
"""
import argparse
import functools
import hashlib
import http.server
import io
import json
import os
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, JpegImagePlugin

STD_LUMA = [16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56, 14, 17, 22, 29,
            51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92, 49, 64, 78, 87, 103, 121, 120,
            101, 72, 92, 95, 98, 112, 100, 103, 99]
GRID = [("width", "240"), ("height", "360"), ("minSize", "1"), ("upscale", "1")]
SUBSAMPLING = {0: "4:4:4", 1: "4:2:2", 2: "4:2:0"}


def ijg_quality(image):
    """The IJG quality whose scaled standard luminance table the JPEG carries, or None."""
    table = sorted((getattr(image, "quantization", None) or {}).get(0, []))
    for quality in range(1, 101):
        scale = 5000 // quality if quality < 50 else 200 - quality * 2
        if sorted(min(255, max(1, (value * scale + 50) // 100)) for value in STD_LUMA) == table:
            return quality
    return None


def describe(body):
    out = {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    try:
        image = Image.open(io.BytesIO(body))
    except Exception:
        return out
    out.update(format=image.format, size=list(image.size), mode=image.mode,
               orientation=image.getexif().get(0x0112))
    if image.format == "JPEG":
        out.update(quality=ijg_quality(image), subsampling=SUBSAMPLING.get(JpegImagePlugin.get_sampling(image)),
                   progressive=bool(image.info.get("progressive")))
    return out


def synthetic(directory):
    """The pictures PMS is handed over http, made the same way on every run."""
    band = Image.new("RGB", (300, 200), (0, 0, 255))
    ImageDraw.Draw(band).rectangle([0, 0, 99, 199], fill=(255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6
    band.save(os.path.join(directory, "exif6.jpg"), quality=90, exif=exif.tobytes())
    band.save(os.path.join(directory, "exif1.jpg"), quality=90)
    alpha = Image.new("RGBA", (600, 300), (0, 255, 0, 128))
    alpha.save(os.path.join(directory, "alpha.png"))
    alpha.convert("RGB").save(os.path.join(directory, "webp.webp"), quality=90)
    alpha.convert("RGB").save(os.path.join(directory, "gif.gif"))
    return {"exif6": "exif6.jpg", "exif1": "exif1.jpg", "alpha": "alpha.png", "webp": "webp.webp", "gif": "gif.gif"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--pms", default="http://127.0.0.1:32400")
    parser.add_argument("--token", default=os.environ.get("PLEX_TOKEN", ""))
    parser.add_argument("--item", default="3")
    parser.add_argument("--serve", required=True, help="an address of this host PMS can reach")
    args = parser.parse_args()

    def get(path, accept=None):
        headers = {"Accept": accept} if accept else {}
        if args.token:
            headers["X-Plex-Token"] = args.token
        request = urllib.request.Request(args.pms + path, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.headers.get("Content-Type"), response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers.get("Content-Type"), error.read()

    _, _, raw = get("/identity", "application/json")
    version = json.loads(raw)["MediaContainer"]["version"]
    _, _, raw = get("/library/metadata/%s?includeImages=1" % args.item, "application/json")
    row = json.loads(raw)["MediaContainer"]["Metadata"][0]
    images = {image["type"]: image["url"] for image in row.get("Image", [])}
    _, _, raw = get("/library/metadata/%s/posters" % args.item, "application/json")
    posters = json.loads(raw)["MediaContainer"]["Metadata"]
    remote = next(poster["key"] for poster in posters if poster["key"].startswith("https://metadata-static.plex.tv/"))
    tmdb = next(poster["key"] for poster in posters if poster["key"].startswith("https://image.tmdb.org/t/p/original/"))

    directory = tempfile.mkdtemp(prefix="photo-transcode-")
    files = synthetic(directory)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    httpd = http.server.ThreadingHTTPServer((args.serve, 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    served = "http://%s:%d/" % (args.serve, httpd.server_address[1])

    urls = {"poster": images["coverPoster"], "logo": images["clearLogo"], "remote": remote, "tmdb": tmdb}
    urls.update({name: served + file for name, file in files.items()})
    sources = {}
    for name, url in urls.items():
        if url.startswith("/"):
            status, kind, body = get(url)
        else:
            with urllib.request.urlopen(url, timeout=60) as response:
                status, kind, body = response.status, response.headers.get("Content-Type"), response.read()
        sources[name] = dict(describe(body), url=url if not url.startswith(served) else files[name], status=status, type=kind)

    def size(width, height, *extra):
        return [("width", str(width)), ("height", str(height))] + list(extra)

    asks = [("poster", GRID), ("poster", size(240, 360)), ("poster", size(240, 360, ("minSize", "1"))),
            ("poster", size(1000, 300)), ("poster", size(1000, 300, ("minSize", "1"))),
            ("poster", size(2000, 2000, ("minSize", "1"))), ("poster", size(2000, 2000, ("minSize", "1"), ("upscale", "1"))),
            ("poster", size(4096, 4096)), ("poster", size(4096, 4096, ("minSize", "1"), ("upscale", "1"))),
            ("poster", size(3000, 5000, ("upscale", "1"))), ("poster", size(8192, 8192, ("upscale", "1"))),
            ("poster", size(10000, 10000, ("upscale", "1"))), ("poster", size(11000, 11000, ("upscale", "1"))),
            ("poster", size(8192, 8192, ("minSize", "1"), ("upscale", "1"))),
            ("poster", size(0, 0)), ("poster", size(0, 360)), ("poster", size(240, 0)),
            ("poster", size(1, 100)), ("poster", size(-5, 360))]
    asks += [("poster", GRID + [("quality", quality)]) for quality in
             ("1", "10", "50", "90", "100", "-1", "0", "101", "127", "128", "129", "150", "200", "255", "256", "300")]
    asks += [("poster", GRID + [("format", value)]) for value in ("png", "jpeg", "jpg", "webp", "gif")]
    asks += [("poster", GRID + [("blur", "20")]), ("poster", GRID + [("opacity", "50")]),
             ("poster", GRID + [("background", "ff0000")])]
    asks += [("logo", size(400, 200)), ("logo", size(400, 200, ("format", "png"))), ("logo", size(400, 200, ("format", "jpeg"))),
             ("logo", size(400, 200, ("minSize", "1"), ("upscale", "1"))), ("logo", size(6000, 1000, ("upscale", "1"))),
             ("logo", size(12000, 1000, ("upscale", "1"))), ("logo", size(3000, 10, ("upscale", "1"))), ("logo", size(0, 0))]
    asks += [("remote", GRID), ("tmdb", GRID)]
    asks += [("exif6", size(150, 150)), ("exif6", size(1000, 1000)), ("exif1", size(150, 150)),
             ("alpha", size(300, 300)), ("alpha", size(300, 300, ("format", "jpeg"))), ("webp", size(300, 300)),
             ("gif", size(300, 300))]
    answers = []
    for name, query in asks:
        target = urls[name]
        status, kind, body = get("/photo/:/transcode?" + urllib.parse.urlencode(query + [("url", target)]))
        answer = dict(describe(body) if status == 200 else {"bytes": len(body)}, source=name, query=query, status=status, type=kind)
        if status == 200 and name in files:
            image = Image.open(io.BytesIO(body)).convert("RGB")
            answer["top"] = list(image.getpixel((image.width // 2, 2)))
            answer["bottom"] = list(image.getpixel((image.width // 2, image.height - 3)))
        answers.append(answer)
        print(name, urllib.parse.urlencode(query), status, kind, answer.get("format"), answer.get("size"), answer.get("quality"))
    httpd.shutdown()
    fixture = {
        "_provenance": "plexlab (PMS %s, unclaimed) on the lab host, 2026-11-17: /photo/:/transcode asked by hand with "
                       "probes/record_photo_transcode.py (not a client recording; Plex Web's grid request is the first). "
                       "Plex's artwork kept by sha256 and length only; the synthetic pictures are made by synthetic()." % version,
        "server": version, "sources": sources, "answers": answers}
    with open(args.out, "w") as out:
        json.dump(fixture, out, indent=1)
        out.write("\n")


if __name__ == "__main__":
    main()
