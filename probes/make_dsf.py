#!/usr/bin/env python3
"""Write a 2 s stereo DSD64 DSF (Sony's DSF 1.01 layout): a 1 kHz tone as first-order delta-sigma
bits, LSB first, in 4096-byte blocks per channel. FFmpeg reads DSD but writes none, so this made the
`.dsd` and `.dsf` of `tests/fixtures/music/audio-formats.json` (`probes/record_audio_formats.py`).

    python3 probes/make_dsf.py dsd.dsd
"""
import math
import struct
import sys

RATE, SECONDS, CHANNELS, BLOCK = 2822400, 2, 2, 4096


def main():
    samples = RATE * SECONDS
    channels = []
    for _ in range(CHANNELS):
        bits = bytearray((samples + 7) // 8)
        integral, previous = 0.0, 0.0
        for n in range(samples):
            integral += 0.5 * math.sin(2 * math.pi * 1000 * n / RATE) - previous
            previous = 1.0 if integral >= 0 else -1.0
            if integral >= 0:
                bits[n >> 3] |= 1 << (n & 7)
        channels.append(bits)
    body = bytearray()
    for block in range((len(channels[0]) + BLOCK - 1) // BLOCK):
        for bits in channels:
            chunk = bits[block * BLOCK:(block + 1) * BLOCK]
            body += chunk + bytes(BLOCK - len(chunk))
    fmt = struct.pack("<4sQIIIIIIQI4x", b"fmt ", 52, 1, 0, 2, CHANNELS, RATE, 1, samples, BLOCK)
    data = struct.pack("<4sQ", b"data", 12 + len(body)) + body
    head = struct.pack("<4sQQQ", b"DSD ", 28, 28 + len(fmt) + len(data), 0)
    open(sys.argv[1], "wb").write(head + fmt + data)


if __name__ == "__main__":
    main()
