import struct

import pytest

from crimsonipelago.game_adapter import save_container as sc
from tests.fixtures.synthetic_save import make_blob, make_save


def test_roundtrip():
    blob = make_blob()
    data = sc.encode(blob, sc.new_header(2))
    decoded = sc.decode(data)
    assert decoded.blob == blob
    assert decoded.hmac_ok
    assert decoded.version == 2


def test_tampered_payload_fails_hmac():
    data = bytearray(make_save())
    data[sc.PAYLOAD_OFFSET + 3] ^= 0xFF
    with pytest.raises(sc.SaveIntegrityError):
        sc.decode(bytes(data))
    # diagnostic mode still reports it (decompression may also fail on garbage)
    try:
        assert not sc.decode(bytes(data), require_hmac=False).hmac_ok
    except sc.SaveFormatError:
        pass


def test_encode_preserves_unknown_header_bytes():
    header = bytearray(sc.new_header(2, flags=0x1234))
    header[0x08:0x12] = bytes(range(10))  # unknown fields
    header[0x70:0x80] = b"\xAA" * 16
    data = sc.encode(make_blob(), bytes(header))
    out = sc.decode(data).header
    assert out[0x08:0x12] == bytes(range(10))
    assert out[0x70:0x80] == b"\xAA" * 16
    assert struct.unpack_from("<H", out, sc.FLAGS_OFFSET)[0] == 0x1234


def test_version_1_key_supported():
    blob = make_blob()
    assert sc.decode(sc.encode(blob, sc.new_header(1))).blob == blob


def test_rejects_garbage():
    with pytest.raises(sc.SaveFormatError):
        sc.decode(b"NOPE" + b"\x00" * 200)
    with pytest.raises(sc.SaveFormatError):
        sc.decode(b"SAVE")
    header = bytearray(sc.new_header(2))
    struct.pack_into("<H", header, sc.VERSION_OFFSET, 99)
    with pytest.raises(sc.SaveFormatError):
        sc.decode(bytes(header) + b"\x00" * 64)


def test_known_nonce_is_deterministic():
    blob = make_blob()
    a = sc.encode(blob, sc.new_header(2), nonce=b"\x01" * 16)
    b = sc.encode(blob, sc.new_header(2), nonce=b"\x01" * 16)
    assert a == b
