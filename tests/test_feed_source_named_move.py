#!/usr/bin/env python3
"""A file its source placed by ids moves when the source places it on a show it names itself.

A source that names a show itself pushes its files `kind: episode` on its own numbers, with no ids,
one `release` and the show in `display` (rex-youtube's channels). zurg placed fun's One Pace files,
a fan re-edit of One Piece, by One Piece's ids until DMM numbered One Pace as a show of its own
(2026-11-09); zurg then pushed the same files that way, and rex moved none of them: under a One Piece
Plex's agent had named, a push that named nothing at Plex's agent changed nothing, and where nothing
named One Piece, the show a file sat in was retitled "One Pace" beside a second One Pace show made
for the rest.

`fixtures/feed/source_named_onepace_1109.json` is zurg's items for five of fun's One Pace releases,
both ways, and Plex's provider's answers for the first (its `_about` says how each was made). Then:

* claimed, One Piece named by Plex's agent, pushed a release at a time as zurg's change pushes go:
  Dressrosa's 48 files and Water Seven 08 leave One Piece for One Pace on their keys, with a person's
  watch state and locked title; One Piece, which still holds Reverse Mountain, keeps its title and
  guid; once the rest is pushed, One Piece and Netflix Stand Up Specials (where DMM had put the
  Specials) hold nothing and are gone, show and season, with the `deleted` frames a real PMS sends
  for them; the 51 files are one show, One Pace, each on its arc and part; pushed again, nothing changes;
* nothing names One Piece (no agent answer for it): the same push ends in one One Pace show, and
  the show Water Seven 08 sat in alone is gone, not retitled into a second;
* a person matched One Piece by hand: its rows stay where the person put them.

    python3 tests/test_feed_source_named_move.py
"""
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.parse

from test_feed import MOUNT, Bytes, Server, catalog, check, matches, results, serve

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = json.load(open(os.path.join(HERE, "fixtures", "feed", "source_named_onepace_1109.json")))
ONE_PIECE = "plex://show/5d9c084fec357c001f9ab4d8"
NETFLIX = "plex://show/5d9c0834e264b7001fc3ec53"
EPISODES = [item for item in FIXTURE["after"] if item.get("kind") == "episode"]
FIRST = [item for item in EPISODES if "Dressrosa" in item["id"] or "Water Seven 08" in item["id"]]
SHOWS = "/library/sections/2/all"
# A Dressrosa file a person watches, and one they retitle: rows of a file each.
DRESSROSA_WATCHED = next(item["id"] for item in EPISODES if "Chapter 720-721" in item["id"])
DRESSROSA_LOCKED = next(item["id"] for item in EPISODES if "Chapter 730-731" in item["id"])


def pushed(leg, items=None):
    """The leg's items as zurg pushes them, under the test mount, each with its probe."""
    wanted = None if items is None else {item["id"] for item in items}
    return [dict(item, file="%s/%s" % (MOUNT, item["path"]), media_info=FIXTURE["probes"][item["id"]])
            for item in FIXTURE[leg] if wanted is None or item["id"] in wanted]


def start(bytes_port, label, named=True):
    work = tempfile.mkdtemp(prefix="rex-source-named-")
    catalog(work)
    if named:
        cache_path = os.path.join(work, "plex_cache.json")
        cache = json.load(open(cache_path))
        cache.update(FIXTURE["plex"])
        json.dump(cache, open(cache_path, "w"))
    server = Server(work, bytes_port)
    status, body = server.feed("POST", "items", {"items": pushed("before")})
    check("%s: zurg's items as DMM placed them that day are taken" % label,
          status == 200 and not body.get("rejected"), body)
    return work, server


def deleted_since(server, mark):
    time.sleep(0.7)
    out = []
    for name, frame in server.events.frames[mark:]:
        if name == "timeline":
            entry = frame.get("TimelineEntry")
            out.extend(row for row in (entry if isinstance(entry, list) else [entry]) if row.get("state") == 9)
    return out


