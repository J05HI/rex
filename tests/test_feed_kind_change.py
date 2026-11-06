#!/usr/bin/env python3
"""A version its source now places as the other kind of title leaves the row of several it was in.

A Discord user's zurg held two releases of Primal (2019) S03, an AC3 and a DTS-HD BDRip of ten
episodes each, which DMM once placed as the 2019 film. rex made them one film row holding both
releases' S03E01 and eighteen films of one file. DMM now places both as the show (TMDB 89456),
season 3 episodes 1-10, and zurg pushes each file again with `kind: "episode"` (2026-11-04): the
eighteen moved to the show, and for the row of two the feed answered `updated` and moved nothing:
it stayed a film in the movie library. Only a version re-placed on other numbers left a row of
several, and a probe digest (`probe_kept`) kept the row for the same reason, so a snapshot never
asked for it again.

`fixtures/feed/kind_change_primal_1104.json` is both releases' items as zurg's own feed code builds
them, placed as DMM answered that day and, before, as a film (a stand-in whose Plex answer
`duplicate_releases.json` recorded; its `_about` says why). Each release's hash, size and probe are
`duplicate_releases.json`'s two releases', not Primal's. The user's rex.db was not available: the
first leg pushes the film placement release by release, as zurg's change pushes go, which folds the
rows exactly as the user's were (one of two versions, eighteen of one). Then:

* the episode placement pushed whole, a release at a time, moves both versions of the film of two
  to the show's S3E1, on the film's key, as the other eighteen already went;
* a snapshot naming each probe by its digest alone asks for both versions whole (`wants`) and
  changes nothing until they come;
* a film of two a person Fix Matched stays as they matched it, its versions asked for by nothing;
* the other way, an episode of two releases placed as a film leaves the show;
* a rex with no show library takes the episodes as films: the film of two stays one film, and a
  later push or snapshot changes nothing (a version leaving there split into films of one on new
  keys, which then answered `updated` on every push);
* placed as episodes with no numbers (zurg's shape for a file DMM placed as a show's but could not
  number), the film of two stays the film, as a film of one does: rex numbers only new rows.

    python3 tests/test_feed_kind_change.py
"""
import json
import os
import shutil
import tempfile
import urllib.parse

from test_feed import LEGACY, MOVIE_GUID, Bytes, Server, check, matches, results, serve

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = json.load(open(os.path.join(HERE, "fixtures", "feed", "kind_change_primal_1104.json")))
RECORDED = json.load(open(os.path.join(HERE, "fixtures", "feed", "duplicate_releases.json")))
PROBES = {entry["entry"]["hash"]: entry["entry"]["media_info"] for entry in RECORDED["entries"]}
FILM = "plex://movie/5d77687d7e5fa10020bf080d"
RELEASES = sorted({item["release"] for item in FIXTURE["episode"]})


def pushed(leg, whole=True, release=None):
    """The leg's items, each with its release's probe, or by its digest alone (`whole` false)."""
    return [dict(item, media_info=PROBES[item["hash"]] if whole else None)
            for item in FIXTURE[leg] if release in (None, item["release"])]


def first(release):
    return next(item["id"] for item in FIXTURE["episode"] if item["release"] == release and item["episode"] == 1)


E01 = [first(release) for release in RELEASES]


def start(bytes_port, leg="film", sections=LEGACY["sections"]):
    work = tempfile.mkdtemp(prefix="rex-kind-")
    json.dump({"machineIdentifier": "00000000000000000000000000000000000000f7", "friendlyName": "rex-kind",
               "sections": sections, "items": []}, open(os.path.join(work, "catalog.json"), "w"))
    json.dump(RECORDED["plex"], open(os.path.join(work, "plex_cache.json"), "w"))
    server = Server(work, bytes_port)
    # As zurg's change pushes go: a release at a time.
    for release in RELEASES:
        server.feed("POST", "items", {"items": pushed(leg, release=release)})
    return work, server


def films(server):
    return [row for row in server.rows("/library/sections/1/all?includeGuids=1") if row.get("guid") == FILM]


def folded(server, label):
    """The rows the film placement left: S03E01 of both releases one film of two versions."""
    keys = server.keys()
    row = server.row(keys.get(E01[0]))
    check("%s: both releases' S03E01 are one film of two versions, the rest films of one" % label,
          keys.get(E01[0]) == keys.get(E01[1]) and row.get("type") == "movie" and len(row.get("Media") or []) == 2
          and sorted(len(film.get("Media") or []) for film in films(server)) == [1] * 18 + [2],
          (keys.get(E01[0]), keys.get(E01[1]), row.get("type"), [len(film.get("Media") or []) for film in films(server)]))
    return keys.get(E01[0])


def on_show(server, label):
    """Every file is the show's season 3 episode it was placed on, each episode one row of both releases."""
    keys = server.keys()
    wrong = []
    for item in FIXTURE["episode"]:
        row = server.row(keys.get(item["id"]))
        if (row.get("type"), str(row.get("librarySectionID")), row.get("parentIndex"), row.get("index"), len(row.get("Media") or [])) \
                != ("episode", "2", 3, item["episode"], 2):
            wrong.append((item["id"].rsplit("/", 1)[1][:40], keys.get(item["id"]), row.get("type"), row.get("librarySectionID"),
                          row.get("parentIndex"), row.get("index"), len(row.get("Media") or [])))
    check("%s: every file is the show's S3E1-E10 in TV Shows, each episode one row holding both releases" % label, not wrong, wrong)
    check("%s: no film is left in Movies" % label, films(server) == [], [(row.get("ratingKey"), row.get("title")) for row in films(server)])
    return keys


