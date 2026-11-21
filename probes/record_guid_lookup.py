#!/usr/bin/env python3
"""Record what plexlab answers when /library/metadata/ is asked by plex:// guid.

Plex Web's Discover person page ("Movies & Shows in Media Libraries") asks every server of the
account for the person's titles by guid, ten at a time (Plex Web 4.160.0, `main-8792` chunk,
generators `WP`/`HP`):

    GET /library/metadata/${guids.map(encodeURIComponent).join(",")}?skipRefresh=1

and treats any error as "none here". This asks plexlab (PMS 1.43.4 on kids, unclaimed, so its own
host needs no token) that request for each guid shape its library holds -- film, show, season,
episode, a local:// season, a batch out of key order, unknown and malformed guids, guids mixed with
numeric keys -- each beside the same request by ratingKey, and records whether the two bodies are
identical. GETs only.

plexlab holds no guid on two rows, so `--shared` also measures that on plexscratch42 (PMS 1.42.2,
disposable, offline: its rows carry local:// guids): two of its films are given one plex:// guid in
its database for the length of the requests and put back after, as `shared` in the output.

    python3 probes/record_guid_lookup.py tests/fixtures/guid_lookup_s1434.json --shared
"""
import json
import subprocess
import sys

REMOTE = r'''
import json, urllib.error, urllib.parse, urllib.request
def get(path, accept):
    headers = {"Accept": accept} if accept else {}
    request = urllib.request.Request("http://127.0.0.1:32400" + path, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
def rows(body):
    try:
        container = json.loads(body)["MediaContainer"]
    except Exception:
        return None, None
    attrs = {k: v for k, v in container.items() if k != "Metadata"}
    return attrs, [{"ratingKey": r.get("ratingKey"), "type": r.get("type"), "guid": r.get("guid")} for r in container.get("Metadata") or []]
out = {"server": None, "library": [], "cases": []}
status, body = get("/identity", "application/json")
out["server"] = json.loads(body)["MediaContainer"]["version"]
for section in ("1", "2"):
    for row in rows(get("/library/sections/%s/all" % section, "application/json")[1])[1]:
        out["library"].append(row)
for show in [r["ratingKey"] for r in out["library"] if r["type"] == "show"]:
    out["library"] += rows(get("/library/metadata/%s/children" % show, "application/json")[1])[1]
    out["library"] += rows(get("/library/metadata/%s/allLeaves" % show, "application/json")[1])[1]
for case in json.loads(JOB):
    by_guid = "/library/metadata/" + ",".join(urllib.parse.quote(m, safe="") if case.get("encode", True) else m for m in case["members"]) + case.get("query", "?skipRefresh=1")
    status, body = get(by_guid, case.get("accept", "application/json"))
    attrs, found = rows(body) if case.get("accept", "application/json") else (None, None)
    entry = {"name": case["name"], "members": case["members"], "path": by_guid, "accept": case.get("accept", "application/json"),
             "status": status, "container": attrs, "rows": found}
    if not found and status == 200 and not case.get("accept", "application/json"):
        entry["xml"] = body.decode()[:400]
    if status != 200:
        entry["body"] = body.decode(errors="replace")[:200]
    if case.get("keys") is not None:
        by_key = "/library/metadata/" + case["keys"] + case.get("query", "?skipRefresh=1")
        key_status, key_body = get(by_key, case.get("accept", "application/json"))
        entry["same_as"] = {"path": by_key, "status": key_status, "identical": key_status == status and key_body == body}
    out["cases"].append(entry)
print(json.dumps(out))
'''

M = {"bbb": "plex://movie/5d7768352e80df001ebde4c9", "sintel": "plex://movie/5d77688123d5a3001f4ed3df",
     "tos": "plex://movie/5d77699b96b655001fdd3b87", "elephants": "plex://movie/5d776833103a2d001f5674a1",
     "cosmos": "plex://movie/5d776bd7594b2b001e6e4d2f", "firefly": "plex://show/5d9c086c7d06d9001ffd27aa",
     "s1": "plex://season/602e67e766dfdb002c0a1b5b", "trainjob": "plex://episode/5d9c127e4eefaa001f6449c2",
     "bushwhacked": "plex://episode/5d9c127e4eefaa001f6449b6", "mrsreynolds": "plex://episode/5d9c127e2df347001e41c8f3"}
