#!/usr/bin/env python3
"""plezytracks.py <outdir> <CODE>...: in Plezy, play each variant from the start, switch to every audio track
and every subtitle track in its Audio & Subtitles sheet, then seek; screenshot each state (performance overlay on)."""
import os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(__file__))
import ui  # noqa: E402

OUT = sys.argv[1]
A = ["adb", "-s", os.environ.get("ADB_SERIAL", "127.0.0.1:15555"), "shell"]


def sh(*a): subprocess.run(A + list(a), capture_output=True)
def shot(path): open(path, "wb").write(subprocess.run(["adb", "-s", os.environ.get("ADB_SERIAL", "127.0.0.1:15555"), "exec-out", "screencap", "-p"], capture_output=True).stdout)
def zen(cmd): return subprocess.run(["ssh", os.environ.get("REXLAB_SSH", "zen"), cmd], capture_output=True, text=True).stdout
def tap(b): sh("input", "tap", str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))


def cols():
    nodes = [(re.sub(r"\s+", " ", l), b) for l, b, c in ui.nodes()]
    if not any(l.startswith(" | Audio & Subtitles Audio Subtitles") for l, b in nodes):
        return None
    items = [(l[3:], b) for l, b in nodes if l.startswith(" | ") and len(l) > 3 and b[1] >= 500 and b[0] in (345, 1265)]
    return [x for x in items if x[1][0] == 345], [x for x in items if x[1][0] == 1265]


def sheet():
    """open Audio & Subtitles; returns (audio labels, subtitle labels), every entry of both columns."""
    for _ in range(5):
        open_sheet()
        c = cols()
        if c:
            audio = [l for l, b in c[0]]
            sh("input", "swipe", "1720", "600", "1720", "950", "300"); time.sleep(0.8)
            top = [l for l, b in cols()[1]]
            sh("input", "swipe", "1720", "950", "1720", "560", "300"); time.sleep(0.8)
            subs = list(dict.fromkeys(top + [l for l, b in cols()[1]]))
            return audio, subs
        time.sleep(1)
    raise RuntimeError("sheet did not open")


def choose(kind, label):
    """with the sheet open, tap the entry named label in the audio or subtitle column."""
    for swipe in (None, ("600", "950"), ("950", "560")):
        if swipe:
            sh("input", "swipe", "1720", swipe[0], "1720", swipe[1], "300"); time.sleep(0.8)
        c = cols()
        if not c:
            open_sheet(); c = cols()
            if not c:
                return False
        a, s_ = c
        hit = [b for l, b in (a if kind == "a" else s_) if l == label]
        if hit:
            tap(hit[0]); return True
    return False


def close_sheet():
    sh("input", "tap", "2078", "354"); time.sleep(0.6)  # the sheet's X; a pick may already have closed it


def open_sheet():
    sh("input", "tap", "1791", "116"); time.sleep(1.2)  # works when the controls are showing
    if not cols():
        sh("input tap 1200 760; sleep 0.4; input tap 1791 116"); time.sleep(1.2)


def overlay():
    """switch on Plezy's performance overlay (codec/channels/bitrate of the audio mpv decodes)."""
    for _ in range(3):
        sh("input", "tap", "1665", "116"); time.sleep(1.2)
        if not ui.find(r"Playback Speed"):
            sh("input tap 1200 760; sleep 0.4; input tap 1665 116"); time.sleep(1.2)
        if ui.find(r"Playback Speed"):
            break
    for _ in range(3):
        hit = ui.find(r"Performance Overlay")
        if hit:
            tap(hit[0][1]); time.sleep(0.8); break
        sh("input", "swipe", "1260", "1000", "1260", "450", "300"); time.sleep(0.8)
    close_sheet()


def reset(code):
    zen("T=$(cat ~/rex-ffprobe-lab/.owner_token); for k in $(curl -s -H \"X-Plex-Token: $T\" -H \"Accept: application/json\" "
        "http://127.0.0.1:32640/library/sections/1/all | python3 -c \"import sys,json; print(' '.join(m['ratingKey'] for m in "
        f"json.load(sys.stdin)['MediaContainer']['Metadata'] if m['title'].startswith('{code}')))\"); do curl -s -o /dev/null "
        "-H \"X-Plex-Token: $T\" \"http://127.0.0.1:32640/:/unscrobble?key=$k&identifier=com.plexapp.plugins.library\"; done")


def start(code, o, tag):
    reset(code)
    sh("am", "force-stop", "com.edde746.plezy")
    sh("monkey", "-p", "com.edde746.plezy", "-c", "android.intent.category.LAUNCHER", "1"); time.sleep(8)
    for rx in (r"\| Later$", r"\| Reconnect$"):
        if ui.find(rx):
            tap(ui.find(rx)[0][1]); time.sleep(8)
    tap(ui.find(r"Search\sTab")[0][1]); time.sleep(2)
    sh("input", "text", code); time.sleep(5)
    tap(ui.find(rf"^ \| {code} .*movie")[0][1]); time.sleep(4)
    open(f"{o}/details-{tag}.txt", "w").write("\n".join(f"{b} {l}" for l, b, c in ui.nodes() if l.strip(" |")))
    t0 = zen("date +%H:%M:%S").strip()
    tap(ui.find(r"\| (Play|Resume)$")[0][1]); time.sleep(8)
    return t0


for code in sys.argv[2:]:
    o = f"{OUT}/{code}"; os.makedirs(o, exist_ok=True)
    try:
        t0 = start(code, o, "a")
        overlay()
        shot(f"{o}/00-start.png")
        audio, subs = sheet()
        open(f"{o}/sheet.txt", "w").write("AUDIO\n" + "\n".join(audio) + "\nSUBS\n" + "\n".join(subs))
        close_sheet()
        n = 0
        for phase, kind, labels in (("a", "a", audio), ("s", "s", subs)):
            if phase == "s":
                sh("input", "keyevent", "BACK"); time.sleep(1); sh("input", "keyevent", "BACK"); time.sleep(1)
                start(code, o, "s")
            for label in labels:
                n += 1
                open_sheet()
                ok = choose(kind, label)
                time.sleep(0.8)
                if cols():
                    close_sheet()
                time.sleep(3)
                shot(f"{o}/{n:02d}-{kind}.png")
                if kind == "a":  # the overlay only shows with the controls up
                    sh("input", "tap", "1200", "760"); time.sleep(0.7)
                    shot(f"{o}/{n:02d}-{kind}-ctl.png")
                    sh("input", "tap", "1200", "760"); time.sleep(0.5)
                print(code, f"{n:02d}-{kind}", label, "" if ok else "NOT FOUND", flush=True)
        sh("input tap 1200 760; sleep 0.4; input tap 1483 927"); time.sleep(5)
        shot(f"{o}/seek.png")
        sh("input", "keyevent", "BACK"); time.sleep(1); sh("input", "keyevent", "BACK"); time.sleep(1)
        log = zen(f"journalctl --user -u rexlab --since {t0} --no-pager -o cat")
        open(f"{o}/rex.log", "w").write("\n".join(x for x in log.splitlines() if re.search(r"INFO|WARN|ERROR|PUT /library/parts|transcode/universal/(decision|start)", x)))
    except Exception:
        import traceback; open(f"{o}/error.txt", "w").write(traceback.format_exc()); print(code, "ERROR", traceback.format_exc(), flush=True)