def one_pace(server, label, items=EPISODES):
    """Every file of `items` an episode of one show, One Pace as its source names it, on its arc and part."""
    keys = server.keys()
    rows = {item["id"]: server.row(keys.get(item["id"])) for item in items}
    shows = {row.get("grandparentRatingKey") for row in rows.values()}
    wrong = [(item["id"].rsplit("/", 1)[1][:40], row.get("parentIndex"), row.get("index")) for item in items
             for row in [rows[item["id"]]] if (row.get("parentIndex"), row.get("index")) != (item["season"], item["episode"])]
    check("%s: the %d files are one show, each on the arc and part its source placed it on" % (label, len(items)),
          len(shows) == 1 and not wrong, (shows, wrong[:5]))
    show = server.row(next(iter(shows)))
    check("%s: that show is One Pace, titled and described by its source, nothing of Plex's" % label,
          show.get("title") == "One Pace" and str(show.get("guid", "")).startswith("local://")
          and show.get("leafCount") == len(items) and "fan project" in (show.get("summary") or ""),
          (show.get("title"), show.get("guid"), show.get("leafCount"), (show.get("summary") or "")[:40]))
    retitled = [item["id"].rsplit("/", 1)[1][:40] for item in items
                if item["id"] != DRESSROSA_LOCKED and rows[item["id"]].get("title") != item["display"]["title"]]
    check("%s: each episode carries One Pace's title for it, but the one a person locked" % label, not retitled, retitled[:5])
    titles = [row.get("title") for row in server.rows(SHOWS)]
    check("%s: the library holds one One Pace" % label, titles.count("One Pace") == 1, titles)
    return show


