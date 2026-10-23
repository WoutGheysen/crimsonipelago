// Crimsonipelago game plugin (R0): loads inside Crimson Desert through an ASI loader, writes a log next
// to itself and lets the Crimsonipelago client connect over localhost.
//
// R0 does not touch the game at all: no hooks, no memory writes. It proves that our own code runs in the
// game process and can talk to the client, and it records facts about the running game (executable,
// version, module base and size) for the next research steps.
//
// Protocol: newline-delimited JSON over TCP on 127.0.0.1:38301 (localhost only).
//   plugin -> client on connect: {"type":"hello", "plugin":"crimsonipelago", "protocol":1, ...game facts}
//   client -> plugin: {"cmd":"ping","id":N}   -> {"type":"pong","id":N,"uptime_ms":...}
//   client -> plugin: {"cmd":"info"}          -> {"type":"info", ...game facts}
//   anything else                             -> {"type":"error","message":"unknown command"}

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <psapi.h>

#include <atomic>
#include <cstdio>
#include <cstdarg>
#include <cstdint>
#include <string>

#ifndef CRIMSONIPELAGO_PLUGIN_VERSION
#define CRIMSONIPELAGO_PLUGIN_VERSION "0.1.0-r0"
#endif

namespace {

constexpr unsigned short kPort = 38301;
constexpr int kProtocol = 1;

HMODULE g_self = nullptr;
FILE* g_log = nullptr;
CRITICAL_SECTION g_log_lock;
ULONGLONG g_start_ms = 0;
std::atomic<bool> g_stop{false};

std::wstring dir_of(const std::wstring& path) {
    const size_t cut = path.find_last_of(L"\\/");
    return cut == std::wstring::npos ? std::wstring() : path.substr(0, cut);
}

std::wstring module_path(HMODULE module) {
    wchar_t buf[MAX_PATH * 2];
    const DWORD n = GetModuleFileNameW(module, buf, static_cast<DWORD>(sizeof(buf) / sizeof(buf[0])));
    return std::wstring(buf, n);
}

std::string utf8(const std::wstring& w) {
    if (w.empty()) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    std::string s(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.c_str(), static_cast<int>(w.size()), s.data(), n, nullptr, nullptr);
    return s;
}

void log(const char* fmt, ...) {
    if (!g_log) return;
    EnterCriticalSection(&g_log_lock);
    SYSTEMTIME t;
    GetLocalTime(&t);
    std::fprintf(g_log, "%02d:%02d:%02d.%03d  ", t.wHour, t.wMinute, t.wSecond, t.wMilliseconds);
    va_list args;
    va_start(args, fmt);
    std::vfprintf(g_log, fmt, args);
    va_end(args);
    std::fputc('\n', g_log);
    std::fflush(g_log);
    LeaveCriticalSection(&g_log_lock);
}

std::string json_escape(const std::string& s) {
    std::string out;
    out.reserve(s.size() + 8);
    for (const char c : s) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(c) < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += c;
                }
        }
    }
    return out;
}

struct GameFacts {
    std::string exe_path;
    std::string exe_version;     // file version from the executable's version resource
    uint64_t module_base = 0;
    uint64_t module_size = 0;
    uint32_t pe_timestamp = 0;   // link time from the PE header: changes with every game build
    DWORD pid = 0;
};

std::string file_version(const std::wstring& path) {
    DWORD ignored = 0;
    const DWORD size = GetFileVersionInfoSizeW(path.c_str(), &ignored);
    if (size == 0) return "unknown";
    std::string data(size, '\0');
    if (!GetFileVersionInfoW(path.c_str(), 0, size, data.data())) return "unknown";
    VS_FIXEDFILEINFO* info = nullptr;
    UINT len = 0;
    if (!VerQueryValueW(data.data(), L"\\", reinterpret_cast<void**>(&info), &len) || !info) return "unknown";
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%u.%u.%u.%u", HIWORD(info->dwFileVersionMS), LOWORD(info->dwFileVersionMS),
                  HIWORD(info->dwFileVersionLS), LOWORD(info->dwFileVersionLS));
    return buf;
}

