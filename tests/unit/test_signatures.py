"""Phase 1b: finding the game functions live delivery uses in a game version it hasn't seen, by signature."""

import json
import struct

import pytest

from crimsonipelago.game_adapter.runtime.signatures import (
    FoundTargets,
    Signature,
    code_sections,
    load_signatures,
    parse_pattern,
    r3_from,
    scan,
)

BASE = 0x140000000
TEXT_RVA, TEXT_SIZE = 0x1000, 0x30000


def _image(code_at: dict[int, bytes], *, sections=((".text", TEXT_RVA, TEXT_SIZE, 0x60000020),
                                                    (".data", 0x40000, 0x1000, 0xC0000040))) -> dict[int, bytes]:
    """A fake module in memory: DOS and PE headers, a section table, and code with `code_at` placed in it."""
    header = bytearray(0x1000)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, 0x80)
    header[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HH", header, 0x84 + 2, len(sections), 0)      # NumberOfSections at +6 of the PE header
    struct.pack_into("<H", header, 0x80 + 20, 0xF0)                  # SizeOfOptionalHeader
    table = 0x80 + 24 + 0xF0
    for n, (name, rva, size, flags) in enumerate(sections):
        entry = table + 40 * n
        header[entry:entry + 8] = name.encode().ljust(8, b"\0")
        struct.pack_into("<II", header, entry + 8, size, rva)
        struct.pack_into("<I", header, entry + 36, flags)
    text = bytearray(b"\xcc" * TEXT_SIZE)
    for rva, data in code_at.items():
        text[rva - TEXT_RVA:rva - TEXT_RVA + len(data)] = data
    return {BASE: bytes(header), BASE + TEXT_RVA: bytes(text)}


def _reader(image, unreadable=()):
    def read(addr, size):
        if any(lo <= addr < hi for lo, hi in unreadable):
            return None
        for start, data in image.items():
            if start <= addr and addr + size <= start + len(data):
                return data[addr - start:addr - start + size]
        return None
    return read


FN = bytes.fromhex("4c8bdc49895b0849897b105549 8d6bf1 4881ecf0000000488bda".replace(" ", ""))


def _sigs():
    fn = Signature("give", "function", tuple(FN[:10]) + (None, None) + tuple(FN[12:]))
    # mov rbx, [rip+disp] (48 8b 1d disp32) after a recognisable lead-in
    glob = Signature("item_table", "global", parse_pattern("0f b7 39 48 8b 1d ?? ?? ?? ?? 3b 7b 08"),
                     disp_at=6, insn_end=10)
    return {"give": fn, "item_table": glob}


def test_scan_finds_a_function_and_works_out_a_global_from_the_code_reading_it():
    reads_table = 0x2F000
    lead = bytes.fromhex("0fb739488b1d") + struct.pack("<i", 0x6D69AA8 - (reads_table + 10)) + bytes.fromhex("3b7b08")
    image = _image({0x2781780 - 0x2770000: FN, reads_table: lead})
    found = scan(_reader(image), BASE, _sigs())
    assert found.complete and found.targets == {"give": 0x2781780 - 0x2770000, "item_table": 0x6D69AA8}
    assert found.scanned == TEXT_SIZE                                   # only the executable section


def test_scan_finds_a_match_across_the_seam_between_batches(monkeypatch):
    from crimsonipelago.game_adapter.runtime import signatures

    monkeypatch.setattr(signatures, "BATCH", 0x1000)
    image = _image({TEXT_RVA + 0x1000 - 7: FN})                          # straddles the first batch's end
    found = scan(_reader(image), BASE, {"give": _sigs()["give"]})
    assert found.targets == {"give": TEXT_RVA + 0x1000 - 7} and found.matches["give"] == [TEXT_RVA + 0x1000 - 7]


def test_scan_refuses_a_signature_found_twice_or_not_at_all():
    image = _image({0x2000: FN, 0x8000: FN})
    found = scan(_reader(image), BASE, _sigs())
    assert found.problems == {"give": "found 2 times", "item_table": "not found"} and not found.complete


def test_scan_skips_unreadable_code():
    image = _image({0x9000: FN})
    found = scan(_reader(image, unreadable=[(BASE + 0x3000, BASE + 0x4000)]), BASE, {"give": _sigs()["give"]})
    assert found.targets == {"give": 0x9000}


def test_code_sections_reads_the_section_table():
    assert code_sections(_reader(_image({})), BASE) == [(TEXT_RVA, TEXT_SIZE)]
    with pytest.raises(ValueError):
        code_sections(lambda a, n: b"\0" * n, BASE)


def test_shipped_signatures_load_and_cover_live_delivery():
    sigs, defaults = load_signatures()
    assert set(sigs) == {"probe", "give", "runner", "item_table", "knowledge_table"}
    assert all(len(s.pattern) >= 40 for s in sigs.values())
    assert sigs["item_table"].kind == "global" and 0 < sigs["item_table"].disp_at < sigs["item_table"].insn_end
    r3 = r3_from(scan(_reader(_image({})), BASE, {}), defaults)
    assert r3["reason"] == "0x0b" and r3["knowledge_live"] is True and "_about" not in r3


def test_found_targets_are_kept_per_build(tmp_path):
    record = FoundTargets(tmp_path / "found-targets.json")
    assert record.get(5) is None
    found = scan(_reader(_image({0x2000: FN})), BASE, {"give": _sigs()["give"]})
    record.put(5, r3_from(found, {"reason": "0x0b"}), "1.0.0.3000", found)
    assert record.get(5) == {"reason": "0x0b", "give": "0x2000", "found_by": "signatures"}
    assert json.loads((tmp_path / "found-targets.json").read_text())["5"]["game"] == "1.0.0.3000"
