#!/usr/bin/env python3
"""The server's memory stays near its library's, at the start and under the home screen.

The production rex (2026-10-10), 19,600 rows: 3.5 GB resident and 1.5 GB swapped. heaptrack and
`/proc` on a copy of its database found the start holding every row twice (the saved
document beside the restored catalog, 1.58 GB) and requests copying whole libraries --
a page of 50 shows +650 MB, an episode count +1.4 GB at peak, `/hubs` +1.9 GB -- kept
afterwards by the allocator.

Its shape (`rust/tests/fixtures/fun_show_shape.json`, first 120 shows) with every
episode a copy of one real row (`fixtures/fun_episode_row.json`) is saved to a state
database by one server and restored by a second, the path the production server takes at every
start. The bounds are ratios to the rows' own JSON size and to the resident size after the start,
so they hold on any host. Linux only: it reads `/proc/<pid>/status`. Measured on the test host:
unfixed, the start peaked at 16.6x the rows' JSON and the requests added 1,440 MB; fixed,
8.2x and 6 MB. Each stream was then a map of its own, half of the production server's 2.6 GB on
2026-10-16; held as text, the restored library is 4.0x its JSON (8.1x before, arm64 Docker), and with
every map, list and string fitted to its size as it is read (`catalog::compact`, 2026-10-18;
302 MB of the production server's 1.61 GB was room parsing grew into) 3.2x.
An unpaged listing of every episode built its body in several full copies the allocator then
kept (the production server's library: +2.4 GB a request, 6.5 GB after three); here two of them left
+131 MB on 363 MB before and +19 MB on 182 MB after.

A claimed start is handed the owner's audio and subtitle settings by plex.tv's account refresh, on
a thread of its own, and compares every Part's selection with them. Every row it read was kept for a
request's trim, so it held the whole library parsed at once: the production server's start peaked at
1.57 GB of heap, 1,199 MB of it those rows (45,328 rows, heaptrack, 2026-11-02), where an unclaimed
one stays near its idle 95 MB. Here, with a stub plex.tv and an account wanting the rows' own Russian
audio and no subtitles, which re-selects the one episode in fifty left on its English track (92):
unfixed, 140 MB at peak, 3.1x the rows' JSON where the same start unclaimed peaks at 0.8x; read a
row at a time and let go of, 42 MB, 0.9x (arm64 Docker, 2026-11-02).

    python3 tests/test_perf_memory.py
"""
import copy
import http.client
import http.server
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

from server_command import server_command

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1",
           REX_PLEXTV_URL=os.environ.get("REX_PLEXTV_URL", "http://127.0.0.1:9"))
for name in ("REX_STATE", "REX_TOKEN", "TMDB_KEY", "PLEX_TOKEN"):
    ENV.pop(name, None)
HEADERS = {"X-Plex-Product": "Plex Web", "X-Plex-Platform": "Chrome",
           "X-Plex-Client-Identifier": "perf-memory", "Accept": "application/json"}
SHOWS = 120
# Each Part's streams are held as their JSON text (`catalog::PACKED_STREAMS`).
RESIDENT = 3.6
# A restart over the same catalog: every row held at once, the start peaked at 3.5x the rows' JSON
# and stayed there until a request (a copy of the production server's 42,052 rows: 1,992 MB peak,
# 1,870 MB for 40 s idle); filed one at a time and left in the file, 1.0x and 435 MB (2026-10-24).
RESTART_PEAK = 2.0
RESTART_RESIDENT = 1.2
# The claimed start: its settings pass reads every row, and keeps only those it re-selects.
CLAIMED_PEAK = 2.0
RESELECTED = 50
# The owner's /api/v2/user profile (a real account's, test_track_prefs.py), wanting Russian audio and
# no subtitles: what the episode row already plays.
PROFILE = ('<profile autoSelectAudio="1" defaultAudioAccessibility="0" defaultAudioLanguage="ru" '
           'defaultAudioLanguages="" defaultSubtitleLanguage="" defaultSubtitleLanguages="" '
           'autoSelectSubtitle="0" defaultSubtitleAccessibility="0" defaultSubtitleForced="0" '
           'watchedIndicator="1" mediaReviewsVisibility="0" mediaReviewsLanguages="" mediaPostsVisibility="1"/>')
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  -- " + str(detail)[:600]),
          flush=True)


def free_port():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def request(port, path):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=600)
    try:
        connection.request("GET", path, headers=dict(HEADERS, Connection="close"))
        response = connection.getresponse()
        response.read()
        return response.status
    finally:
        connection.close()


def memory(pid):
    """(resident, peak resident) in MB."""
    fields = {}
    with open("/proc/%d/status" % pid) as handle:
        for line in handle:
            name, _, value = line.partition(":")
            fields[name] = value
    return int(fields["VmRSS"].split()[0]) / 1024, int(fields["VmHWM"].split()[0]) / 1024