GameFacts collect_facts() {
    GameFacts f;
    HMODULE exe = GetModuleHandleW(nullptr);
    const std::wstring path = module_path(exe);
    f.exe_path = utf8(path);
    f.exe_version = file_version(path);
    f.pid = GetCurrentProcessId();
    MODULEINFO mi{};
    if (GetModuleInformation(GetCurrentProcess(), exe, &mi, sizeof(mi))) {
        f.module_base = reinterpret_cast<uint64_t>(mi.lpBaseOfDll);
        f.module_size = mi.SizeOfImage;
    }
    // The main module's PE headers are always mapped: the link timestamp changes with every game build.
    const auto* dos = reinterpret_cast<const IMAGE_DOS_HEADER*>(exe);
    if (dos->e_magic == IMAGE_DOS_SIGNATURE) {
        const auto* nt = reinterpret_cast<const IMAGE_NT_HEADERS*>(reinterpret_cast<const uint8_t*>(exe) + dos->e_lfanew);
        if (nt->Signature == IMAGE_NT_SIGNATURE) f.pe_timestamp = nt->FileHeader.TimeDateStamp;
    }
    return f;
}

std::string facts_json(const char* type, const GameFacts& f) {
    char buf[1024];
    std::snprintf(buf, sizeof(buf),
                  "{\"type\":\"%s\",\"plugin\":\"crimsonipelago\",\"plugin_version\":\"%s\",\"protocol\":%d,"
                  "\"pid\":%lu,\"exe\":\"%s\",\"exe_version\":\"%s\",\"module_base\":\"0x%llx\","
                  "\"module_size\":%llu,\"pe_timestamp\":%u,\"uptime_ms\":%llu}",
                  type, CRIMSONIPELAGO_PLUGIN_VERSION, kProtocol, static_cast<unsigned long>(f.pid),
                  json_escape(f.exe_path).c_str(), json_escape(f.exe_version).c_str(),
                  static_cast<unsigned long long>(f.module_base), static_cast<unsigned long long>(f.module_size),
                  f.pe_timestamp, static_cast<unsigned long long>(GetTickCount64() - g_start_ms));
    return buf;
}

bool send_line(SOCKET s, const std::string& line) {
    std::string data = line + "\n";
    const char* p = data.data();
    int left = static_cast<int>(data.size());
    while (left > 0) {
        const int n = send(s, p, left, 0);
        if (n <= 0) return false;
        p += n;
        left -= n;
    }
    return true;
}

// Tiny field reader for the client's commands: finds "key":<number> or "key":"text" in a flat JSON object.
std::string json_field(const std::string& obj, const char* key) {
    const std::string needle = std::string("\"") + key + "\"";
    size_t at = obj.find(needle);
    if (at == std::string::npos) return {};
    at = obj.find(':', at + needle.size());
    if (at == std::string::npos) return {};
    ++at;
    while (at < obj.size() && (obj[at] == ' ' || obj[at] == '\t')) ++at;
    if (at < obj.size() && obj[at] == '"') {
        const size_t end = obj.find('"', at + 1);
        return end == std::string::npos ? std::string() : obj.substr(at + 1, end - at - 1);
    }
    size_t end = at;
    while (end < obj.size() && obj[end] != ',' && obj[end] != '}' && obj[end] != ' ') ++end;
    return obj.substr(at, end - at);
}

