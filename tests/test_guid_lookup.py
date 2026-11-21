#!/usr/bin/env python3
"""`/library/metadata/` asked by plex:// guid, as plexlab answers it.

Plex Web's Discover person page lists "Movies & Shows in Media Libraries" by asking every
server for the person's titles as `/library/metadata/<encodeURIComponent(guid)>,...?skipRefresh=1`
(ten a request, any error read as none). rex 404'd every one, so its titles never showed there.

Driven from `tests/fixtures/guid_lookup_s1434.json` (`probes/record_guid_lookup.py`): plexlab
(1.43.4) answered each guid request byte for byte as the same request by ratingKey; rex must
answer each of those exactly as it answers the same request by its own keys. The recorded
refusals are rex's too, and plexscratch42's guid shared by two rows answers the lower key
alone. A guid PREFIX (plexlab: the first row by guid) is measured and not emulated: no client
sends one.

The catalog is `tests/catalog_ui.json` with each of plexlab's rows standing on one of its rows
of the same type (a local:// guid on its local:// film), and, for the shared-guid leg, 1013
given 1012's guid. Without a token every guid lookup is the 401 a held one gets, so a peer cannot
test which titles a server holds (rex's choice: an unknown KEY is PMS's 404 before the token, and a
claimed PMS's answer for a guid is unmeasured). Drives a real server process (`tests/server_command.py`).

    python3 tests/test_guid_lookup.py
"""
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse

from server_command import server_command
from test_ports import free_port

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURE = os.path.join(HERE, "fixtures", "guid_lookup_s1434.json")
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
           REX_PLEXTV_URL=os.environ.get("REX_PLEXTV_URL", "http://127.0.0.1:9"))
for name in ("REX_STATE", "REX_TOKEN", "TMDB_KEY"):
    ENV.pop(name, None)
HEADERS = {"X-Plex-Client-Identifier": "guid-lookup", "X-Plex-Product": "Plex Web",
           "X-Plex-Version": "4.160.0", "X-Plex-Platform": "Chrome"}

# plexlab's ratingKey -> the catalog row standing for it.
LAB = {"1": "1007", "3": "1008", "4": "1009", "17": "1012", "18": "1013", "6": "20001",
       "7": "30001", "11": "1014", "8": "1001", "9": "1002", "10": "1003"}
# plexscratch42's: 3 and 4 the rows given one guid, 5 a row of another.
SHARED = {"3": "1012", "4": "1013", "5": "1007"}
# PMS's prefix match; rex looks up whole guids.
NOT_EMULATED = {"empty id", "guid prefix", "guid prefix of a show"}

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  -- " + str(detail)[:600]))


def request(port, path, accept="application/json"):
    headers = dict(HEADERS, Connection="close")
    if accept:
        headers["Accept"] = accept
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def keys_of(body):
    try:
        return [row["ratingKey"] for row in json.loads(body)["MediaContainer"].get("Metadata") or []]
    except (ValueError, KeyError):
        return None


