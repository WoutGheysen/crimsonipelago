"""Developer tool: make code signatures for the game functions live delivery uses, from code dumps.

A signature is the first bytes of a function, decoded instruction by instruction, with every byte that moves
when the game is rebuilt blanked out ("??"): RIP-relative displacements (globals, constants), and the targets
of relative calls and jumps. What's left is the function's own shape, which a game update usually doesn't change
unless it changes the function itself.

A global (a table pointer the code reads) gets a signature around an instruction that reads it, and the client
works the address out from that instruction's displacement in the running game.

    python -m crimsonipelago.tools.make_signatures DUMP.json [DUMP.json ...]

prints the "signatures" section for targets.json. Needs capstone (pip install capstone). The dumps are the
r2-code-*.json files `plugin dump-code` writes: game code, never committed. The signatures are short patterns,
mostly blanks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# What live delivery needs, as in targets.json's r3 section for build 1790086912 (game 2.03.02).
FUNCTIONS = {"probe": 0x277E080, "give": 0x2781780, "runner": 0x2774B20}
GLOBALS = {"item_table": (0x6D69AA8, 0x38AB60), "knowledge_table": (0x6D69A98, 0x22CE360)}   # (global, a reader)

MIN_BYTES = 72           # at least this much of a function's start (a prologue alone is common)
MIN_FIXED = 48           # ... with at least this many bytes that aren't blanked out
MAX_BYTES = 128


def _decoder():
    try:
        import capstone
    except ImportError:
        sys.exit("make_signatures needs capstone: pip install capstone")
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
    md.detail = True
    return md, capstone


def _blanks(insn, capstone) -> set[int]:
    """Offsets within the instruction that a rebuild moves: RIP-relative displacements, relative branch targets."""
    from capstone import x86

    out: set[int] = set()
    enc = insn.disp_offset, insn.disp_size
    for op in insn.operands:
        if op.type == x86.X86_OP_MEM and op.mem.base == x86.X86_REG_RIP and enc[1]:
            out.update(range(enc[0], enc[0] + enc[1]))
    if insn.group(capstone.CS_GRP_JUMP) or insn.group(capstone.CS_GRP_CALL):
        if insn.imm_size and any(op.type == x86.X86_OP_IMM for op in insn.operands):
            out.update(range(insn.imm_offset, insn.imm_offset + insn.imm_size))
    return out


def signature(code: bytes, start: int, md, capstone, *, min_bytes=MIN_BYTES, max_bytes=MAX_BYTES) -> list[str]:
    """Whole instructions from `start`, blanks where a rebuild moves things, until long enough."""
    out: list[str] = []
    fixed = 0
    for insn in md.disasm(code[start:start + max_bytes + 16], 0):
        if len(out) + insn.size > max_bytes:
            break
        blank = _blanks(insn, capstone)
        for i, b in enumerate(insn.bytes):
            out.append("??" if i in blank else f"{b:02x}")
            fixed += i not in blank
        if len(out) >= min_bytes and fixed >= MIN_FIXED:
            break
    return out


def load_code(paths: list[Path]) -> dict[int, bytes]:
    code: dict[int, bytes] = {}
    for path in paths:
        for fn in json.loads(path.read_text(encoding="utf-8")).get("functions", []):
            begin = int(fn["rva_begin"], 16)
            data = bytes.fromhex(fn["bytes"])
            if len(data) > len(code.get(begin, b"")):
                code[begin] = data
    return code


def make(code: dict[int, bytes]) -> dict:
    md, capstone = _decoder()
    sigs: dict[str, dict] = {}
    for name, rva in FUNCTIONS.items():
        if rva not in code:
            sys.exit(f"no dump of {name} ({rva:#x})")
        sigs[name] = {"kind": "function", "pattern": " ".join(signature(code[rva], 0, md, capstone)),
                      "made_from": hex(rva)}
    for name, (target, reader) in GLOBALS.items():
        if reader not in code:
            sys.exit(f"no dump of the function reading {name} ({reader:#x})")
        data = code[reader]
        insns = list(md.disasm(data, reader))
        for n, insn in enumerate(insns):
            from capstone import x86

            mem = [op for op in insn.operands if op.type == x86.X86_OP_MEM and op.mem.base == x86.X86_REG_RIP]
            if mem and insn.address + insn.size + mem[0].mem.disp == target:
                # from a few instructions before it, so the pattern is distinctive, through some after
                first = insns[max(0, n - 4)]
                start = first.address - reader
                pattern = signature(data, start, md, capstone, min_bytes=(insn.address - first.address) + insn.size + 12,
                                    max_bytes=MAX_BYTES)
                sigs[name] = {"kind": "global", "pattern": " ".join(pattern),
                              "disp_at": insn.address - first.address + insn.disp_offset,
                              "insn_end": insn.address - first.address + insn.size, "made_from": hex(first.address)}
                break
        else:
            sys.exit(f"{reader:#x} doesn't read {name} ({target:#x})")
    return sigs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("dumps", nargs="+", type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(make(load_code(args.dumps)), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
