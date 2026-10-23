"""CI check against the real plugin in the test host: link, memory search and a compare-and-set write."""

import sys

from crimsonipelago.game_adapter.runtime.bridge import ResearchBridge

MARKER = 0x00C0FFEE12345678

with ResearchBridge() as bridge:
    print("connected:", bridge.info.describe())
    report = bridge.scan_u64(MARKER)
    print(f"scan: {report.count} hit(s) in {report.ms} ms")
    if report.count < 1:
        sys.exit("marker not found")
    # The value also turns up in transient copies (the scan's own buffers, stack slots); the test host's
    # variable is the one inside its executable's image.
    base, size = int(bridge.info.module_base, 16), bridge.info.module_size
    again = bridge.rescan_u64(MARKER)
    in_image = [a for a in again.hits if base <= a < base + size]
    assert len(in_image) == 1, (again.hits, hex(base), size)
    addr = in_image[0]
    assert bridge.read(addr, 8) == MARKER.to_bytes(8, "little")
    ok, found = bridge.write_u64(addr, MARKER + 5, MARKER + 1)        # wrong expectation: must not write
    assert not ok and found == MARKER, (ok, found)
    ok, found = bridge.write_u64(addr, MARKER, MARKER + 1)
    assert ok, found
    assert bridge.read(addr, 8) == (MARKER + 1).to_bytes(8, "little")
    print("memory search, read and compare-and-set write: ok")
