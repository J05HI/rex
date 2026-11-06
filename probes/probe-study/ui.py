#!/usr/bin/env python3
"""Tiny adb UI helper: ui.py dump | find <regex> | tap <regex> [n] | shot <file>"""
import os, re, subprocess, sys, xml.etree.ElementTree as ET

DEV = ["adb", "-s", os.environ.get("ADB_SERIAL", "127.0.0.1:15555")]


def sh(*a): return subprocess.run(DEV + list(a), capture_output=True, text=True).stdout


def nodes():
    sh("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    root = ET.fromstring(subprocess.run(DEV + ["exec-out", "cat", "/sdcard/ui.xml"], capture_output=True).stdout)
    for n in root.iter("node"):
        label = (n.get("text") or "") + " | " + (n.get("content-desc") or "")
        b = list(map(int, re.findall(r"\d+", n.get("bounds"))))
        yield label, b, n.get("class")


def find(rx): return [(l, b, c) for l, b, c in nodes() if re.search(rx, l, re.I)]


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "dump":
        for l, b, c in nodes():
            if l.strip(" |"):
                print(b, l.replace("\n", " ")[:160])
    elif cmd == "find":
        for l, b, c in find(sys.argv[2]):
            print(b, l.replace("\n", " ")[:160])
    elif cmd == "tap":
        hits = find(sys.argv[2])
        i = int(sys.argv[3]) if len(sys.argv) > 3 else 0
        if not hits:
            sys.exit(f"no match for {sys.argv[2]}")
        l, b, _ = hits[i]
        sh("shell", "input", "tap", str((b[0] + b[2]) // 2), str((b[1] + b[3]) // 2))
        print("tapped", l.replace("\n", " ")[:100])
