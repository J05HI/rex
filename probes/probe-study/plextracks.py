#!/usr/bin/env python3
"""plextracks.py <outdir> <CODE>...: in Plex for Android, play each variant with a matrix of track picks.
Picks are positions in the app's own sheets: audio n (1-based) and subtitle n (0 = Off)."""
import os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(__file__))
import ui  # noqa: E402

OUT = sys.argv[1]
A = ["adb", "-s", os.environ.get("ADB_SERIAL", "127.0.0.1:15555"), "shell"]
PLAYS = [("default", None, None), ("a2", 2, 0), ("a3", 3, 0), ("a1s1", 1, 1), ("s2", None, 2), ("s3", None, 3), ("s4", None, 4)]


def sh(*a): subprocess.run(A + list(a), capture_output=True)
def shot(path): open(path, "wb").write(subprocess.run(["adb", "-s", os.environ.get("ADB_SERIAL", "127.0.0.1:15555"), "exec-out", "screencap", "-p"], capture_output=True).stdout)
def zen(cmd): return subprocess.run(["ssh", os.environ.get("REXLAB_SSH", "zen"), cmd], capture_output=True, text=True).stdout
def top(): return re.search(r"u0 (\S+)/", subprocess.run(A + ["dumpsys activity activities | grep -m1 topResumedActivity"], capture_output=True, text=True).stdout or "u0 ?/")[1]


def tap_node(b): sh("input", "tap", str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))


def open_details(code):
    sh("am", "force-stop", "com.plexapp.android")
    sh("monkey", "-p", "com.plexapp.android", "-c", "android.intent.category.LAUNCHER", "1")
    for _ in range(15):
        time.sleep(1)
        if top() == "com.plexapp.android" and ui.find(r"^ \| Search$"):
            break
    else:
        raise RuntimeError("Plex did not come up")
    tap_node(ui.find(r"^ \| Search$")[0][1]); time.sleep(2)
    sh("input", "text", code); time.sleep(8)
    hit = ui.find(rf"^ \| {code} .*Movie$")
    if not hit:
        raise RuntimeError(f"{code} not found")
    tap_node(hit[0][1]); time.sleep(4)


def row(label):
    """the value node right of a details row label ('Audio' / 'Subtitles')."""
    nodes = list(ui.nodes())
    lab = next((b for l, b, c in nodes if l == f"{label} | "), None)
    if not lab:
        return None
    return next((b for l, b, c in nodes if l.startswith(" | ") and b[0] >= 300 and abs(b[1] - lab[1]) < 20), None)


def pick(label, n):
    b = row(label)
    if not b:
        return f"no {label} row"
    tap_node(b); time.sleep(2)
    nodes = list(ui.nodes())
    head = next(b for l, b, c in nodes if l in ("Audio Stream | ", "Subtitles | "))
    items = [(l, b) for l, b, c in nodes if l.startswith(" | ") and len(l) > 3 and b[0] == 42 and b[2] == 1038 and b[1] > head[1] and "Find Subtitles" not in l]
    if (n >= len(items)) if label == "Subtitles" else (n > len(items)):
        sh("input", "keyevent", "BACK"); time.sleep(1)
        return f"{label} has only {len(items)} entries"
    l, b = items[n] if label == "Subtitles" else items[n - 1]
    tap_node(b); time.sleep(2)
    return f"{label} -> {l[3:]}"


for code in sys.argv[2:]:
    for name, an, sn in PLAYS:
        o = f"{OUT}/{code}/{name}"; os.makedirs(o, exist_ok=True)
        try:
            open_details(code)
            notes = []
            if an is not None:
                notes.append(pick("Audio", an))
            if sn is not None:
                notes.append(pick("Subtitles", sn))
            if any("only" in n for n in notes):
                open(f"{o}/skip.txt", "w").write("\n".join(notes)); print(code, name, "skip", notes); continue
            det = "\n".join(f"{b} {l}" for l, b, c in ui.nodes() if l.strip(" |"))
            open(f"{o}/details.txt", "w").write(det + "\n" + "\n".join(notes))
            t0 = zen("date +%H:%M:%S").strip()
            w = ui.find(r"^(Watch|Resume[^|]*|Play[^|]*) \|")
            tap_node(w[0][1]); time.sleep(10)
            fg = top(); shot(f"{o}/play10.png")
            sh("input tap 1200 300; sleep 0.8; input swipe 376 964 1440 964 800"); time.sleep(7)
            shot(f"{o}/seek.png")
            sh("input", "keyevent", "BACK"); time.sleep(1); sh("input", "keyevent", "BACK"); time.sleep(1)
            log = zen(f"journalctl --user -u rexlab --since {t0} --no-pager -o cat")
            keep = [x for x in log.splitlines() if re.search(r"INFO|WARN|ERROR|TIMELINE|/library/parts/\d+\?|transcode/universal/(decision|start)", x)
                    and not re.search(r'"GET /(photo|library/metadata/\d+/thumb|service/ultrablur)', x)]
            open(f"{o}/rex.log", "w").write("\n".join(keep))
            dec = next((re.sub(r".*DECISION ", "", x) for x in keep if "DECISION" in x), "-")
            tc = next((re.sub(r".*start /library/metadata/\d+ ", "", x) for x in keep if "TRANSCODE" in x and " start " in x), "")
            print(code, name, "|", "; ".join(notes) or "as is", "|", fg, "|", dec[:40], "|", tc[:90], flush=True)
        except Exception as e:  # keep going; record the failure
            open(f"{o}/error.txt", "w").write(str(e)); print(code, name, "ERROR", e, flush=True)
