#!/usr/bin/env python3
"""A collection's clear logo, theme and custom order, answered as a real PMS answers them.

`fixtures/collection_logos_s1434.json` holds python-plexapi 4.18's own requests for
`uploadLogo`, `logos`, `setLogo`, `uploadTheme`, `themes` and `sortUpdate` (what Kometa calls for
a collection's `url_logo` and `collection_order`, rex#15), and what two real PMS 1.43.4 answered
them, one claimed and one not. This replays plexapi's requests in order against a claimed rex
(`--assert-claimed`) and compares each answer with the claimed reference's:

- an upload, by body or by `?url=`, lists `upload://clearLogos/<sha1>` (or `themes`) first and
  selected, and the collection row carries it (`clearLogo` Image, `theme`, locked Fields);
- `setLogo`'s `PUT .../clearLogo?url=` picks one and answers its new path;
- `collectionSort` offers `2:Custom`, so plexapi's `editAdvanced` check passes, and 2 reads back;
- the refusals: `PUT .../clearLogos` 404, an unfetchable url or no upload at all 406, `PUT
  .../clearLogo` without a url 400.

The `?url=` uploads fetch the recorded GitHub url and skip offline.

    python3 tests/test_collection_logos.py
"""
import hashlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zlib

from server_command import server_command
from startup_wait import poll_until
from test_ports import free_port

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = json.load(open(os.path.join(HERE, "fixtures", "collection_logos_s1434.json")))
TOKEN = "logos-token"
RECORDED_ID = "50001"  # the collection plexapi's recording made on rex

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  -- " + str(detail)[:600]))


def png():
    """A 1x1 PNG, the recorder's filepath upload."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


def catalog():
    items = [{"ratingKey": key, "key": "/library/metadata/" + key, "type": "movie", "title": title, "year": year,
              "guid": "plex://movie/%024d" % int(key), "addedAt": 1792789000 + int(key), "librarySectionID": 1,
              "librarySectionKey": "/library/sections/1", "librarySectionTitle": "Movies"}
             for key, title, year in (("1", "Big Buck Bunny", 2008), ("3", "Sintel", 2010))]
    # plexapi's createCollection names the server by its machineIdentifier.
    return {"machineIdentifier": "78a364ad887787f74e834a5c48c987d3e6fb165a", "friendlyName": "rexlogos",
            "sections": [{"key": "1", "type": "movie", "title": "Movies", "agent": "tv.plex.agents.movie",
                          "scanner": "Plex Movie", "language": "en-US", "Location": [{"id": 1, "path": "/data/movies"}]}],
            "items": items}


def ask(port, method, path, body=b""):
    url = "http://127.0.0.1:%d%s%sX-Plex-Token=%s" % (port, path, "&" if "?" in path else "?", TOKEN)
    request = urllib.request.Request(url, data=body or None, method=method, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, ConnectionError):
        return 0, b""


def document(raw):
    try:
        return json.loads(raw)["MediaContainer"]
    except (ValueError, KeyError, TypeError):
        return {}


def reference(method, path):
    """The claimed reference's answer to a request whose path starts `path`."""
    for exchange in FIXTURE["claimed"]:
        if exchange["method"] == method and exchange["path"].startswith(path):
            return exchange
    raise KeyError((method, path))


def reachable(host):
    try:
        socket.create_connection((host, 443), timeout=5).close()
        return True
    except OSError:
        return False


