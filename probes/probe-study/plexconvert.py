#!/usr/bin/env python3
"""plexconvert.py <outdir> <CODE>...: Plex for Android, pick tracks on the details page, play, switch the player to
720p so rex converts, and record which input streams rex's FFmpeg maps and what is on screen."""
import os, re, sys, time

sys.argv, ARGS = ["x", "/tmp/x"], sys.argv
src = open(os.path.join(os.path.dirname(__file__), "plextracks.py")).read().split("for code in sys.argv[2:]:")[0]
exec(compile(src, "plextracks", "exec"))
OUT = ARGS[1]
PLAYS = [("a2", 2, 0), ("a3", 3, 0), ("a1pgs", 1, 3)]

for code in ARGS[2:]:
    for name, an, sn in PLAYS:
        o = f"{OUT}/{code}/{name}"; os.makedirs(o, exist_ok=True)
        try:
            open_details(code)
            notes = [pick("Audio", an), pick("Subtitles", sn)]
            if any("only" in n or "no " in n for n in notes):
                print(code, name, "skip", notes, flush=True); continue
            t0 = zen("date +%H:%M:%S").strip()
            tap_node(ui.find(r"^(Watch|Resume[^|]*|Play[^|]*) \|")[0][1]); time.sleep(6)
            # player settings -> Quality -> 320p (720 kbps), below the source, so rex converts
            sh("input tap 1200 300; sleep 0.5; input tap 2295 830; sleep 1.5; input tap 1200 600; sleep 1.5; input swipe 1200 900 1200 420 900; sleep 0.8; input swipe 1200 900 1200 420 900; sleep 0.8; input swipe 1200 900 1200 420 900; sleep 1; input tap 1200 790")
            time.sleep(14)
            shot(f"{o}/conv.png")
            sh("input", "keyevent", "BACK"); time.sleep(1); sh("input", "keyevent", "BACK"); time.sleep(1)
            log = zen(f"journalctl --user -u rexlab --since {t0} --no-pager -o cat")
            open(f"{o}/rex.log", "w").write(log)
            starts = [re.sub(r".*start /library/metadata/\d+ ", "", x) for x in log.splitlines() if "TRANSCODE" in x and " start " in x]
            maps = [" ".join(re.findall(r"-map \S+|-filter_complex \S+|overlay\S*", x)) for x in log.splitlines() if re.search(r"TRANSCODE \S+ run \d+:", x)]
            print(code, name, "|", "; ".join(notes), "|", starts[:1], "|", maps[:1], flush=True)
        except Exception as e:
            print(code, name, "ERROR", repr(e), flush=True)