def main():
    source = Bytes()
    for item in FIXTURE["episode"]:
        source.files.add("/dav/" + item["path"])
    bytes_port = serve(source.handler())

    # -- whole items, pushed release by release as zurg's change pushes go ----------------------
    work, server = start(bytes_port)
    try:
        key = folded(server, "whole")
        for release in RELEASES:
            status, body = server.feed("POST", "items", {"items": pushed("episode", release=release)})
            check("whole: the episode placement of the %s release is taken" % ("DTS-HD" if "DTS" in release else "AC3"), status == 200 and not body.get("rejected"), body)
        keys = on_show(server, "whole")
        check("whole: both releases' S03E01 are on the film row's key, the key clients know",
              keys.get(E01[0]) == keys.get(E01[1]) == key, (keys.get(E01[0]), keys.get(E01[1]), key))
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- a snapshot by digest: the row of two cannot be kept as it is, so its items are asked for --
    work, server = start(bytes_port)
    try:
        key = folded(server, "digest")
        status, body = server.feed("PUT", "snapshot", {"items": pushed("episode", whole=False)})
        wants = (body or {}).get("wants") or []
        check("digest: the snapshot asks for both versions of the film of two whole",
              status == 200 and set(E01) <= set(wants), (status, body))
        row = server.row(key)
        check("digest: asking changes nothing: the film of two is as it was", row.get("type") == "movie"
              and len(row.get("Media") or []) == 2, (row.get("type"), len(row.get("Media") or [])))
        asked = [item for item in pushed("episode") if item["id"] in wants]
        status, body = server.feed("POST", "items", {"items": asked})
        check("digest: what it asked for, pushed whole, is taken", status == 200 and not body.get("rejected"), body)
        on_show(server, "digest")
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- a person's match is theirs ------------------------------------------------------------------
    work, server = start(bytes_port)
    try:
        key = folded(server, "matched")
        # Onto another film than the one the other seventeen rows are, which a match would merge it into.
        status, _ = server.request("/library/metadata/%s/match?guid=%s&name=%s&year=1992"
                                   % (key, urllib.parse.quote(MOVIE_GUID, safe=""), urllib.parse.quote("Blame It on the Bellboy")), "PUT")
        listed = matches(server) or {}
        check("matched: a person Fix Matches the film of two; its source lists the match",
              status == 200 and set(RELEASES) <= set(listed), (status, listed))
        status, body = server.feed("PUT", "snapshot", {"items": pushed("episode", whole=False)})
        check("matched: a snapshot by digest asks for neither of its versions",
              status == 200 and not set(E01) & set((body or {}).get("wants") or []), body)
        server.feed("POST", "items", {"items": pushed("episode")})
        keys = server.keys()
        row = server.row(key)
        check("matched: pushed whole as the show's S3E1, the film of two stays as the person matched it",
              keys.get(E01[0]) == keys.get(E01[1]) == key and row.get("type") == "movie" and row.get("guid") == MOVIE_GUID
              and str(row.get("librarySectionID")) == "1" and len(row.get("Media") or []) == 2,
              (keys.get(E01[0]), keys.get(E01[1]), key, row.get("type"), row.get("guid"), len(row.get("Media") or [])))
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- the other way: a version of an episode row of several placed as a film leaves it too ------
    work, server = start(bytes_port, "episode")
    try:
        on_show(server, "episodes first")
        for release in RELEASES:
            server.feed("POST", "items", {"items": pushed("film", release=release)})
        keys = server.keys()
        rows = [server.row(keys.get(item["id"])) for item in FIXTURE["film"]]
        check("then a film: placed as a film, no file is left an episode in TV Shows",
              all(row.get("type") == "movie" and str(row.get("librarySectionID")) == "1" for row in rows)
              and not server.rows("/library/sections/2/all"),
              sorted({(row.get("type"), row.get("librarySectionID")) for row in rows}))
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- episodes with no numbers: the film of two stays the film, as a film of one does --------------
    work, server = start(bytes_port)
    try:
        key = folded(server, "no numbers")
        unnumbered = [{field: value for field, value in item.items() if field not in ("season", "episode", "tmdb_episode")}
                      for item in pushed("episode") if item["id"] in E01]
        server.feed("POST", "items", {"items": unnumbered})
        keys = server.keys()
        row = server.row(key)
        check("no numbers: placed as episodes with none, the film of two stays the film on its key",
              keys.get(E01[0]) == keys.get(E01[1]) == key and row.get("type") == "movie" and len(row.get("Media") or []) == 2,
              (keys.get(E01[0]), keys.get(E01[1]), key, row.get("type"), len(row.get("Media") or [])))
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- no show library: the episodes are films there, and the film of two stays one ---------------
    movies = [dict(LEGACY["sections"][0], Location=[{"id": 1, "path": "/zurg"}])]
    work, server = start(bytes_port, sections=movies)
    try:
        key = folded(server, "no show library")
        server.feed("POST", "items", {"items": pushed("episode")})
        status, body = server.feed("POST", "items", {"items": pushed("episode")})
        keys = server.keys()
        row = server.row(key)
        check("no show library: placed as episodes, the film of two stays one film of two on its key",
              keys.get(E01[0]) == keys.get(E01[1]) == key and row.get("type") == "movie" and len(row.get("Media") or []) == 2,
              (keys.get(E01[0]), keys.get(E01[1]), key, row.get("type"), len(row.get("Media") or [])))
        check("no show library: pushed again, nothing changes", status == 200 and body.get("unchanged") == 20
              and not body.get("updated") and not body.get("created"), body)
        status, body = server.feed("PUT", "snapshot", {"items": pushed("episode", whole=False)})
        check("no show library: a snapshot by digest wants nothing and changes nothing",
              status == 200 and not body.get("wants") and body.get("unchanged") == 20, body)
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
