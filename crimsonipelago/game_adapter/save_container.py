"""Crimson Desert `save.save` container: header, ChaCha20 encryption, HMAC, LZ4.

Independent implementation of the container layout documented by the
CRIMSON-DESERT-SAVE-EDITOR-AND-GAME-MODS project (RicePaddySoftware / NattKh,
MPL-2.0). No code is copied from that project; the format facts (offsets,
key derivation constants, algorithms) are credited in NOTICE.md.

Layout (all little-endian):
    0x00  4   magic "SAVE"
    0x04  u16 container version (1 or 2 known)
    0x06  u16 flags
    0x12  u32 uncompressed payload size
    0x16  u32 encrypted payload size
    0x1A  16  ChaCha20 nonce (first 4 bytes are the initial block counter)
    0x2A  32  HMAC-SHA256 over the LZ4-compressed plaintext
    0x80  ..  encrypted payload: ChaCha20(LZ4-block(raw serializer blob))
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass

import lz4.block
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms

MAGIC = b"SAVE"
HEADER_SIZE = 0x80
VERSION_OFFSET = 0x04
FLAGS_OFFSET = 0x06
UNCOMPRESSED_SIZE_OFFSET = 0x12
PAYLOAD_SIZE_OFFSET = 0x16
NONCE_OFFSET = 0x1A
HMAC_OFFSET = 0x2A
PAYLOAD_OFFSET = 0x80

RAW_BLOB_MAGIC = b"\xFF\xFF\x04\x00"

_BASE_KEY = bytes.fromhex("C41B8E730DF259A637CC04E9B12F9668DA107A853E61F9224DB80AD75C13EF90")[:31]
_VERSION_PREFIXES = {
    1: b'^Qgbrm/.#@`zsr]\\@rvfal#"',
    2: b"^Pearl--#Abyss__@!!",
}
KNOWN_VERSIONS = frozenset(_VERSION_PREFIXES)


class SaveFormatError(ValueError):
    """The file is not a save container this code understands."""


class SaveIntegrityError(SaveFormatError):
    """The container decrypted but its HMAC does not match: corrupt or tampered."""


def derive_key(version: int) -> bytes:
    prefix = _VERSION_PREFIXES.get(version)
    if prefix is None:
        raise SaveFormatError(f"unsupported save container version {version}")
    material = prefix + b"PRIVATE_HMAC_SECRET_CHECK"
    return bytes(a ^ b for a, b in zip(_BASE_KEY, material)) + b"\x00"


def chacha20(data: bytes, key: bytes, nonce16: bytes) -> bytes:
    """ChaCha20 with a 16-byte nonce whose first 4 bytes are the LE block counter."""
    encryptor = Cipher(algorithms.ChaCha20(key, nonce16), mode=None).encryptor()
    return encryptor.update(data) + encryptor.finalize()


@dataclass(frozen=True)
class SaveContainer:
    header: bytes
    version: int
    flags: int
    nonce: bytes
    stored_hmac: bytes
    hmac_ok: bool
    blob: bytes  # decrypted + decompressed serializer blob

    @property
    def uncompressed_size(self) -> int:
        return len(self.blob)

    def describe(self) -> dict:
        return {
            "version": self.version,
            "flags": self.flags,
            "hmac_ok": self.hmac_ok,
            "uncompressed_size": self.uncompressed_size,
            "raw_blob_magic_ok": self.blob.startswith(RAW_BLOB_MAGIC),
        }


def decode(data: bytes, *, require_hmac: bool = True) -> SaveContainer:
    if len(data) < HEADER_SIZE + 16:
        raise SaveFormatError("file too small to be a save container")
    if data[:4] != MAGIC:
        raise SaveFormatError(f"bad magic {data[:4]!r}, expected b'SAVE'")
    version, flags = struct.unpack_from("<HH", data, VERSION_OFFSET)
    uncompressed_size, payload_size = struct.unpack_from("<II", data, UNCOMPRESSED_SIZE_OFFSET)
    nonce = data[NONCE_OFFSET:NONCE_OFFSET + 16]
    stored_hmac = data[HMAC_OFFSET:HMAC_OFFSET + 32]
    if PAYLOAD_OFFSET + payload_size > len(data):
        raise SaveFormatError(f"payload size {payload_size} exceeds file size {len(data)}")

    key = derive_key(version)
    compressed = chacha20(data[PAYLOAD_OFFSET:PAYLOAD_OFFSET + payload_size], key, nonce)
    hmac_ok = hmac.compare_digest(hmac.new(key, compressed, hashlib.sha256).digest(), stored_hmac)
    if require_hmac and not hmac_ok:
        raise SaveIntegrityError("HMAC mismatch: the save is corrupt, incomplete or was modified by another tool")
    try:
        blob = lz4.block.decompress(compressed, uncompressed_size=uncompressed_size)
    except lz4.block.LZ4BlockError as exc:
        raise SaveFormatError(f"LZ4 decompression failed: {exc}") from exc
    if len(blob) != uncompressed_size:
        raise SaveFormatError(f"decompressed {len(blob)} bytes, header says {uncompressed_size}")
    return SaveContainer(bytes(data[:HEADER_SIZE]), version, flags, nonce, stored_hmac, hmac_ok, blob)


def encode(blob: bytes, template_header: bytes, *, nonce: bytes | None = None) -> bytes:
    """Re-encode a blob, preserving every header byte except the fields we own.

    Unlike a fresh header, this keeps the original version, flags and any
    unknown header bytes untouched (scope doc 6.4: never discard unknown fields).
    """
    if len(template_header) != HEADER_SIZE or template_header[:4] != MAGIC:
        raise SaveFormatError("template header is not a valid save header")
    version = struct.unpack_from("<H", template_header, VERSION_OFFSET)[0]
    key = derive_key(version)
    compressed = lz4.block.compress(bytes(blob), store_size=False, mode="high_compression", compression=9)
    nonce = nonce if nonce is not None else os.urandom(16)
    if len(nonce) != 16:
        raise ValueError("nonce must be 16 bytes")
    header = bytearray(template_header)
    struct.pack_into("<II", header, UNCOMPRESSED_SIZE_OFFSET, len(blob), len(compressed))
    header[NONCE_OFFSET:NONCE_OFFSET + 16] = nonce
    header[HMAC_OFFSET:HMAC_OFFSET + 32] = hmac.new(key, compressed, hashlib.sha256).digest()
    return bytes(header) + chacha20(compressed, key, nonce)


def new_header(version: int = 2, flags: int = 0x0080) -> bytes:
    """A minimal header, used by tests and the simulated game only."""
    derive_key(version)  # validates the version
    header = bytearray(HEADER_SIZE)
    header[:4] = MAGIC
    struct.pack_into("<HH", header, VERSION_OFFSET, version, flags)
    return bytes(header)


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
