"""CI check against the real plugin in the test host: write watch, memory search and compare-and-set writes."""

import sys
import time

from crimsonipelago.game_adapter.runtime.bridge import ResearchBridge

MARKER = 0x00C0FFEE12345678
WATCHED = 0x00BADC0DE5EED000

with ResearchBridge() as bridge:
    print("connected:", bridge.info.describe())
    base, size = int(bridge.info.module_base, 16), bridge.info.module_size

    # R2: watch the host's variable that it rewrites every 100 ms
    bridge.scan(WATCHED, width=8, align=8)
    found = [a for a in bridge.rescan(WATCHED).hits if base <= a < base + size]
    assert len(found) == 1, found
    slot, threads = bridge.watch(found[0], 8)
    assert slot == 0 and threads >= 1, (slot, threads)
    time.sleep(1.5)
    got = bridge.hits()
    print(f"watch: {got.total} record(s), {got.lost} lost, sites {got.sites}; first: {got.records[:1]}")
    assert sum(got.sites.values()) >= 5 and got.records, got
    first = got.records[0]
    assert first["rip"].startswith("CrimsonDesert.exe+0x") and first["value"] == WATCHED, first
    assert first["frames"] and all(f.startswith("CrimsonDesert.exe+0x") for f in first["frames"][:2]), first
    # function bounds from the unwind data: at least one frame on the path has them
    bounds = []
    for frame in first["frames"]:
        addr = base + int(frame.split("+")[1], 16)
        begin, end = bridge.function_bounds(addr)
        if begin:
            assert begin <= addr < end, (hex(addr), hex(begin), hex(end))
            bounds.append((frame, hex(begin - base), hex(end - base)))
    assert bounds, first["frames"]
    print("function bounds:", bounds)
    assert bridge.unwatch() >= 1
    time.sleep(0.3)
    after = sum(bridge.hits().sites.values())
    time.sleep(0.5)
    assert sum(bridge.hits().sites.values()) == after, "hits still arriving after unwatch"
    print("write watch, call chain and removal: ok")

    report = bridge.scan_u64(MARKER)
    print(f"scan: {report.count} hit(s) in {report.ms} ms")
    if report.count < 1:
        sys.exit("marker not found")
    # The value also turns up in transient copies (the scan's own buffers, stack slots); the test host's
    # variable is the one inside its executable's image.
    again = bridge.rescan_u64(MARKER)
    in_image = [a for a in again.hits if base <= a < base + size]
    assert len(in_image) == 1, (again.hits, hex(base), size)
    addr = in_image[0]
    assert bridge.read(addr, 8) == MARKER.to_bytes(8, "little")
    # the general search: a value range, 8-byte aligned
    ranged = bridge.scan(MARKER - 1, MARKER + 1, width=8, align=8)
    assert addr in ranged.hits, (hex(addr), ranged.hits)
    # 4-byte writes change only the low half
    low = MARKER & 0xFFFFFFFF
    ok, found = bridge.write(addr, low + 5, low + 1, width=4)          # wrong expectation: must not write
    assert not ok and found == low, (ok, found)
    ok, found = bridge.write(addr, low, low + 1, width=4)
    assert ok, found
    assert bridge.read_int(addr) == MARKER + 1, hex(bridge.read_int(addr))
    ok, found = bridge.write_u64(addr, MARKER + 1, MARKER + 2)
    assert ok, found
    assert bridge.read_int(addr) == MARKER + 2
    print("memory search (8- and 4-byte), read and compare-and-set writes: ok")
