// Test host for CI: an executable named CrimsonDesert.exe that loads the plugin the way an ASI loader
// does, then idles so the client can connect. It keeps a known value in memory for the search test.
// Never shipped.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>

volatile uint64_t g_marker = 0x00C0FFEE12345678ull;   // the CI test finds this and changes it

int wmain(int argc, wchar_t** argv) {
    const wchar_t* plugin = argc > 1 ? argv[1] : L"crimsonipelago.asi";
    const DWORD seconds = argc > 2 ? static_cast<DWORD>(_wtoi(argv[2])) : 60;
    HMODULE m = LoadLibraryW(plugin);
    if (!m) {
        std::fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError());
        return 1;
    }
    std::printf("plugin loaded; idling %lu s\n", seconds);
    std::fflush(stdout);
    for (DWORD i = 0; i < seconds * 10; ++i) {
        Sleep(100);
        if (g_marker != 0x00C0FFEE12345678ull) {
            std::printf("marker changed to 0x%llx\n", static_cast<unsigned long long>(g_marker));
            std::fflush(stdout);
            break;
        }
    }
    Sleep(2000);
    return 0;
}
