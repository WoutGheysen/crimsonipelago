// Memory search for research (R1): find where the game keeps a value by searching for it, letting it
// change in the game, and searching again among the previous hits, the way Cheat Engine does.
//
// Every read and write goes through ReadProcessMemory / WriteProcessMemory on our own process: a page that
// disappears between the region walk and the read makes the call fail instead of crashing the game.
// Writes are compare-and-set: they only happen when the current value is the one the client expects.
#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <cstdint>
#include <cstring>
#include <string>
#include <vector>

namespace mem {

constexpr size_t kMaxHits = 4'000'000;   // stop collecting beyond this; the client asks for a narrower value

inline bool readable_rw(const MEMORY_BASIC_INFORMATION& mbi) {
    if (mbi.State != MEM_COMMIT) return false;
    if (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) return false;
    const DWORD p = mbi.Protect & 0xFF;
    // game state lives in writable memory: heaps and the module's data sections
    return p == PAGE_READWRITE || p == PAGE_WRITECOPY || p == PAGE_EXECUTE_READWRITE || p == PAGE_EXECUTE_WRITECOPY;
}

inline bool read(uint64_t addr, void* out, size_t size) {
    SIZE_T got = 0;
    return ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<LPCVOID>(addr), out, size, &got) && got == size;
}

inline bool read_u64(uint64_t addr, uint64_t& value) { return read(addr, &value, sizeof(value)); }

struct ScanResult {
    std::vector<uint64_t> hits;
    bool truncated = false;
    uint64_t bytes_scanned = 0;
    uint32_t regions = 0;
};

// All 4-byte aligned addresses in writable committed memory that hold `value` as a 64-bit integer.
inline ScanResult scan_u64(uint64_t value) {
    ScanResult r;
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    uint64_t addr = reinterpret_cast<uint64_t>(si.lpMinimumApplicationAddress);
    const uint64_t end = reinterpret_cast<uint64_t>(si.lpMaximumApplicationAddress);
    std::vector<uint8_t> buf;
    MEMORY_BASIC_INFORMATION mbi;
    while (addr < end && VirtualQuery(reinterpret_cast<LPCVOID>(addr), &mbi, sizeof(mbi)) == sizeof(mbi)) {
        const uint64_t base = reinterpret_cast<uint64_t>(mbi.BaseAddress);
        const uint64_t size = mbi.RegionSize;
        addr = base + size;
        if (!readable_rw(mbi)) continue;
        ++r.regions;
        constexpr uint64_t kChunk = 4ull << 20;
        for (uint64_t off = 0; off < size; off += kChunk) {
            const uint64_t n = (size - off) < kChunk ? (size - off) : kChunk;
            buf.resize(static_cast<size_t>(n));
            if (!read(base + off, buf.data(), buf.size())) continue;
            r.bytes_scanned += n;
            for (uint64_t i = 0; i + 8 <= n; i += 4) {
                uint64_t v;
                std::memcpy(&v, buf.data() + i, 8);
                if (v == value) {
                    if (r.hits.size() >= kMaxHits) {
                        r.truncated = true;
                        return r;
                    }
                    r.hits.push_back(base + off + i);
                }
            }
        }
    }
    return r;
}

// Keep the previous hits that now hold `value`.
inline std::vector<uint64_t> rescan_u64(const std::vector<uint64_t>& previous, uint64_t value) {
    std::vector<uint64_t> out;
    for (const uint64_t a : previous) {
        uint64_t v;
        if (read_u64(a, v) && v == value) out.push_back(a);
    }
    return out;
}

inline bool write_u64_if(uint64_t addr, uint64_t expect, uint64_t value, uint64_t& found) {
    if (!read_u64(addr, found) || found != expect) return false;
    SIZE_T wrote = 0;
    return WriteProcessMemory(GetCurrentProcess(), reinterpret_cast<LPVOID>(addr), &value, sizeof(value), &wrote) &&
           wrote == sizeof(value);
}

inline std::string hex(const uint8_t* data, size_t n) {
    static const char* digits = "0123456789abcdef";
    std::string s;
    s.reserve(n * 2);
    for (size_t i = 0; i < n; ++i) {
        s += digits[data[i] >> 4];
        s += digits[data[i] & 15];
    }
    return s;
}

}  // namespace mem