UNKNOWN = "plex://movie/5d776b59ad5437001f79c6f8"
JOB = [
    {"name": "film", "members": [M["bbb"]], "keys": "1"},
    {"name": "show", "members": [M["firefly"]], "keys": "6"},
    {"name": "season", "members": [M["s1"]], "keys": "7"},
    {"name": "episode", "members": [M["trainjob"]], "keys": "8"},
    {"name": "local season", "members": ["local://11"], "keys": "11"},
    {"name": "batch out of key order", "members": [M["tos"], M["bbb"], M["firefly"]], "keys": "4,1,6"},
    {"name": "batch of ten", "members": [M[k] for k in ("cosmos", "elephants", "sintel", "tos", "bbb", "firefly", "s1", "mrsreynolds", "bushwhacked", "trainjob")],
     "keys": "18,17,3,4,1,6,7,10,9,8"},
    {"name": "known then unknown", "members": [M["bbb"], UNKNOWN], "keys": "1"},
    {"name": "unknown then known", "members": [UNKNOWN, M["bbb"]], "keys": "1"},
    {"name": "unknown alone", "members": [UNKNOWN], "keys": "999999"},
    {"name": "two unknowns", "members": [UNKNOWN, "plex://show/5d9c086c7d06d9001ffd27ab"]},
    {"name": "guid then key", "members": [M["bbb"], "3"], "keys": "1,3"},
    {"name": "key then guid", "members": ["3", M["bbb"]], "keys": "3,1"},
    {"name": "same guid twice", "members": [M["bbb"], M["bbb"]], "keys": "1,1"},
    {"name": "guid and its own key", "members": [M["bbb"], "1"], "keys": "1,1"},
    {"name": "film id under show", "members": ["plex://show/5d7768352e80df001ebde4c9"]},
    {"name": "empty id", "members": ["plex://movie/"]},
    {"name": "guid prefix", "members": ["plex://movie/5d77688"]},
    {"name": "guid prefix of a show", "members": ["plex://show/5d9c08"]},
    {"name": "unknown scheme", "members": ["plex://bogus/5d7768352e80df001ebde4c9"]},
    {"name": "other agent", "members": ["com.plexapp.agents.imdb://tt1254207?lang=en"]},
    {"name": "not encoded", "members": [M["bbb"]], "encode": False, "keys": "1"},
    {"name": "batch not encoded", "members": [M["tos"], M["bbb"]], "encode": False, "keys": "4,1"},
    {"name": "no query", "members": [M["bbb"]], "query": "", "keys": "1"},
    {"name": "xml film", "members": [M["bbb"]], "accept": None, "keys": "1"},
    {"name": "xml batch", "members": [M["tos"], M["bbb"], M["firefly"]], "accept": None, "keys": "4,1,6"},
    {"name": "children by guid", "members": [M["firefly"]], "query": "/children", "keys": "6"},
]


SHARED = r'''
import json, subprocess, urllib.error, urllib.request
DB = "/config/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db"
def sql(statement):
    return subprocess.check_output(["docker", "exec", "plexscratch42-plex", "/usr/lib/plexmediaserver/Plex SQLite", DB, statement]).decode().strip()
tok = subprocess.check_output(["docker", "exec", "plexscratch42-plex", "sh", "-c",
    "cat '/config/Library/Application Support/Plex Media Server/.LocalAdminToken'"]).decode().strip()
def get(path):
    request = urllib.request.Request("http://100.85.222.1:32411" + path, headers={"Accept": "application/json", "X-Plex-Token": tok})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, [{"ratingKey": r["ratingKey"], "guid": r.get("guid")} for r in json.loads(response.read())["MediaContainer"].get("Metadata") or []]
    except urllib.error.HTTPError as error:
        return error.code, None
before = sql("select id, guid, title from metadata_items where id in (3, 4) order by id")
guid = "plex://movie/5d776833103a2d001f5674a1"
out = {"server": None, "rows_before": before, "guid": guid, "cases": []}
sql("update metadata_items set guid = '%s' where id in (3, 4)" % guid)
try:
    for path in ["/library/metadata/3,4?skipRefresh=1", "/library/metadata/plex%3A%2F%2Fmovie%2F5d776833103a2d001f5674a1?skipRefresh=1",
                 "/library/metadata/plex%3A%2F%2Fmovie%2F5d776833103a2d001f5674a1,plex%3A%2F%2Fmovie%2F5d776b59ad5437001f79c6f8?skipRefresh=1",
                 "/library/metadata/5,plex%3A%2F%2Fmovie%2F5d776833103a2d001f5674a1?skipRefresh=1",
                 "/library/metadata/plex%3A%2F%2Fmovie%2F5d776833103a2d001f5674a1,5?skipRefresh=1"]:
        status, found = get(path)
        out["cases"].append({"path": path, "status": status, "rows": found})
finally:
    sql("update metadata_items set guid = 'local://' || id where id in (3, 4)")
out["rows_after"] = sql("select id, guid, title from metadata_items where id in (3, 4) order by id")
out["server"] = subprocess.check_output(["sh", "-c", "curl -s -H 'X-Plex-Token: %s' http://100.85.222.1:32411/identity | grep -o 'version=\"[0-9][^\"]*' | cut -d'\"' -f2" % tok]).decode().split()[-1]  # /identity also carries apiVersion="1.0"
print(json.dumps(out))
'''


def main():
    run = subprocess.run(["ssh", "ben@kids", "python3", "-"], input=("JOB = %r\n" % json.dumps(JOB) + REMOTE).encode(),
                         capture_output=True, check=True)
    result = json.loads(run.stdout)
    if "--shared" in sys.argv[2:]:
        shared = subprocess.run(["ssh", "ben@kids", "python3", "-"], input=SHARED.encode(), capture_output=True, check=True)
        result["shared"] = json.loads(shared.stdout)
    with open(sys.argv[1], "w") as out:
        json.dump(result, out, indent=1)
        out.write("\n")
    for case in result["cases"]:
        same = case.get("same_as") or {}
        print("%-24s %s %s rows=%s identical=%s" % (case["name"], case["status"], same.get("path", ""),
              [r["ratingKey"] for r in case["rows"] or []], same.get("identical")))
    for case in (result.get("shared") or {}).get("cases", []):
        print("shared %s %s %s" % (case["status"], case["path"], [r["ratingKey"] for r in case["rows"] or []]))


if __name__ == "__main__":
    main()
