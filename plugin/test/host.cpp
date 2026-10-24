// Test host for CI: an executable named CrimsonDesert.exe that loads the plugin the way an ASI loader
// does, then idles so the client can connect. It keeps a known value in memory for the search test.
// Never shipped.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

volatile uint64_t g_marker = 0x00C0FFEE12345678ull;   // the CI test finds this and changes it
volatile uint64_t g_watched = 0x00BADC0DE5EED000ull;  // rewritten (same value) every 100 ms: the watch test

// A real function of its own, so the watch test can check the writer and its caller.
__declspec(noinline) void poke_watched() { g_watched = 0x00BADC0DE5EED000ull; }

// The execute-watch test: a function with five arguments, the first pointing at a known value. Its address
// sits right after a marker the test can search for.
const uint64_t g_tag[2] = {0x007A67007A67007Aull, 0x1122334455667788ull};
struct Bag { uint64_t magic; uint64_t count; const uint64_t* tag; };
Bag g_bag = {0x00BA6BA6BA6BA600ull, 0, g_tag};
__declspec(noinline) uint64_t add_to_bag(Bag* bag, uint64_t delta, uint64_t a3, uint64_t a4, uint64_t a5) {
    bag->count += delta;
    return bag->count + a3 + a4 + a5;
}
using AddFn = uint64_t (*)(Bag*, uint64_t, uint64_t, uint64_t, uint64_t);
volatile AddFn g_add = add_to_bag;   // called through a pointer, so it can't be inlined
volatile uint64_t g_fn_marker[2] = {0x00F00DF00DF00D00ull, 0};

__declspec(noinline) void tick() {
    poke_watched();
    g_add(&g_bag, 3, 0x33, 0x44, 0x55);
}

int wmain(int argc, wchar_t** argv) {
    const wchar_t* plugin = argc > 1 ? argv[1] : L"crimsonipelago.asi";
    const DWORD seconds = argc > 2 ? static_cast<DWORD>(_wtoi(argv[2])) : 60;
    g_fn_marker[1] = reinterpret_cast<uint64_t>(add_to_bag);
    HMODULE m = LoadLibraryW(plugin);
    if (!m) {
        std::fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError());
        return 1;
    }
    std::printf("plugin loaded; idling %lu s\n", seconds);
    std::fflush(stdout);
    for (DWORD i = 0; i < seconds * 10; ++i) {
        Sleep(100);
        tick();
        if (g_marker != 0x00C0FFEE12345678ull) {
            std::printf("marker changed to 0x%llx\n", static_cast<unsigned long long>(g_marker));
            std::fflush(stdout);
            break;
        }
    }
    Sleep(2000);
    return 0;
}