def start(catalog, *gate):
    port = free_port()
    process = subprocess.Popen(
        server_command("--port", port, "--catalog", catalog, *(gate or ("--assert-claimed",)), "--gdm", "off", root=ROOT),
        cwd=ROOT, env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        if process.poll() is not None:
            break
        try:
            if request(port, "/identity")[0] == 200:
                return process, port
        except OSError:
            pass
        time.sleep(0.1)
    process.kill()
    raise SystemExit("server did not start")


def translate(text, guids, keys):
    """A recorded path with plexlab's guids and keys swapped for the catalog's."""
    for lab, ours in guids.items():
        text = text.replace(urllib.parse.quote(lab, safe=""), urllib.parse.quote(ours, safe="")).replace(lab, ours)
    path, _, query = text.partition("?")
    head, _, tail = path[len("/library/metadata/"):].partition("/")
    members = ",".join(keys.get(member, member) for member in head.split(","))
    return "/library/metadata/" + members + ("/" + tail if tail else "") + ("?" + query if query else "")


def main():
    fixture = json.load(open(FIXTURE))
    catalog = json.load(open(os.path.join(HERE, "catalog_ui.json")))
    rows = {row["ratingKey"]: row for row in catalog["items"]}
    guids = {lab["guid"]: rows[LAB[lab["ratingKey"]]]["guid"] for lab in fixture["library"] if lab["ratingKey"] in LAB}
    guids["local://11"] = "local://1014"
    shared_guid = fixture["shared"]["guid"]
    with tempfile.TemporaryDirectory(prefix="rex-guid-lookup-") as folder:
        path = os.path.join(folder, "catalog.json")
        json.dump(catalog, open(path, "w"))
        process, port = start(path)
        try:
            for case in fixture["cases"]:
                name = case["name"]
                ours = translate(case["path"], guids, LAB)
                status, body = request(port, ours, case["accept"])
                if name in NOT_EMULATED:
                    check("%s: a guid prefix is no guid rex holds (PMS %s)" % (name, case["status"]), status == 404, (status, ours))
                    continue
                check("%s: %s as plexlab (%s)" % (name, status, case["status"]), status == case["status"], (ours, body[:200]))
                same = case.get("same_as")
                if same and same["status"] == case["status"]:
                    check("%s: plexlab answered it as %s" % (name, same["path"]), same["identical"], same)
                    key_status, key_body = request(port, translate(same["path"], {}, LAB), case["accept"])
                    if same["identical"]:
                        check("%s: rex answers it as %s" % (name, translate(same["path"], {}, LAB)),
                              (status, body) == (key_status, key_body), (keys_of(body), keys_of(key_body)))
                # A listing under the guid (`/children`) is its own library's, not plexlab's rows.
                if case["rows"] is not None and status == 200 and "/" not in ours.split("?")[0][len("/library/metadata/"):]:
                    check("%s: rows in plexlab's order" % name, keys_of(body) == [LAB[k] for k in (r["ratingKey"] for r in case["rows"])],
                          (keys_of(body), case["rows"]))
        finally:
            process.terminate()
            process.wait(timeout=10)

        # plexscratch42: a guid two rows share is the lower id's alone, in a batch too.
        for row in catalog["items"]:
            if row["ratingKey"] in (SHARED["3"], SHARED["4"]):
                row["guid"] = shared_guid
        json.dump(catalog, open(path, "w"))
        process, port = start(path)
        try:
            for case in fixture["shared"]["cases"]:
                ours = translate(case["path"], {}, SHARED)
                status, body = request(port, ours)
                want = [SHARED[r["ratingKey"]] for r in case["rows"] or []]
                check("shared guid: %s -> %s" % (case["path"].split("?")[0], want),
                      status == case["status"] and keys_of(body) == want, (status, keys_of(body)))
        finally:
            process.terminate()
            process.wait(timeout=10)

        # Without a token, a guid rex holds and one it does not are the same 401.
        process, port = start(path, "--token", "guid-lookup-token")
        try:
            held = "/library/metadata/" + urllib.parse.quote(shared_guid, safe="") + "?skipRefresh=1"
            unheld = "/library/metadata/" + urllib.parse.quote("plex://movie/5d776b59ad5437001f79c6f8", safe="") + "?skipRefresh=1"
            refused = [request(port, held), request(port, unheld)]
            check("no token: a held guid and an unheld one are one 401", refused[0][0] == 401 and refused[0] == refused[1],
                  [(status, body[:80]) for status, body in refused])
            token = "&X-Plex-Token=guid-lookup-token"
            check("with the token: held 200, unheld 404",
                  (request(port, held + token)[0], request(port, unheld + token)[0]) == (200, 404))
        finally:
            process.terminate()
            process.wait(timeout=10)

    print("%d/%d passed" % (sum(results), len(results)))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
