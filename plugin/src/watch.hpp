// Watches for research (R2): which game code changes a value, and what called it; and (R2c) execute watches
// on a function's first instruction, recording the arguments it was called with and the memory behind them.
//
// Uses the processor's debug registers (up to 4 addresses): every thread of the game gets the same
// watches, and a vectored exception handler records each hit (the instruction that wrote, the call
// chain, return addresses found on the stack) into a ring buffer, then lets the game carry on. Nothing in
// the game's memory or code is changed; the watches are removed when the client disconnects.
//
// The handler runs inside the game's threads, so it only copies numbers: no allocation, no locks, no I/O.
#pragma once

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <tlhelp32.h>

#include <atomic>
#include <cstdint>
#include <cstring>

namespace watch {

constexpr unsigned kSlots = 4;
constexpr unsigned kFrames = 16;
constexpr unsigned kStack = 16;
constexpr size_t kRing = 1024;
constexpr uint64_t kStackScanBytes = 4096;
constexpr unsigned kSites = 64;       // distinct writing instructions tracked
constexpr uint64_t kPerSite = 8;      // full records per instruction between two collections: a write every
                                      // frame can't crowd out rare ones
constexpr unsigned kArgs = 16;        // execute watches: rcx, rdx, r8, r9 and 12 stack arguments
constexpr unsigned kSnapRing = 32;    // execute watches: memory behind the arguments, for the last 32 calls
constexpr unsigned kSnapBytes = 512;

enum Kind : uint8_t { kWrite = 0, kAccess = 1, kExecute = 2 };

struct Hit {
    std::atomic<uint64_t> seq{0};   // index + 1 once complete: lets the reader skip a record being rewritten
    uint64_t rip = 0, rsp = 0, value = 0;
    uint32_t tid = 0;
    uint8_t slot = 0, nframes = 0, nstack = 0;
    uint64_t frames[kFrames] = {};
    uint64_t stack[kStack] = {};
    uint8_t nargs = 0;
    uint64_t args[kArgs] = {};
    int64_t snap = -1;              // index into g_snaps, or -1
};

// The memory each pointer argument pointed at when the function was entered.
struct Snap {
    std::atomic<uint64_t> seq{0};
    uint16_t len[kArgs] = {};
    uint8_t data[kArgs][kSnapBytes] = {};
};

struct Watch {
    uint64_t addr = 0;
    unsigned size = 0;     // 1, 2, 4 or 8 (1 for execute)
    Kind kind = kWrite;
};

inline Hit g_ring[kRing];
inline std::atomic<uint64_t> g_total{0};
inline std::atomic<uint64_t> g_delivered{0};
inline std::atomic<unsigned> g_mask{0};        // slots in use
inline std::atomic<unsigned> g_ever{0};        // slots ever used: stray hits after removal are swallowed
inline Watch g_watch[kSlots];
inline uint64_t g_image_lo = 0, g_image_hi = 0; // the game's executable: stack values inside it are return addresses
inline PVOID g_handler = nullptr;
inline Snap g_snaps[kSnapRing];
inline std::atomic<uint64_t> g_snap_total{0};

struct Site {
    std::atomic<uint64_t> rip{0};
    std::atomic<uint64_t> count{0};      // every hit
    std::atomic<uint64_t> recorded{0};   // full records since the last collection
};
inline Site g_sites[kSites];

inline void reset_quota() {
    for (Site& site : g_sites) site.recorded.store(0);
}

// Counts a hit for its instruction; false when that instruction already has kPerSite records.
inline bool count_site(uint64_t rip) {
    for (unsigned i = 0; i < kSites; ++i) {
        uint64_t cur = g_sites[i].rip.load(std::memory_order_acquire);
        if (cur == 0) {
            uint64_t expected = 0;
            if (g_sites[i].rip.compare_exchange_strong(expected, rip)) cur = rip;
            else cur = expected;
        }
        if (cur == rip) {
            g_sites[i].count.fetch_add(1, std::memory_order_relaxed);
            return g_sites[i].recorded.fetch_add(1, std::memory_order_relaxed) < kPerSite;
        }
    }
    return true;   // table full: record anyway
}

inline uint64_t dr7_for(unsigned mask) {
    uint64_t dr7 = 0;
    for (unsigned i = 0; i < kSlots; ++i) {
        if (!(mask & (1u << i))) continue;
        const Watch& w = g_watch[i];
        const uint64_t rw = w.kind == kExecute ? 0 : w.kind == kAccess ? 3 : 1;
        const uint64_t len = w.kind == kExecute || w.size == 1 ? 0 : w.size == 2 ? 1 : w.size == 8 ? 2 : 3;
        dr7 |= 1ull << (i * 2);                    // local enable
        dr7 |= rw << (16 + i * 4);
        dr7 |= len << (18 + i * 4);
    }
    return dr7;
}

inline bool stack_read(uint64_t addr, uint64_t lo, uint64_t hi, uint64_t& out) {
    if (addr < lo || addr + 8 > hi || (addr & 7)) return false;
    std::memcpy(&out, reinterpret_cast<const void*>(addr), 8);
    return true;
}

inline LONG CALLBACK on_exception(EXCEPTION_POINTERS* ep) {
    if (ep->ExceptionRecord->ExceptionCode != EXCEPTION_SINGLE_STEP) return EXCEPTION_CONTINUE_SEARCH;
    CONTEXT* c = ep->ContextRecord;
    const unsigned fired = static_cast<unsigned>(c->Dr6 & 0xF);
    const unsigned ours = fired & g_ever.load(std::memory_order_relaxed);
    if (!ours) return EXCEPTION_CONTINUE_SEARCH;
    c->Dr6 = 0;
    const unsigned active = g_mask.load(std::memory_order_relaxed);
    if (!(ours & active)) return EXCEPTION_CONTINUE_EXECUTION;   // removed meanwhile: swallow

    unsigned slot = 0;
    while (!((ours & active) & (1u << slot))) ++slot;
    const bool execute = g_watch[slot].kind == kExecute;
    // An execute watch fires before the instruction runs: the resume flag lets it run once without firing again.
    if (execute) c->EFlags |= 0x10000;
    if (!count_site(c->Rip)) return EXCEPTION_CONTINUE_EXECUTION;
    const uint64_t index = g_total.fetch_add(1, std::memory_order_relaxed);
    Hit& h = g_ring[index % kRing];
    h.seq.store(0, std::memory_order_relaxed);
    h.rip = c->Rip;
    h.rsp = c->Rsp;
    h.tid = GetCurrentThreadId();
    h.slot = static_cast<uint8_t>(slot);
    h.value = 0;
    if (!execute) std::memcpy(&h.value, reinterpret_cast<const void*>(g_watch[slot].addr), g_watch[slot].size);

    // Stack bounds of this thread (the handler runs on the thread that wrote).
    const auto* tib = reinterpret_cast<const NT_TIB*>(NtCurrentTeb());
    const uint64_t stack_lo = c->Rsp, stack_hi = reinterpret_cast<uint64_t>(tib->StackBase);

    // Execute watches: the arguments as the function receives them (x64: rcx, rdx, r8, r9, then the stack
    // above the return address and the 32-byte shadow space), and the memory behind those that point somewhere.
    h.nargs = 0;
    h.snap = -1;
    if (execute) {
        h.args[0] = c->Rcx;
        h.args[1] = c->Rdx;
        h.args[2] = c->R8;
        h.args[3] = c->R9;
        h.nargs = 4;
        for (unsigned i = 0; i < kArgs - 4; ++i) {
            uint64_t v;
            if (!stack_read(c->Rsp + 0x28 + 8 * i, stack_lo, stack_hi, v)) break;
            h.args[h.nargs++] = v;
        }
        const uint64_t si = g_snap_total.fetch_add(1, std::memory_order_relaxed);
        Snap& snap = g_snaps[si % kSnapRing];
        snap.seq.store(0, std::memory_order_relaxed);
        for (unsigned i = 0; i < kArgs; ++i) {
            snap.len[i] = 0;
            const uint64_t p = i < h.nargs ? h.args[i] : 0;
            if (p < 0x10000 || p > 0x7FFFFFFFFFFFull) continue;
            SIZE_T got = 0;
            for (SIZE_T want = kSnapBytes; want >= 64; want /= 2) {   // smaller when it runs into an unmapped page
                if (ReadProcessMemory(GetCurrentProcess(), reinterpret_cast<LPCVOID>(p), snap.data[i], want, &got) &&
                    got == want) {
                    snap.len[i] = static_cast<uint16_t>(want);
                    break;
                }
            }
        }
        snap.seq.store(si + 1, std::memory_order_release);
        h.snap = static_cast<int64_t>(si);
    }

    // Call chain from the unwind data. Functions without it are treated as leaves.
    CONTEXT u = *c;
    h.nframes = 0;
    for (unsigned n = 0; n < kFrames; ++n) {
        DWORD64 image = 0;
        PRUNTIME_FUNCTION fn = RtlLookupFunctionEntry(u.Rip, &image, nullptr);
        if (fn) {
            PVOID data = nullptr;
            DWORD64 frame = 0;
            RtlVirtualUnwind(UNW_FLAG_NHANDLER, image, u.Rip, fn, &u, &data, &frame, nullptr);
        } else {
            uint64_t ret = 0;
            if (!stack_read(u.Rsp, stack_lo, stack_hi, ret)) break;
            u.Rip = ret;
            u.Rsp += 8;
        }
        if (u.Rip == 0 || u.Rsp < stack_lo || u.Rsp >= stack_hi) break;
        h.frames[h.nframes++] = u.Rip;
    }

    // Values on the stack that point into the game's executable: return addresses, even where the
    // unwind data is missing or wrong.
    h.nstack = 0;
    const uint64_t scan_hi = stack_hi - stack_lo > kStackScanBytes ? stack_lo + kStackScanBytes : stack_hi;
    for (uint64_t p = (stack_lo + 7) & ~7ull; p + 8 <= scan_hi && h.nstack < kStack; p += 8) {
        uint64_t v;
        std::memcpy(&v, reinterpret_cast<const void*>(p), 8);
        if (v >= g_image_lo && v < g_image_hi) h.stack[h.nstack++] = v;
    }
    h.seq.store(index + 1, std::memory_order_release);
    return EXCEPTION_CONTINUE_EXECUTION;
}

// Sets the debug registers of every thread of the process except the caller's. Returns the number of
// threads updated.
inline unsigned apply(unsigned mask) {
    const uint64_t dr7 = dr7_for(mask);
    const DWORD self = GetCurrentThreadId(), pid = GetCurrentProcessId();
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    if (snap == INVALID_HANDLE_VALUE) return 0;
    unsigned done = 0;
    THREADENTRY32 te;
    te.dwSize = sizeof(te);
    for (BOOL more = Thread32First(snap, &te); more; more = Thread32Next(snap, &te)) {
        if (te.th32OwnerProcessID != pid || te.th32ThreadID == self) continue;
        HANDLE t = OpenThread(THREAD_GET_CONTEXT | THREAD_SET_CONTEXT | THREAD_SUSPEND_RESUME, FALSE, te.th32ThreadID);
        if (!t) continue;
        if (SuspendThread(t) != static_cast<DWORD>(-1)) {
            alignas(16) CONTEXT ctx;
            std::memset(&ctx, 0, sizeof(ctx));
            ctx.ContextFlags = CONTEXT_DEBUG_REGISTERS;
            if (GetThreadContext(t, &ctx)) {
                ctx.Dr0 = (mask & 1) ? g_watch[0].addr : 0;
                ctx.Dr1 = (mask & 2) ? g_watch[1].addr : 0;
                ctx.Dr2 = (mask & 4) ? g_watch[2].addr : 0;
                ctx.Dr3 = (mask & 8) ? g_watch[3].addr : 0;
                ctx.Dr6 = 0;
                ctx.Dr7 = dr7;
                if (SetThreadContext(t, &ctx)) ++done;
            }
            ResumeThread(t);
        }
        CloseHandle(t);
    }
    CloseHandle(snap);
    return done;
}

inline void init(uint64_t image_base, uint64_t image_size) {
    g_image_lo = image_base;
    g_image_hi = image_base + image_size;
    if (!g_handler) g_handler = AddVectoredExceptionHandler(1, on_exception);
}

// Adds a watch; returns the slot, or -1 when all four are taken or the address doesn't suit.
inline int add(uint64_t addr, unsigned size, Kind kind) {
    if (kind == kExecute) size = 1;
    if (!(size == 1 || size == 2 || size == 4 || size == 8) || addr % size) return -1;
    unsigned mask = g_mask.load();
    if (mask == 0) {
        for (Site& site : g_sites) {
            site.count.store(0);
            site.recorded.store(0);
            site.rip.store(0);
        }
    }
    for (unsigned i = 0; i < kSlots; ++i) {
        if (mask & (1u << i)) continue;
        g_watch[i] = Watch{addr, size, kind};
        g_ever.fetch_or(1u << i);
        g_mask.store(mask | (1u << i));
        return static_cast<int>(i);
    }
    return -1;
}

inline unsigned clear() {
    g_mask.store(0);
    return apply(0);
}

}  // namespace watch
