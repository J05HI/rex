#!/usr/bin/env python3
"""A rex whose log pipe is gone still stops on SIGTERM.

The zurg+rex Android app reads rex's output through a pipe, and Android's `Process.destroy()`
closes that pipe as it sends SIGTERM. rex's shutdown logs "stopping" first, and writing a log
line to a closed stderr panicked the thread doing the shutdown: rex never exited, on a Pixel 6
(2026-11-09) not even minutes later, and kept holding its port and its state file.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

from server_command import server_command
from test_ports import free_port

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print("%s  %s%s" % ("PASS" if ok else "FAIL", name, "" if ok else "  -- " + detail))


with tempfile.TemporaryDirectory() as work:
    port = free_port()
    command = server_command("--port", port, "--catalog", os.path.join(work, "rex.db"), "--gdm", "off", root=ROOT)
    # The state file is what starts the saver that stops rex on SIGTERM; suites run without it.
    command.remove("--no-state")
    proc = subprocess.Popen(command, cwd=work, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = False
    for line in proc.stderr:
        if b"rex on http" in line:
            started = True
            break
    check("rex starts", started)
    # The handler is installed once the server is up, a moment after the line above.
    time.sleep(3)
    proc.stdout.close()
    proc.stderr.close()
    proc.send_signal(signal.SIGTERM)
    try:
        code = proc.wait(timeout=15)
        check("SIGTERM stops rex though nothing reads its log any more", code == 0, "exit %s" % code)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        check("SIGTERM stops rex though nothing reads its log any more", False, "still running 15 s after SIGTERM")

print("%d/%d checks passed" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
