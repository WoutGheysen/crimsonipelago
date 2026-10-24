"""CI check against the real plugin in the test host: write watch, memory search and compare-and-set writes."""

import sys
import time

from crimsonipelago.game_adapter.runtime.bridge import BridgeError, ResearchBridge

MARKER = 0x00C0FFEE12345678
WATCHED = 0x00BADC0DE5EED000
FN_MARKER = 0x00F00DF00DF00D00
BAG_MAGIC = 0x00BA6BA6BA6BA600
TAG = 0x007A67007A67007A

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

    # R2c: an execute watch on the host's add_to_bag(bag, 3, 0x33, 0x44, 0x55)
    bridge.scan(FN_MARKER, width=8, align=8)
    marker = [a for a in bridge.rescan(FN_MARKER).hits if base <= a < base + size]
    assert len(marker) == 1, marker
    fn = bridge.read_int(marker[0] + 8)
    assert base <= fn < base + size, hex(fn)
    slot, threads = bridge.watch(fn, 1, "execute", follow="0:16:64")
    time.sleep(1.0)
    got = bridge.hits(8)
    assert bridge.unwatch() >= 1
    calls = [r for r in got.records if r.get("args")]
    print(f"execute watch: {len(calls)} call(s); first args {calls[0]['args'][:5] if calls else None}")
    assert calls, got
    call = calls[0]
    args = [int(a, 16) for a in call["args"]]
    assert args[1] == 3 and args[2] == 0x33 and args[3] == 0x44 and args[4] == 0x55, call["args"]
    bag = bytes.fromhex(call["memory"]["0"])
    assert int.from_bytes(bag[:8], "little") == BAG_MAGIC, bag[:16].hex()
    assert call["rip"] == f"CrimsonDesert.exe+{hex(fn - base)}", (call["rip"], hex(fn - base))
    followed = bytes.fromhex(call["follow"][0]["hex"])
    assert int.from_bytes(followed[:8], "little") == TAG, call["follow"]
    time.sleep(0.5)                                         # the game (host) kept running after each call
    assert bridge.read_int(fn) is not None
    print("execute watch, arguments, memory and a followed pointer: ok")

    # R3: a job on the main thread, entered through tick(), with memory of our own and a byte set first
    main_tid = call["tid"]
    tick_fn, job_fn = bridge.read_int(marker[0] + 16), bridge.read_int(marker[0] + 24)
    buf = bridge.alloc(64)
    bridge.poke(buf, bytes(range(64)))
    assert bridge.read(buf, 4) == bytes(range(4))
    try:
        bridge.poke(fn, b"\x90")
        raise AssertionError("poke outside our memory must be refused")
    except BridgeError:
        pass
    args = [buf] + list(range(2, 15))
    state = bridge.job(job_fn, args, runner=tick_fn, tid=main_tid, set_bytes={buf + 40: 0x5A})
    assert state == "queued", state
    deadline = time.time() + 5
    status = bridge.job_status()
    while status["state"] in ("queued", "running") and time.time() < deadline:
        time.sleep(0.1)
        status = bridge.job_status()
    bridge.unwatch()
    print("job:", status)
    assert status["state"] == "done" and int(status["rax"], 16) == 0x600D, status
    assert bridge.read_int(buf) == sum(range(2, 15)), bridge.read(buf, 16).hex()
    assert bridge.read_int(buf + 8) == main_tid == status["tid"], (bridge.read_int(buf + 8), main_tid, status)
    assert bridge.read(buf + 40, 1) == b"\x5a"
    print("job on the game thread, own memory and set bytes: ok")

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
