#!/usr/bin/env python3
"""A debrid-like source: :32641 (loopback) answers every GET with a 302 to :32642 on the LAN,
which serves the file with real Range support, as a provider CDN does.
cdn.py <serve dir> <lan ip>"""
import os, re, sys, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import unquote

ROOT, LAN = sys.argv[1], sys.argv[2]


class Redirect(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", f"http://{LAN}:32642{self.path}")
        self.send_header("Content-Length", "0")
        self.end_headers()
    do_HEAD = do_GET


class Files(BaseHTTPRequestHandler):
    def do_GET(self, head=False):
        p = os.path.abspath(os.path.join(ROOT, unquote(self.path.split("?")[0]).lstrip("/")))
        if not p.startswith(os.path.abspath(ROOT) + "/") or not os.path.isfile(p):
            self.send_error(404); return
        size = os.path.getsize(p)
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", ""))
        if m:
            if m[1]:
                start = int(m[1]); end = int(m[2]) if m[2] else size - 1
            else:
                start = max(0, size - int(m[2]))
            if start >= size:
                self.send_response(416); self.send_header("Content-Range", f"bytes */{size}"); self.end_headers(); return
            end = min(end, size - 1)
            self.send_response(206); self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Type", "video/mp4" if p.endswith(".mp4") else "video/x-matroska")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        if head:
            return
        with open(p, "rb") as f:
            f.seek(start); left = end - start + 1
            try:
                while left > 0:
                    b = f.read(min(1 << 20, left))
                    if not b: break
                    self.wfile.write(b); left -= len(b)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_HEAD(self): self.do_GET(head=True)

    def log_message(self, fmt, *a):  # one line per read, for evidence of who downloads
        sys.stderr.write(f"CDN {self.client_address[0]} {self.command} {self.path} {self.headers.get('Range', '-')} {a[1] if len(a) > 1 else ''}\n")


threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", 32641), Redirect).serve_forever, daemon=True).start()
ThreadingHTTPServer(("0.0.0.0", 32642), Files).serve_forever()
