"""A photo library pushed to rex as plexscratch's PMS holds it, and the replay of Plex Web against it.

plexscratch (PMS 1.43.4) was given a photo library on 2026-11-04 through Plex Web's own Add Library
(Photos), on a folder of Pepper&Carrot pages (David Revoy, CC BY 4.0) laid out as below, and Plex
Web 4.160.0 walked it while every exchange was recorded (`probes/photo_capture_fixture.py`,
`fixtures/photos/web-photos.jsonl`). This rig makes stand-in pictures of the same names, formats and
sizes with FFmpeg (no page is checked in), serves them from a plain Range server registered as a
source that declares its `photos` directory a photo library, and pushes each as `kind: photo` with
its ffprobe, as a source would.
"""
import json
import os
import re
import subprocess
import tempfile

import music_rig as M

FIXTURES = os.path.join(M.HERE, "fixtures", "photos")
REFERENCE_MACHINE = "12081b20eeb2f1ca3be0473ea444236d942d156a"

# plexscratch's folder: each picture's path below the library and its size (`cover.jpg` is the
# Episode 2 title banner; the sketch a 320-pixel copy of a page).
PICTURES = [
    ("cover.jpg", 1200, 287),
    ("Pepper&Carrot/Episode 01 - Potion of Flight/page 1.jpg", 1200, 1660),
    ("Pepper&Carrot/Episode 01 - Potion of Flight/page 2.jpg", 1200, 1660),
    ("Pepper&Carrot/Episode 01 - Potion of Flight/page 10.jpg", 1200, 1660),
    ("Pepper&Carrot/Episode 02 - Rainbow Potions/E02P01.jpg", 1200, 1660),
    ("Pepper&Carrot/Episode 02 - Rainbow Potions/E02P02.png", 1200, 1660),
    ("Pepper&Carrot/Episode 02 - Rainbow Potions/E02P03.jpg", 1200, 1660),
    ("Pepper&Carrot/Episode 02 - Rainbow Potions/Sketches/sketch.jpg", 320, 443),
    ("Witch Test v01 (2019) (Digital).cbz/001.jpg", 1200, 1660),
    ("Witch Test v01 (2019) (Digital).cbz/002.jpg", 1200, 1660),
    ("Witch Test v01 (2019) (Digital).cbz/010.jpg", 1200, 1660),
]
SEEDED = [{"key": "1", "type": "movie", "title": "Movies", "uuid": "0" * 39 + "1", "Location": [{"id": 1, "path": "/data/movies"}]},
          {"key": "2", "type": "show", "title": "TV Shows", "uuid": "0" * 39 + "2", "Location": [{"id": 2, "path": "/data/shows"}]}]


def load():
    return [json.loads(line) for line in open(os.path.join(FIXTURES, "web-photos.jsonl"))]


def make_pictures(root):
    """The pictures, each a flat colour of its size, and the items pushing them."""
    items = []
    for index, (path, width, height) in enumerate(PICTURES):
        file = os.path.join(root, "photos", path)
        os.makedirs(os.path.dirname(file), exist_ok=True)
        subprocess.run([M.tool("ffmpeg"), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x%02x6040:s=%dx%d" % (index * 20, width, height), "-frames:v", "1", file], check=True)
        probe = json.loads(subprocess.check_output([M.tool("ffprobe"), "-v", "error", "-print_format", "json",
                                                    "-show_format", "-show_streams", file]))
        items.append({"id": "photos/" + path, "path": "photos/" + path, "kind": "photo",
                      "size": os.path.getsize(file), "media_info": probe})
    return items


def start():
    work = tempfile.mkdtemp(prefix="rex-photos-")
    items = make_pictures(os.path.join(work, "media"))
    bytes_port, handler = M.range_server(os.path.join(work, "media"))
    agent_url, _ = M.agent_server([])
    rex = M.Rex(work, bytes_port, agent_url, extra_sections=SEEDED, music=False,
                register={"directories": [{"name": "photos", "photos": "Photos"}]})
    return work, items, rex, handler


def reference_rows():
    """plexscratch's rows, by key: every row its recorded answers name (title and parent)."""
    rows = {}
    for record in load():
        body = record.get("res_body") or ""
        if not body.startswith("{"):
            continue
        for row in json.loads(body).get("MediaContainer", {}).get("Metadata", []):
            if row.get("type") == "photo":
                rows.setdefault(row["ratingKey"], row)
    return rows


def key_map(rex, section):
    """plexscratch's keys to rex's, by each row's title and its parent's title, and its Parts' ids."""
    ours, parts = {}, {}

    def walk(key, parent_title):
        for row in rex.rows("/library/metadata/%s/children" % key if key else "/library/sections/%s/all" % section):
            ours[(row["title"], parent_title)] = row["ratingKey"]
            for media in row.get("Media", []):
                for part in media.get("Part", []):
                    parts[(row["title"], parent_title)] = str(part["id"])
            if row["key"].endswith("/children"):
                walk(row["ratingKey"], row["title"])
    walk(None, None)
    theirs = reference_rows()
    mapping, part_map = {}, {}
    titles = {}
    for title, parent in ours:
        titles.setdefault(title, []).append(parent)
    for key, row in theirs.items():
        parent = theirs.get(row.get("parentRatingKey", ""), {}).get("title")
        # A row whose album no recorded answer lists (`sketch`'s `Sketches`): by its title alone.
        if (row["title"], parent) not in ours and len(titles.get(row["title"], [])) == 1:
            parent = titles[row["title"]][0]
        if (row["title"], parent) in ours:
            mapping[key] = ours[(row["title"], parent)]
            for media in row.get("Media", []):
                for part in media.get("Part", []):
                    if (row["title"], parent) in parts:
                        part_map[str(part["id"])] = parts[(row["title"], parent)]
    return mapping, part_map


def rewrite_record(record, mapping, part_map, section):
    """A recorded request with plexscratch's keys, section and machine swapped for rex's."""
    extra = {REFERENCE_MACHINE: M.MACHINE, "/library/sections/10": "/library/sections/%s" % section,
             "/hubs/sections/10": "/hubs/sections/%s" % section,
             "%2Flibrary%2Fsections%2F10%2F": "%2Flibrary%2Fsections%2F" + section + "%2F"}
    path = re.sub(r"^/hubs/metadata/(\d+)", lambda m: "/hubs/metadata/" + mapping.get(m.group(1), m.group(1)), record["path"])
    return M.rewrite(path, mapping, part_map, extra), extra


def send(rex, record, mapping, part_map, section):
    """`music_rig.send` with Parts swapped inside a nested `url=` too (`/photo/:/transcode`'s)."""
    path, extra = rewrite_record(record, mapping, part_map, section)
    query = [[name, M.rewrite(value, mapping, part_map, extra)] for name, value in record.get("query") or []]
    return M.send(rex, dict(record, query=query), mapping, part_map, extra, path=path)
