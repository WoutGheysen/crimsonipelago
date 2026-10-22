"""Pearl Abyss archive checksum: Bob Jenkins' lookup3 (hashlittle) with seed 0xDEBA1DCD.

Used for PAPGT/PAMT header checksums, PAZ chunk checksums, directory-name hashes and the
per-file ChaCha20 nonce. Matches crimson-rs `calculate_checksum`.
"""

from __future__ import annotations

import struct

SEED = 0xDEBA1DCD
_M = 0xFFFFFFFF


def _rot(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & _M


def _mix(a: int, b: int, c: int) -> tuple[int, int, int]:
    for x, y, z, k in ((0, 2, 1, 4), (1, 0, 2, 6), (2, 1, 0, 8), (0, 2, 1, 16), (1, 0, 2, 19), (2, 1, 0, 4)):
        v = [a, b, c]
        v[x] = ((v[x] - v[y]) & _M) ^ _rot(v[y], k)
        v[y] = (v[y] + v[z]) & _M
        a, b, c = v
    return a, b, c


def _final(a: int, b: int, c: int) -> tuple[int, int, int]:
    c = ((c ^ b) - _rot(b, 14)) & _M
    a = ((a ^ c) - _rot(c, 11)) & _M
    b = ((b ^ a) - _rot(a, 25)) & _M
    c = ((c ^ b) - _rot(b, 16)) & _M
    a = ((a ^ c) - _rot(c, 4)) & _M
    b = ((b ^ a) - _rot(a, 14)) & _M
    c = ((c ^ b) - _rot(b, 24)) & _M
    return a, b, c


def checksum(data: bytes) -> int:
    a = b = c = (len(data) + SEED) & _M
    off, remaining = 0, len(data)
    while remaining > 12:
        w0, w1, w2 = struct.unpack_from("<III", data, off)
        a, b, c = _mix((a + w0) & _M, (b + w1) & _M, (c + w2) & _M)
        off += 12
        remaining -= 12
    if remaining == 0:
        return c
    tail = bytes(data[off:]) + b"\0" * (12 - remaining)
    w0, w1, w2 = struct.unpack("<III", tail)
    return _final((a + w0) & _M, (b + w1) & _M, (c + w2) & _M)[2]