def main():
    work = tempfile.mkdtemp(prefix="rex-logos-")
    json.dump(catalog(), open(os.path.join(work, "catalog.json"), "w"))
    port = free_port()
    env = dict(os.environ, REX_PLEXTV_URL="http://127.0.0.1:9")
    for name in ("REX_TOKEN", "PLEX_TOKEN", "REX_STATE"):
        env.pop(name, None)
    log = open(os.path.join(work, "server.log"), "ab")
    proc = subprocess.Popen(server_command("--port", port, "--catalog", os.path.join(work, "catalog.json"),
                                           "--gdm", "off", "--token", TOKEN, "--assert-claimed", root=ROOT),
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    online = reachable("raw.githubusercontent.com")
    logo = png()
    mine = "upload://clearLogos/" + hashlib.sha1(logo).hexdigest()
    try:
        poll_until(lambda: ask(port, "GET", "/library/sections")[0] == 200, 40)
        calls = FIXTURE["plexapi"]
        status, raw = ask(port, calls[0]["method"], calls[0]["path"])
        made = (document(raw).get("Metadata") or [{}])[0].get("ratingKey")
        check("createCollection: 200 and a collection", status == 200 and made, (status, raw[:200]))
        cid = str(made)
        here = "/library/metadata/%s" % cid

        def send(call, body=b""):
            path = call["path"].replace("/%s/" % RECORDED_ID, "/%s/" % cid).replace("/%s?" % RECORDED_ID, "/%s?" % cid)
            if path.endswith("/" + RECORDED_ID):
                path = path[: -len(RECORDED_ID)] + cid
            return ask(port, call["method"], path, logo if call["body_bytes"] else body)

        def listed(kind):
            return document(ask(port, "GET", "%s/%s" % (here, kind))[1]).get("Metadata") or []

        def row():
            return (document(ask(port, "GET", "/library/collections/%s" % cid)[1]).get("Metadata") or [{}])[0]

        by_call = {}
        for call in calls[1:]:
            by_call.setdefault(call["call"], []).append(call)

        # logos(): empty, as the reference's before any upload.
        status, raw = send(by_call["logos"][0])
        check("logos() before an upload: 200, empty, as the reference's",
              status == 200 and document(raw).get("size") == 0 == reference("GET", "/library/metadata/7/clearLogos")["body"]["MediaContainer"]["size"],
              (status, raw[:200]))

        # uploadLogo(filepath=): the body is the logo.
        status, _ = send(by_call["uploadLogo(filepath)"][0])
        check("uploadLogo(filepath): 200", status == 200, status)
        logos = listed("clearLogos")
        check("the upload is the one logo, selected", [(p.get("ratingKey"), p.get("selected")) for p in logos] == [(mine, True)], logos)
        theirs = reference("GET", "/library/metadata/7/clearLogos")
        for entry in FIXTURE["claimed"]:
            if entry["method"] == "GET" and entry["path"] == "/library/metadata/7/clearLogos" and entry["body"]["MediaContainer"]["size"]:
                theirs = entry["body"]["MediaContainer"]["Metadata"][0]
                break
        want = {name: value.replace("/7/", "/%s/" % cid).replace(theirs["ratingKey"].split("/")[-1], mine.split("/")[-1])
                for name, value in theirs.items() if name in ("key", "thumb")}
        check("its key and thumb are the reference's form", logos and {k: logos[0].get(k) for k in want} == want, (logos, want))
        status, raw = ask(port, "GET", logos[0]["key"] if logos else here + "/file")
        check("its key serves the bytes", status == 200 and raw == logo, (status, len(raw)))
        got = row()
        images = [i for i in got.get("Image", []) if i.get("type") == "clearLogo"]
        ref_row = next(e for e in FIXTURE["claimed"] if e["method"] == "GET" and e["path"] == "/library/collections/7")
        ref_row = ref_row["body"]["MediaContainer"]["Metadata"][0]
        check("the row carries a clearLogo Image, as the reference's",
              len(images) == 1 and images[0].get("alt") == got.get("title")
              and re.fullmatch(r"%s/clearLogo/\d+" % here, images[0].get("url", ""))
              and [i["type"] for i in ref_row["Image"]] == ["clearLogo"], (got.get("Image"), ref_row["Image"]))
        check("and a locked clearLogo Field", {"locked": True, "name": "clearLogo"} in got.get("Field", []), got.get("Field"))
        status, raw = ask(port, "GET", images[0]["url"] if images else here + "/clearLogo")
        check("the Image url serves the logo", status == 200 and raw == logo, (status, len(raw)))

        # uploadLogo(url=): fetched, listed first and selected, the reference's own sha1.
        call = by_call["uploadLogo(url)"][0]
        if online:
            status, _ = send(call)
            ref = reference("POST", "/library/metadata/7/clearLogos?url=https%3A%2F%2Fraw.githubusercontent.com%2Fgithub")
            check("uploadLogo(url): %d as the reference's" % ref["status"], status == ref["status"], status)
            logos = listed("clearLogos")
            check("the fetched logo is listed first, selected, as upload://clearLogos/<the reference's sha1>",
                  [(p.get("ratingKey"), p.get("selected")) for p in logos] == [(theirs["ratingKey"], True), (mine, False)], logos)
        else:
            print("SKIP  uploadLogo(url): raw.githubusercontent.com unreachable")

        # setLogo(): plexapi's PUT .../clearLogo?url=<ratingKey>, its answer the new path.
        pick = dict(by_call["setLogo"][0], path="/library/metadata/%s/clearLogo?url=%s" % (RECORDED_ID, urllib.parse.quote_plus(mine)))
        status, raw = send(pick)
        ref = reference("PUT", "/library/metadata/7/clearLogo?url=")
        check("setLogo: %d with the new path, as the reference's" % ref["status"],
              status == ref["status"] and re.fullmatch(r"%s/clearLogo/\d+" % here, raw.decode()), (status, raw[:100]))
        check("the picked logo is selected", [p.get("ratingKey") for p in listed("clearLogos") if p.get("selected")] == [mine],
              listed("clearLogos"))

        # The refusals, each the reference's.
        for method, path, ref_path in (
                ("PUT", "%s/clearLogos?url=%s" % (here, urllib.parse.quote_plus("https://example.com/x.png")), "/library/metadata/7/clearLogos?url="),
                ("POST", "%s/clearLogos?url=%s" % (here, urllib.parse.quote_plus("https://not-a-real-host.invalid/x.png")),
                 "/library/metadata/7/clearLogos?url=https%3A%2F%2Fnot-a-real-host"),
                ("POST", "%s/clearLogos" % here, "/library/metadata/7/clearLogos"),
                ("PUT", "%s/clearLogo" % here, "/library/metadata/7/clearLogo")):
            ref = next(e for e in FIXTURE["claimed"] if e["method"] == method and (e["path"] == ref_path or ref_path.endswith("=") and e["path"].startswith(ref_path) or "%3A" in ref_path and e["path"].startswith(ref_path)))
            status, _ = ask(port, method, path)
            check("%s %s: %d as the reference's" % (method, path[len(here):][:40], ref["status"]), status == ref["status"], status)

        # uploadTheme(filepath=) and themes(): a Track candidate, the row's theme.
        status, _ = send(by_call["uploadTheme(filepath)"][0])
        check("uploadTheme(filepath): 200", status == 200, status)
        status, raw = send(by_call["themes"][0])
        themes = document(raw).get("Metadata") or []
        check("themes() lists it, selected", [(t.get("ratingKey"), t.get("selected")) for t in themes]
              == [("upload://themes/" + mine.split("/")[-1], True)], themes)
        got = row()
        ref_row = reference("GET", "/library/collections/7?includePreferences=1")
        ref_row = [e for e in FIXTURE["claimed"] if e["path"] == ref_row["path"]][-1]["body"]["MediaContainer"]["Metadata"][0]
        check("the row carries a theme, as the reference's",
              re.fullmatch(r"%s/theme/\d+" % here, got.get("theme", "")) and ref_row["theme"].startswith("/library/metadata/7/theme/"),
              got.get("theme"))
        check("and a locked theme Field", {"locked": True, "name": "theme"} in got.get("Field", []), got.get("Field"))

        # sortUpdate('alpha') then ('custom'): plexapi reads the enum, then writes.
        def enum():
            status, raw = send(by_call["sortUpdate(custom)"][0])
            settings = (document(raw).get("Metadata") or [{}])[0].get("Preferences", {}).get("Setting", [])
            return next((s for s in settings if s.get("id") == "collectionSort"), {})
        for call in by_call["sortUpdate(alpha)"]:
            status, _ = send(call)
            check("sortUpdate(alpha) %s: 200" % call["method"], status == 200, status)
        setting = enum()
        ref_setting = next(s for s in ref_row["Preferences"]["Setting"] if s["id"] == "collectionSort")
        check("collectionSort offers the claimed reference's enumValues", setting.get("enumValues") == ref_setting["enumValues"],
              (setting.get("enumValues"), ref_setting["enumValues"]))
        # python-plexapi's editAdvanced: `2` must be one of the enum's keys.
        keys = [pair.split(":")[0] for pair in setting.get("enumValues", "").split("|")]
        check("plexapi's editAdvanced accepts 2", "2" in keys, keys)
        status, _ = send(by_call["sortUpdate(custom)"][1])
        check("sortUpdate(custom) PUT collectionSort=2: 200", status == 200, status)
        check("2 reads back, as the reference's", enum().get("value") == ref_setting["value"] == "2"
              and str(row().get("collectionSort")) == ref_row["collectionSort"], (enum().get("value"), row().get("collectionSort")))

        # A film takes uploadLogo(url=) too.
        if online:
            status, _ = send(by_call["film uploadLogo(url)"][0])
            ref = reference("POST", "/library/metadata/1/clearLogos?url=https%3A%2F%2Fraw.githubusercontent.com%2Fgithub")
            check("a film's uploadLogo(url): %d as the reference's" % ref["status"], status == ref["status"], status)
        else:
            print("SKIP  a film's uploadLogo(url): raw.githubusercontent.com unreachable")
    finally:
        proc.terminate()
        proc.wait(timeout=20)
        log.close()
    print("%d/%d passed" % (sum(results), len(results)))
    return 0 if results and all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