def main():
    source = Bytes()
    for item in FIXTURE["before"]:
        source.files.add("/dav/" + item["path"])
    bytes_port = serve(source.handler())

    # -- claimed, One Piece named by Plex's agent: a release at a time ----------------------------------
    work, server = start(bytes_port, "named")
    try:
        before = server.keys()
        rows = [server.row(before[item["id"]]) for item in FIRST if "Dressrosa" in item["id"]]
        piece = {row.get("grandparentRatingKey") for row in rows}
        piece_key, piece_season = next(iter(piece), None), rows[-1].get("parentRatingKey")
        check("named: Dressrosa's 48 files are episodes of One Piece as Plex's agent names it",
              len(piece) == 1 and server.row(piece_key).get("guid") == ONE_PIECE, (piece, server.row(piece_key).get("guid")))
        netflix = server.row(before[next(item["id"] for item in EPISODES if "Whole Cake" in item["id"])])
        netflix_key, netflix_season = netflix.get("grandparentRatingKey"), netflix.get("parentRatingKey")
        check("named: Whole Cake Island 01 is an episode of Netflix Stand Up Specials, as DMM placed it",
              server.row(netflix_key).get("guid") == NETFLIX, server.row(netflix_key).get("title"))
        # One row of a file each, but Dressrosa's first two: a version on Reverse Mountain's S1E1 and S1E2
        # (Dressrosa came unnumbered, so its files took S1E1-E48 in file order).
        own = [item for item in EPISODES if list(before.values()).count(before[item["id"]]) == 1]
        watched, locked = before[DRESSROSA_WATCHED], before[DRESSROSA_LOCKED]
        server.request("/:/scrobble?key=%s&identifier=com.plexapp.plugins.library" % watched)
        query = urllib.parse.urlencode({"type": 4, "id": locked, "title.value": "Ours", "title.locked": 1})
        status, _ = server.request("/library/sections/2/all?" + query, "PUT")
        check("named: a person watches one Dressrosa file and retitles another", status == 200
              and server.row(watched).get("viewCount") == 1 and server.row(locked).get("title") == "Ours",
              (status, server.row(watched).get("viewCount"), server.row(locked).get("title")))

        mark = server.events.mark()
        status, body = server.feed("POST", "items", {"items": pushed("after", FIRST)})
        check("named: Dressrosa and Water Seven 08 placed on One Pace are taken", status == 200 and not body.get("rejected"), body)
        keys = server.keys()
        one_pace(server, "named, first push", FIRST)
        moved = [item["id"].rsplit("/", 1)[1][:40] for item in FIRST if item in own and keys.get(item["id"]) != before[item["id"]]]
        check("named: each file that was a row of its own is still that row, on its key", not moved, moved[:5])
        check("named: the Dressrosa file the person watched stays watched", server.row(watched).get("viewCount") == 1,
              server.row(watched).get("viewCount"))
        check("named: the title the person locked stays theirs", server.row(locked).get("title") == "Ours", server.row(locked).get("title"))
        left = server.row(piece_key)
        check("named: One Piece, still holding Reverse Mountain, keeps its title and Plex's guid",
              (left.get("title"), left.get("guid"), left.get("leafCount")) == ("One Piece", ONE_PIECE, 2),
              (left.get("title"), left.get("guid"), left.get("leafCount")))
        gone = [frame.get("itemID") for frame in deleted_since(server, mark)]
        check("named: no moved file is announced deleted", not set(gone) & {keys[item["id"]] for item in FIRST}, gone)

        mark = server.events.mark()
        status, body = server.feed("POST", "items", {"items": pushed("after", [item for item in FIXTURE["after"] if item not in FIRST])})
        check("named: the rest is taken", status == 200 and not body.get("rejected"), body)
        keys = server.keys()
        one_pace(server, "named, whole")
        moved = [item["id"].rsplit("/", 1)[1][:40] for item in EPISODES if item in own and keys.get(item["id"]) != before[item["id"]]]
        check("named: every placed file that was a row of its own is still that row, on its key", not moved, moved[:5])
        empty = {key: server.request("/library/metadata/%s" % key)[0] for key in (piece_key, piece_season, netflix_key, netflix_season)}
        check("named: One Piece and Netflix Stand Up Specials, left with nothing, are gone, show and season",
              set(empty.values()) == {404}, empty)
        listed = [row.get("ratingKey") for row in server.rows(SHOWS)]
        check("named: neither is listed", not {piece_key, netflix_key} & set(listed), listed)
        frames = {(frame.get("itemID"), frame.get("type")) for frame in deleted_since(server, mark)}
        check("named: each sends the `deleted` frames a real PMS sends for a show and season left empty",
              {(piece_key, 2), (piece_season, 3), (netflix_key, 2), (netflix_season, 3)} <= frames, sorted(frames))
        status, body = server.feed("POST", "items", {"items": pushed("after")})
        check("named: pushed again, nothing changes", status == 200 and not body.get("created") and not body.get("updated"), body)
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- nothing names One Piece: no second One Pace -----------------------------------------------------
    work, server = start(bytes_port, "unnamed", named=False)
    try:
        before = server.keys()
        alone = server.row(before[next(item["id"] for item in EPISODES if "Water Seven 08" in item["id"])]).get("grandparentRatingKey")
        status, body = server.feed("POST", "items", {"items": pushed("after")})
        check("unnamed: zurg's One Pace items are taken", status == 200 and not body.get("rejected"), body)
        one_pace(server, "unnamed")
        check("unnamed: the show Water Seven 08 sat in alone is gone, not retitled One Pace",
              server.request("/library/metadata/%s" % alone)[0] == 404, server.row(alone).get("title"))
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)

    # -- a person's match is theirs ------------------------------------------------------------------------
    work, server = start(bytes_port, "matched")
    try:
        before = server.keys()
        piece_key = server.row(before[DRESSROSA_WATCHED]).get("grandparentRatingKey")
        status, _ = server.request("/library/metadata/%s/match?guid=%s&name=%s&year=1999"
                                   % (piece_key, urllib.parse.quote(ONE_PIECE, safe=""), urllib.parse.quote("One Piece")), "PUT")
        listed = matches(server) or {}
        check("matched: a person Fix Matches One Piece; its source lists the match",
              status == 200 and any("Dressrosa" in release for release in listed), (status, list(listed)))
        server.feed("POST", "items", {"items": pushed("after")})
        keys = server.keys()
        stayed = [item for item in EPISODES if "Dressrosa" in item["id"]]
        wrong = [item["id"].rsplit("/", 1)[1][:40] for item in stayed
                 if (keys.get(item["id"]), server.row(keys.get(item["id"])).get("grandparentRatingKey")) != (before[item["id"]], piece_key)]
        check("matched: Dressrosa's files stay under the One Piece the person matched, on their keys", not wrong, wrong[:5])
        check("matched: One Piece keeps the person's match", server.row(piece_key).get("guid") == ONE_PIECE, server.row(piece_key).get("guid"))
    finally:
        server.stop()
        shutil.rmtree(work, ignore_errors=True)
    print("\n%d/%d passed" % (sum(results), len(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
