#!/usr/bin/env python3
"""streams.py <token file> <ratingKey>...: rex's audio/subtitle streams per item."""
import json, sys, urllib.request

tok = open(sys.argv[1]).read().strip()
for k in sys.argv[2:]:
    r = urllib.request.Request(f"http://127.0.0.1:32640/library/metadata/{k}", headers={"X-Plex-Token": tok, "Accept": "application/json"})
    m = json.load(urllib.request.urlopen(r))["MediaContainer"]["Metadata"][0]
    p = m["Media"][0]["Part"][0]
    out = []
    for s in p["Stream"]:
        if s["streamType"] < 2:
            continue
        flags = "".join(f for f, on in ((" D", s.get("default")), (" F", s.get("forced")), (" SEL", s.get("selected"))) if on)
        out.append(f"[{s['id']}:i{s.get('index')} {s.get('codec')} {s.get('languageCode', '-')} {s.get('title', '')[:12]}{flags}]")
    print(m["title"][:3], "part", p["id"], " ".join(out))
