#!/usr/bin/env python3
"""Write eng.srt, styled.ass and forced.sup (HDMV PGS) for a 120 s 1920x1080 test file.
Each line names its own track and time, so a screenshot shows which track is on screen and its sync.
mksubs.py <out dir>"""
import os, struct, sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = sys.argv[1]
DUR = 120


def ts(t, sep=","):
    return f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{int(t % 60):02d}{sep}{int(round(t % 1 * 1000)):03d}"


def mmss(t): return f"{int(t // 60)}:{int(t % 60):02d}"


# SRT: a line every 2 s, shown 1.8 s
with open(f"{OUT}/eng.srt", "w") as f:
    for n, t in enumerate(range(0, DUR, 2), 1):
        f.write(f"{n}\n{ts(t)} --> {ts(t + 1.8)}\nSRT ENGLISH {mmss(t)}\n\n")

# ASS: top of screen, big yellow with a red outline, a line every 2 s
with open(f"{OUT}/styled.ass", "w") as f:
    f.write("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Top,DejaVu Sans,90,&H0000FFFF,&H000000FF,&H000000FF,&H00000000,-1,-1,0,0,100,100,0,0,1,6,0,8,40,40,60,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    for t in range(1, DUR, 2):
        f.write(f"Dialogue: 0,{ts(t, '.')[1:-1]},{ts(t + 1.8, '.')[1:-1]},Top,,0,0,0,,ASS STYLED {mmss(t)}\n")

# PGS: a forced line every 4 s, shown 3 s, bottom centre
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)


def bitmap(text):
    w, h = 900, 100
    img = Image.new("L", (w, h), 0)
    ImageDraw.Draw(img).text((w // 2, h // 2), text, font=FONT, fill=255, anchor="mm")
    fill = img.point(lambda v: 255 if v > 128 else 0)
    outline = fill.filter(ImageFilter.MaxFilter(7))
    px = [[1 if fill.getpixel((x, y)) else (2 if outline.getpixel((x, y)) else 0) for x in range(w)] for y in range(h)]
    return w, h, px


def rle(px):
    out = bytearray()
    for row in px:
        x = 0
        while x < len(row):
            c, n = row[x], 1
            while x + n < len(row) and row[x + n] == c and n < 16383:
                n += 1
            if c != 0 and n <= 2:
                out += bytes([c]) * n
            elif c == 0:
                out += bytes([0, n]) if n < 64 else bytes([0, 0x40 | n >> 8, n & 0xFF])
            else:
                out += bytes([0, 0x80 | n, c]) if n < 64 else bytes([0, 0xC0 | n >> 8, n & 0xFF, c])
            x += n
        out += b"\x00\x00"
    return bytes(out)


def seg(pts, kind, body):
    p = int(pts * 90000)
    return b"PG" + struct.pack(">IIBH", p, p, kind, len(body)) + body


def show(t, num, text):
    w, h, px = bitmap(text)
    x, y = (1920 - w) // 2, 1080 - h - 60
    data = rle(px)
    pcs = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, num, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0x40, x, y)  # 0x40 = forced
    wds = struct.pack(">BBHHHH", 1, 0, x, y, w, h)
    pal = struct.pack(">BB", 0, 0) + bytes([0, 16, 128, 128, 0, 1, 235, 128, 128, 255, 2, 16, 128, 128, 255])
    ods = struct.pack(">HBB", 0, 0, 0xC0) + (len(data) + 4).to_bytes(3, "big") + struct.pack(">HH", w, h) + data
    return seg(t, 0x16, pcs) + seg(t, 0x17, wds) + seg(t, 0x14, pal) + seg(t, 0x15, ods) + seg(t, 0x80, b"")


def clear(t, num, x=510, y=920, w=900, h=100):
    pcs = struct.pack(">HHBHBBBB", 1920, 1080, 0x10, num, 0x00, 0, 0, 0)
    return seg(t, 0x16, pcs) + seg(t, 0x17, struct.pack(">BBHHHH", 1, 0, x, y, w, h)) + seg(t, 0x80, b"")


with open(f"{OUT}/forced.sup", "wb") as f:
    n = 0
    for t in range(0, DUR, 4):
        f.write(show(t + 0.5, n, f"PGS FORCED {mmss(t + 0.5)}")); n += 1
        f.write(clear(t + 3.5, n)); n += 1

# self-check: RLE of a known row decodes back
def unrle(b, w):
    rows, row, i = [], [], 0
    while i < len(b):
        c = b[i]; i += 1
        if c:
            row.append(c); continue
        f = b[i]; i += 1
        if f == 0:
            rows.append(row); row = []; continue
        n = f & 0x3F
        if f & 0x40:
            n = n << 8 | b[i]; i += 1
        col = 0
        if f & 0x80:
            col = b[i]; i += 1
        row += [col] * n
    return rows


_w, _h, _px = bitmap("PGS FORCED 0:00")
assert unrle(rle(_px), _w) == _px, "RLE round trip failed"
print("wrote", sorted(os.listdir(OUT)))
