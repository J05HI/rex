#!/usr/bin/env python3
"""A library's language is the language Plex's agent is asked in (rex#9).

A real PMS sends its library's language with every call to Plex's metadata agent. plexlab's and
plexav's outbound captures (PMS 1.43.4, en-US libraries) record where: `x-plex-language: en-US` and
`x-plex-country: US` headers on `POST /library/metadata/matches` (`fixtures/plexmeta_matches_s143.json`),
and `X-Plex-Country=US&X-Plex-Language=en-US` last in the query of a GET of an item
(`fixtures/music/agent.json`). Until 2026-11-04 rex sent none, and asked music in en-US whatever
the library said, so a de-DE library ("Filme", haldir31's; Adan14boni's es-MX) was described in
English. Here, with plex.tv and the agent stubbed (test_plextv's stub, which keeps every request):

  (a) a de-DE film and show library: the matches carry de-DE/DE headers, a show's children GET
      de-DE/DE in its query, and Plex Web's Fix Match search asks in de-DE too;
  (b) the same titles in en-US libraries over (a)'s answer cache: asked again, in en-US/US, never
      served the de-DE answers; and back in de-DE, served from the cache without asking;
  (c) a de-DE music library asks for its album in de-DE/DE (the music rig's agent, plexav's In Rainbows).

    python3 tests/test_metadata_language.py
"""
import glob
import json
import os
import shutil
import sys
import tempfile
import threading

import test_plextv as plextv
from test_plextv import AGENT, CATALOG, SERVER_TOKEN, Instance, Stub, check, wait_for
from test_feed_named_on_claim import EPISODE, MOVIE, call, fresh, named, push
from test_ports import free_port

import music_rig


def language_of(record):
    """(language, country) a request to the agent named, in its headers and in its query."""
    headers, query = dict(record["headers"]), dict(record["query"])
    return ((headers.get("x-plex-language"), headers.get("x-plex-country")),
            (query.get("X-Plex-Language"), query.get("X-Plex-Country")))


def asked(stub):
    return [(r["method"], r["path"], language_of(r)) for r in stub.taken(AGENT)]


def libraries(directory, language):
    path = os.path.join(directory, "catalog.json")
    catalog = json.load(open(path))
    for section in catalog["sections"]:
        section["language"] = language
    json.dump(catalog, open(path, "w"))


def run(stub, directory, language):
    """A claimed rex over `directory` whose libraries are `language`, both titles pushed and named."""
    stub.clear()
    libraries(directory, language)
    rex = Instance(directory, free_port(), stub.server_address[1], plex_token=None, announce=None)
    try:
        status = push(rex)
        found = wait_for(lambda: named(rex), 20)
        return rex, status, found
    except Exception:
        rex.stop()
        raise