def catalog(directory):
    """catalog_ui.json plus the production server's first SHOWS shows; and the JSON size of every row."""
    data = json.load(open(os.path.join(HERE, "catalog_ui.json")))
    shape = json.load(open(os.path.join(ROOT, "rust", "tests", "fixtures",
                                        "fun_show_shape.json")))[:SHOWS]
    episode = json.load(open(os.path.join(HERE, "fixtures", "fun_episode_row.json")))
    key = 400000
    for number, seasons in enumerate(shape):
        key += 1
        show = str(key)
        data["items"].append({"ratingKey": show, "key": "/library/metadata/%s/children" % show,
                              "type": "show", "title": "Show %03d" % number, "librarySectionID": 2,
                              "addedAt": 1789180050, "updatedAt": 1789180050, "_scanned": True})
        for season_index, count in enumerate(seasons, 1):
            key += 1
            season = str(key)
            data["items"].append({"ratingKey": season, "type": "season", "index": season_index,
                                  "title": "Season %d" % season_index, "parentRatingKey": show,
                                  "librarySectionID": 2, "addedAt": 1789180050, "_scanned": True})
            for index in range(1, count + 1):
                key += 1
                row = copy.deepcopy(episode)
                row.update({"ratingKey": str(key), "key": "/library/metadata/%d" % key,
                            "index": index, "parentIndex": season_index, "parentRatingKey": season,
                            "grandparentRatingKey": show, "librarySectionID": 2,
                            "addedAt": 1789180050 + key, "viewOffset": 1000 if key % 97 == 0 else None,
                            "_scanned": True})
                if row["viewOffset"] is None:
                    del row["viewOffset"]
                if key % RESELECTED == 0:
                    streams = row["Media"][0]["Part"][0]["Stream"]
                    del streams[1]["selected"]
                    streams[9]["selected"] = True
                data["items"].append(row)
    path = os.path.join(directory, "catalog.json")
    with open(path, "w") as handle:
        json.dump(data, handle)
    rows_mb = sum(len(json.dumps(row)) for row in data["items"]) / 1e6
    # the production server's catalog file is a seed with no rows: the library is the state's own.
    seed = os.path.join(directory, "seed.json")
    shutil.copy(os.path.join(HERE, "catalog_ui.json"), seed)
    return path, seed, rows_mb


class PlexTv(http.server.BaseHTTPRequestHandler):
    """plex.tv for the claimed start: the owner's profile, and an empty answer to the rest."""

    def log_message(self, *args):
        pass

    def answer(self):
        # `REX_PLEXTV_URL` sends `<base>/<host><path>`.
        if self.path.split("?")[0] == "/servers.plex.tv/api/v2/user":
            body = '<?xml version="1.0" encoding="UTF-8"?>\n<user id="1" username="owner">%s</user>\n' % PROFILE
        else:
            body = '<?xml version="1.0" encoding="UTF-8"?>\n<MediaContainer size="0"/>\n'
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_PUT = do_POST = answer


def start(path, state, plextv=None, log=None):
    """A server over `path`: suite-claimed, or claimed for real against the stub plex.tv at port
    `plextv` (its `claim_state.json` beside the catalog), logging to `log`."""
    port = free_port()
    claimed = ("--assert-claimed",) if plextv is None else ()
    env = ENV if plextv is None else dict(ENV, REX_PLEXTV_URL="http://127.0.0.1:%d" % plextv)
    output = subprocess.DEVNULL if log is None else open(log, "ab")
    process = subprocess.Popen(
        server_command("--port", port, "--catalog", path, "--state", state, *claimed,
                       "--gdm", "off", root=ROOT),
        cwd=ROOT, env=env, stdout=output, stderr=subprocess.STDOUT)
    for _ in range(1800):
        if process.poll() is not None:
            break
        try:
            if request(port, "/identity") == 200:
                return process, port
        except OSError:
            pass
        time.sleep(0.1)
    process.kill()
    raise SystemExit("server did not start")