void serve_client(SOCKET client, const GameFacts& facts) {
    log("client connected");
    if (!send_line(client, facts_json("hello", facts))) {
        log("client: hello failed");
        return;
    }
    std::string pending;
    char buf[1024];
    while (!g_stop.load()) {
        const int n = recv(client, buf, sizeof(buf), 0);
        if (n <= 0) break;
        pending.append(buf, static_cast<size_t>(n));
        if (pending.size() > 64 * 1024) {
            log("client sent an oversized line; disconnecting");
            break;
        }
        size_t nl;
        while ((nl = pending.find('\n')) != std::string::npos) {
            const std::string line = pending.substr(0, nl);
            pending.erase(0, nl + 1);
            const std::string cmd = json_field(line, "cmd");
            std::string reply;
            if (cmd == "ping") {
                std::string id = json_field(line, "id");
                if (id.empty()) id = "0";
                char out[160];
                std::snprintf(out, sizeof(out), "{\"type\":\"pong\",\"id\":%s,\"uptime_ms\":%llu}", id.c_str(),
                              static_cast<unsigned long long>(GetTickCount64() - g_start_ms));
                reply = out;
            } else if (cmd == "info") {
                reply = facts_json("info", facts);
            } else {
                reply = "{\"type\":\"error\",\"message\":\"unknown command\"}";
                log("unknown command: %s", line.substr(0, 200).c_str());
            }
            if (!send_line(client, reply)) break;
        }
    }
    log("client disconnected");
}

DWORD WINAPI worker(LPVOID) {
    g_start_ms = GetTickCount64();
    InitializeCriticalSection(&g_log_lock);
    const std::wstring log_path = dir_of(module_path(g_self)) + L"\\crimsonipelago_plugin.log";
    g_log = _wfopen(log_path.c_str(), L"w");

    const GameFacts facts = collect_facts();
    log("Crimsonipelago plugin %s loaded", CRIMSONIPELAGO_PLUGIN_VERSION);
    log("game: %s (file version %s, build stamp %u)", facts.exe_path.c_str(), facts.exe_version.c_str(), facts.pe_timestamp);
    log("module base 0x%llx, size %llu bytes, pid %lu", static_cast<unsigned long long>(facts.module_base),
        static_cast<unsigned long long>(facts.module_size), static_cast<unsigned long>(facts.pid));
    log("R0: no hooks, no memory writes; waiting for the client on 127.0.0.1:%u", kPort);

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        log("WSAStartup failed: %d", WSAGetLastError());
        return 0;
    }
    SOCKET server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (server == INVALID_SOCKET) {
        log("socket failed: %d", WSAGetLastError());
        return 0;
    }
    BOOL exclusive = TRUE;
    setsockopt(server, SOL_SOCKET, SO_EXCLUSIVEADDRUSE, reinterpret_cast<const char*>(&exclusive), sizeof(exclusive));
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(kPort);
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);   // localhost only: nothing on the network can reach it
    if (bind(server, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == SOCKET_ERROR ||
        listen(server, 1) == SOCKET_ERROR) {
        log("cannot listen on 127.0.0.1:%u (error %d); is another copy of the game or plugin running?", kPort,
            WSAGetLastError());
        closesocket(server);
        return 0;
    }
    log("listening on 127.0.0.1:%u", kPort);
    while (!g_stop.load()) {
        SOCKET client = accept(server, nullptr, nullptr);
        if (client == INVALID_SOCKET) {
            if (g_stop.load()) break;
            log("accept failed: %d", WSAGetLastError());
            Sleep(1000);
            continue;
        }
        serve_client(client, facts);   // one client at a time: the Crimsonipelago client
        closesocket(client);
    }
    closesocket(server);
    return 0;
}

bool in_crimson_desert() {
    std::wstring exe = module_path(nullptr);
    for (auto& c : exe) c = static_cast<wchar_t>(towlower(c));
    return exe.find(L"crimsondesert") != std::wstring::npos;
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_self = module;
        DisableThreadLibraryCalls(module);
        if (!in_crimson_desert()) return TRUE;   // loaded into something else by a shared ASI loader: stay idle
        // Never do real work under the loader lock: start our own thread and return.
        HANDLE t = CreateThread(nullptr, 0, worker, nullptr, 0, nullptr);
        if (t) CloseHandle(t);
    } else if (reason == DLL_PROCESS_DETACH) {
        g_stop.store(true);
    }
    return TRUE;
}