def films_and_shows():
    stub = Stub(free_port())
    threading.Thread(target=stub.serve_forever, daemon=True).start()
    directory = fresh(True)
    try:
        # -- (a) de-DE ------------------------------------------------------------------------
        rex, status, found = run(stub, directory, "de-DE")
        try:
            check("(a) a de-DE library takes and names both titles", status == 200 and found, found)
            matches = [r for r in stub.taken(AGENT, "/library/metadata/matches", "POST")]
            check("(a) every match carries de-DE/DE in its headers, as a PMS sends en-US/US",
                  matches and all(language_of(r)[0] == ("de-DE", "DE") for r in matches), asked(stub))
            children = stub.taken(AGENT, "/library/metadata/[0-9a-f]+/children", "GET")
            check("(a) the show's children are asked with de-DE/DE in the query",
                  children and all(language_of(r)[1] == ("de-DE", "DE") for r in children), asked(stub))
            stub.clear()
            status, body = call(rex, "GET", "/rex/feed/zurg/items")
            key = next(item["ratingKey"] for item in body["items"] if item["id"] == "%s/%s" % MOVIE[:2])
            status, _ = call(rex, "GET", "/library/metadata/%s/matches?manual=1&title=Sintel&year=2010&agent=tv.plex.agents.movie&language=de-DE" % key,
                             token=SERVER_TOKEN)
            search = stub.taken(AGENT, "/library/metadata/matches", "POST")
            check("(a) Fix Match's search asks in the library's de-DE",
                  status == 200 and search and all(language_of(r)[0] == ("de-DE", "DE") for r in search), (status, asked(stub)))
        finally:
            rex.stop()

        # -- (b) the same titles in en-US libraries, over (a)'s cache -------------------------
        english = fresh(True)
        for kept in glob.glob(os.path.join(directory, "plex_cache*")):
            shutil.copy(kept, english)
        try:
            rex, status, found = run(stub, english, "en-US")
            try:
                matches = stub.taken(AGENT, "/library/metadata/matches", "POST")
                check("(b) an en-US library is not served the de-DE answers: it asks again, in en-US/US",
                      found and matches and all(language_of(r)[0] == ("en-US", "US") for r in matches), asked(stub))
                children = stub.taken(AGENT, "/library/metadata/[0-9a-f]+/children", "GET")
                check("(b) and its show's children with en-US/US in the query",
                      children and all(language_of(r)[1] == ("en-US", "US") for r in children), asked(stub))
            finally:
                rex.stop()
            again = fresh(True)
            for kept in glob.glob(os.path.join(english, "plex_cache*")):
                shutil.copy(kept, again)
            try:
                rex, status, found = run(stub, again, "de-DE")
                try:
                    check("(b) back in de-DE, both are named from the cache without asking",
                          found and not stub.taken(AGENT, "/library/metadata/.*"), asked(stub))
                finally:
                    rex.stop()
            finally:
                shutil.rmtree(again, ignore_errors=True)
        finally:
            shutil.rmtree(english, ignore_errors=True)
    finally:
        stub.shutdown()
        shutil.rmtree(directory, ignore_errors=True)


def music():
    work = tempfile.mkdtemp(prefix="rex-metadata-language-")
    try:
        # plexav's In Rainbows, its first track as plexav's PMS probed it: identified on push, so no
        # bytes are made or read.
        track = json.load(open(os.path.join(music_rig.FIXTURES, "media.json")))["tracks"][0]
        mbid = "3b408cb5-7d51-4188-b07c-fabcf308cda3"
        relative = "music/Radiohead/In Rainbows (2007)/01 - %s.flac" % track["title"]
        item = {"id": relative, "path": relative, "kind": "track", "ids": {"mbid": mbid}, "disc": 1, "track": 1,
                "media_info": track["probe"], "release": "music/Radiohead/In Rainbows (2007)"}
        bytes_port, _ = music_rig.range_server(work)
        agent_url, heard = music_rig.agent_server()
        section = {"key": "1", "type": "artist", "title": "Musik", "agent": "tv.plex.agents.music",
                   "scanner": "Plex Music", "language": "de-DE", "uuid": "0000000000000000000000000000000000000a01",
                   "Location": [{"id": 1, "path": "/data/music"}]}
        rex = music_rig.Rex(work, bytes_port, agent_url, extra_sections=[section], music=False)
        try:
            status, _ = rex.push([item])
            reads = wait_for(lambda: [query for path, query in heard if path and path.startswith("/library/metadata/") and path != "/library/metadata/matches"], 20)
            match = [query for path, query in heard if path == "/library/metadata/matches"]
            check("(c) a de-DE music library matches its album by release id with no language, as plexav's PMS did",
                  status == 200 and match and all("X-Plex-Language" not in dict(query) for query in match), heard)
            check("(c) and reads the album in de-DE/DE, not en-US",
                  reads and all((dict(query).get("X-Plex-Language"), dict(query).get("X-Plex-Country")) == ("de-DE", "DE") for query in reads),
                  [entry for entry in heard if entry[0]])
        finally:
            rex.stop()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    films_and_shows()
    music()
    print("\n%d/%d passed" % (sum(plextv.results), len(plextv.results)))
    return 0 if all(plextv.results) else 1


if __name__ == "__main__":
    sys.exit(main())