def stop(process):
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=120)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def main():
    if not sys.platform.startswith("linux"):
        print("SKIP  reads /proc/<pid>/status (Linux only)")
        return
    directory = tempfile.mkdtemp(prefix="rex-perf-memory-")
    process = None
    try:
        path, seed, rows_mb = catalog(directory)
        state = os.path.join(directory, "state.db")
        # The first server writes the rows into the state database...
        process, port = start(path, state)
        request(port, "/library/sections/2/all?X-Plex-Container-Start=0&X-Plex-Container-Size=1")
        time.sleep(3)
        stop(process)
        # ...and the second restores them from it over the seed, as the production server does at
        # every start.
        process, port = start(seed, state)
        time.sleep(35)  # past one trim interval
        loaded, load_peak = memory(process.pid)
        print("rows %.0f MB of JSON; after the start %.0f MB resident, %.0f MB peak"
              % (rows_mb, loaded, load_peak), flush=True)
        check("the start peaks under 12x the rows' JSON (%.1fx)" % (load_peak / rows_mb),
              load_peak < 12 * rows_mb, (load_peak, rows_mb))
        check("the restored library holds under %.1fx its rows' JSON (%%.1fx)" % RESIDENT % (loaded / rows_mb),
              loaded < RESIDENT * rows_mb, (loaded, rows_mb))

        for path_ in ("/hubs", "/library/sections/2/all?X-Plex-Container-Start=0&X-Plex-Container-Size=50",
                      "/library/sections/2/all?type=4&X-Plex-Container-Start=0&X-Plex-Container-Size=0",
                      "/library/recentlyAdded?X-Plex-Container-Start=0&X-Plex-Container-Size=50"):
            if request(port, path_) != 200:
                check("%s answers" % path_, False)
        _, peak = memory(process.pid)
        print("after /hubs, a show page, an episode count and recently added: %.0f MB peak" % peak,
              flush=True)
        check("those requests raise the peak by under a third of the started size (+%.0f MB)"
              % (peak - load_peak), peak - load_peak < loaded / 3, (peak, load_peak, loaded))
        # Every episode, unpaged, twice -- as a client syncing the library asks for it.
        for _ in range(2):
            if request(port, "/library/sections/2/all?type=4") != 200:
                check("the unpaged episode listing answers", False)
            time.sleep(1)
        # Given back, not given back within a second: the cache trims after the request, but the
        # allocator returns pages to the OS in its own time, later on a loaded host (the nightly
        # that runs beside zurg's build read +16 MB of 45 MB where idle runs read +3 MB of 54 MB,
        # 2026-11-07), so wait for the resident size to settle, past one trim interval.
        settled = time.time() + 40
        while True:
            after, _ = memory(process.pid)
            if after - loaded < loaded / 4 or time.time() > settled:
                break
            time.sleep(2)
        print("after two unpaged episode listings: %.0f MB resident" % after, flush=True)
        check("what an unpaged listing built is given back (+%.0f MB of %.0f MB)" % (after - loaded, loaded),
              after - loaded < loaded / 4, (after, loaded))
        stop(process)
        # Every start after that is over the same seed: the state's rows are filed as they are read
        # and stay in the file, and the memory a restore used goes back without waiting for a request.
        process, port = start(seed, state)
        time.sleep(8)
        resident, peak = memory(process.pid)
        print("restarted over the same catalog: %.0f MB resident, %.0f MB peak, no request" % (resident, peak),
              flush=True)
        check("a restart over the same catalog peaks under %.1fx the rows' JSON (%.1fx)" % (RESTART_PEAK, peak / rows_mb),
              peak < RESTART_PEAK * rows_mb, (peak, rows_mb))
        check("...and holds under %.1fx of it before any request (%.1fx)" % (RESTART_RESIDENT, resident / rows_mb),
              resident < RESTART_RESIDENT * rows_mb, (resident, rows_mb))
        if request(port, "/library/sections/2/all?X-Plex-Container-Start=0&X-Plex-Container-Size=50") != 200:
            check("a page of the restored library answers", False)
        stop(process)
        # Claimed, the same start is handed the owner's track settings by plex.tv's account refresh.
        machine = json.load(open(seed))["machineIdentifier"]
        with open(os.path.join(directory, "claim_state.json"), "w") as handle:
            json.dump({"machine": machine, "token": "perf-memory-server-token", "owner": "owner",
                       "email": "owner@example.com", "feature_uuids": [], "account_id": 1,
                       "view_state_cursor": None, "access_tokens": []}, handle)
        stub = http.server.ThreadingHTTPServer(("127.0.0.1", 0), PlexTv)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        log = os.path.join(directory, "claimed.log")
        process, port = start(seed, state, plextv=stub.server_address[1], log=log)
        tracks = None
        for _ in range(3000):
            with open(log, errors="replace") as handle:
                tracks = next((line for line in handle if "TRACKS account settings" in line), None)
            if tracks is not None:
                break
            time.sleep(0.1)
        time.sleep(1)
        resident, peak = memory(process.pid)
        stub.shutdown()
        expected = sum(1 for row in json.load(open(path))["items"]
                       if row.get("type") == "episode" and int(row["ratingKey"]) % RESELECTED == 0)
        print("claimed, the owner's track settings read: %.0f MB resident, %.0f MB peak; %s"
              % (resident, peak, (tracks or "no TRACKS line").strip()), flush=True)
        check("the settings re-select the %d episodes left on another track" % expected,
              tracks is not None and (" %d item(s) re-selected" % expected) in tracks, tracks)
        check("a claimed start peaks under %.1fx the rows' JSON (%.1fx)" % (CLAIMED_PEAK, peak / rows_mb),
              peak < CLAIMED_PEAK * rows_mb, (peak, rows_mb))
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        shutil.rmtree(directory, ignore_errors=True)
    print("%d/%d passed" % (sum(results), len(results)))
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
