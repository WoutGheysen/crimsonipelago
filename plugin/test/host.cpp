// Test host for CI: an executable named CrimsonDesert.exe that loads the plugin the way an ASI loader
// does, then idles so the client can connect. Never shipped.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <cstdio>
#include <cstdlib>

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
    Sleep(seconds * 1000);
    return 0;
}
