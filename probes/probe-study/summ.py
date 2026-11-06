#!/usr/bin/env python3
"""summ.py <runs/app dir>: one line per variant from details.txt, ui dumps and rex.log."""
import os, re, sys

D = sys.argv[1]
for c in sorted(os.listdir(D)):
    p = lambda f: open(f"{D}/{c}/{f}", errors="replace").read() if os.path.exists(f"{D}/{c}/{f}") else ""
    det, log = p("details.txt"), p("rex.log")
    ui = " ".join(p(f) for f in ("ui15.txt", "ui_seek.txt", "ui_after.txt"))
    vid = re.search(r"\] (\S+ \([^)]*\)|[^|\n]*\(H[^|\n]*)\s\|", det)
    info = " / ".join(re.findall(r"\] ([^|\n]*(?:\(|minutes|hr|min)[^|\n]*?) \|", det))[:110] or re.sub(r"\s+", " ", det)[:110]
    dec = (re.search(r"DECISION (.*?) for", log) or [None, "-"])[1]
    tc = re.search(r"TRANSCODE \S+ start \S+ (.*)", log)
    times = [int(t) for t in re.findall(r"TIMELINE \d+ state=\w+ t=(\d+)ms", log)]
    states = [s for s in re.findall(r"TIMELINE \d+ state=(\w+)", log)]
    parts = re.findall(r"PART \d+ range=(\S+) -> (\d+)", log)
    errs = re.findall(r"(?:WARN|ERROR)\s+(.{0,140})", log) + re.findall(r"\] ([^|\n]*(?:rror|can't|cannot|unable|problem)[^|\n]*) \|", ui, re.I)
    print(f"{c}: {info}\n     decision={dec} {('tc: ' + tc[1][:90]) if tc else ''}\n     parts={parts[:5]} maxT={max(times) if times else '-'}ms states={'>'.join(dict.fromkeys(states))}")
    for e in dict.fromkeys(errs):
        print("     !", e[:150])
