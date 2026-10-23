// Crimsonipelago game plugin: loads inside Crimson Desert through an ASI loader, writes a log next to
// itself and lets the Crimsonipelago client connect over localhost.
//
// R0: proves our code runs in the game and talks to the client; records the game build.
// R1: memory search for research (memory.hpp). The plugin never writes on its own: a write happens only
//     when the client asks for one and the current value is exactly the one it expects.
//
// Protocol: newline-delimited JSON over TCP on 127.0.0.1:38301 (localhost only).
//   plugin -> client on connect: {"type":"hello", "plugin":"crimsonipelago", "protocol":1, ...game facts}
//   client -> plugin: {"cmd":"ping","id":N}   -> {"type":"pong","id":N,"uptime_ms":...}
//   client -> plugin: {"cmd":"info"}          -> {"type":"info", ...game facts}
//   client -> plugin: {"cmd":"scan","lo":N,"hi":M,"width":4|8,"align":1|2|4|8}
//                                                 -> {"type":"scan","count":..,"hits":[first 64]}
//   client -> plugin: {"cmd":"rescan","lo":N,"hi":M}         -> same, among the previous hits
//   client -> plugin: {"cmd":"read","addr":"0x..","size":N}   -> {"type":"bytes","hex":".."} (N <= 4096)
//   client -> plugin: {"cmd":"write","addr":"0x..","expect":N,"value":M,"width":4|8} -> {"type":"written",...}
//   ("value":N instead of lo/hi searches one value; scan_u64, rescan_u64, write_u64 are the 8-byte forms)
//   anything else                             -> {"type":"error","message":"unknown command"}

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <psapi.h>

#include <atomic>
#include <cstdlib>
#include <cwctype>
#include <cstdio>
#include <cstdarg>
#include <cstdint>
#include <string>
#include <vector>

#include "memory.hpp"

#ifndef CRIMSONIPELAGO_PLUGIN_VERSION
#define CRIMSONIPELAGO_PLUGIN_VERSION "0.3.0-r1b"
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

uint64_t parse_u64(const std::string& s, bool& ok) {
    ok = false;
    if (s.empty()) return 0;
    char* end = nullptr;
    const unsigned long long v = std::strtoull(s.c_str(), &end, 0);   // "0x.." or decimal
    ok = end && *end == '\0';
    return v;
}

std::string hits_json(const char* type, const std::vector<uint64_t>& hits, bool truncated, uint64_t bytes, uint32_t regions,
                      ULONGLONG ms) {
    std::string out = std::string("{\"type\":\"") + type + "\",\"count\":" + std::to_string(hits.size()) +
                      ",\"truncated\":" + (truncated ? "true" : "false") + ",\"bytes_scanned\":" + std::to_string(bytes) +
                      ",\"regions\":" + std::to_string(regions) + ",\"ms\":" + std::to_string(ms) + ",\"hits\":[";
    for (size_t i = 0; i < hits.size() && i < 64; ++i) {
        char a[24];
        std::snprintf(a, sizeof(a), "%s\"0x%llx\"", i ? "," : "", static_cast<unsigned long long>(hits[i]));
        out += a;
    }
    return out + "]}";
}

std::string error_json(const char* message) {
    return std::string("{\"type\":\"error\",\"message\":\"") + json_escape(message) + "\"}";
}

struct Search {
    std::vector<uint64_t> hits;
    mem::Query query;   // the last search's width: a rescan reads the same size
};

// Reads the optional "width"/"align" fields; defaults are the R1 u64 search.
bool read_query(const std::string& line, mem::Query& q, bool with_range, std::string& problem) {
    bool ok = false;
    if (with_range) {
        const std::string value = json_field(line, "value");
        if (!value.empty()) {
            q.lo = q.hi = parse_u64(value, ok);
        } else {
            q.lo = parse_u64(json_field(line, "lo"), ok);
            if (ok) q.hi = parse_u64(json_field(line, "hi"), ok);
        }
        if (!ok) {
            problem = "value (or lo and hi) must be integers";
            return false;
        }
    }
    const std::string width = json_field(line, "width"), align = json_field(line, "align");
    if (!width.empty()) q.width = static_cast<unsigned>(parse_u64(width, ok));
    if (!width.empty() && !ok) q.width = 0;
    if (!align.empty()) q.align = static_cast<unsigned>(parse_u64(align, ok));
    if (!align.empty() && !ok) q.align = 0;
    if (!q.valid()) {
        problem = "width must be 4 or 8, align 1, 2, 4 or 8, lo <= hi, and 4-byte values below 2^32";
        return false;
    }
    return true;
}

// Research commands (R1). The hit list lives for the connection: one search at a time.
//   scan   {lo, hi | value, width=8, align=4}  -> new search
//   rescan {lo, hi | value}                     -> among the previous hits, same width
//   write  {addr, expect, value, width=8}       -> compare-and-set
// scan_u64 / rescan_u64 / write_u64 are the same with width 8 (R1's first version).
std::string handle_memory(const std::string& cmd, const std::string& line, Search& search) {
    bool ok = false;
    std::string problem;
    if (cmd == "scan" || cmd == "scan_u64") {
        mem::Query q;
        if (!read_query(line, q, true, problem)) return error_json(problem.c_str());
        if (cmd == "scan_u64") q.width = 8, q.align = 4;
        const ULONGLONG t0 = GetTickCount64();
        mem::ScanResult r = mem::scan(q);
        search.hits.swap(r.hits);
        search.query = q;
        log("scan %llu..%llu (%u bytes, align %u): %zu hits in %u regions, %llu MB, %llu ms%s",
            static_cast<unsigned long long>(q.lo), static_cast<unsigned long long>(q.hi), q.width, q.align,
            search.hits.size(), r.regions, static_cast<unsigned long long>(r.bytes_scanned >> 20),
            static_cast<unsigned long long>(GetTickCount64() - t0), r.truncated ? " (truncated)" : "");
        return hits_json("scan", search.hits, r.truncated, r.bytes_scanned, r.regions, GetTickCount64() - t0);
    }
    if (cmd == "rescan" || cmd == "rescan_u64") {
        mem::Query q = search.query;
        if (!read_query(line, q, true, problem)) return error_json(problem.c_str());
        q.width = search.query.width;   // same size as the search it narrows
        const ULONGLONG t0 = GetTickCount64();
        search.hits = mem::rescan(search.hits, q);
        search.query = q;
        log("rescan %llu..%llu: %zu hits", static_cast<unsigned long long>(q.lo), static_cast<unsigned long long>(q.hi),
            search.hits.size());
        return hits_json("scan", search.hits, false, 0, 0, GetTickCount64() - t0);
    }
    if (cmd == "read") {
        const uint64_t addr = parse_u64(json_field(line, "addr"), ok);
        if (!ok) return error_json("addr must be an integer");
        const uint64_t size = parse_u64(json_field(line, "size"), ok);
        if (!ok || size == 0 || size > 4096) return error_json("size must be 1..4096");
        std::vector<uint8_t> data(static_cast<size_t>(size));
        if (!mem::read(addr, data.data(), data.size())) return error_json("address not readable");
        return std::string("{\"type\":\"bytes\",\"hex\":\"") + mem::hex(data.data(), data.size()) + "\"}";
    }
    if (cmd == "write" || cmd == "write_u64") {
        const uint64_t addr = parse_u64(json_field(line, "addr"), ok);
        if (!ok) return error_json("addr must be an integer");
        const uint64_t expect = parse_u64(json_field(line, "expect"), ok);
        if (!ok) return error_json("expect must be an integer");
        const uint64_t value = parse_u64(json_field(line, "value"), ok);
        if (!ok) return error_json("value must be an integer");
        mem::Query q;
        if (cmd == "write" && !read_query(line, q, false, problem)) return error_json(problem.c_str());
        if (q.width == 4 && (expect > 0xFFFFFFFFull || value > 0xFFFFFFFFull))
            return error_json("4-byte writes take values below 2^32");
        uint64_t found = 0;
        const bool done = mem::write_if(addr, q.width, expect, value, found);
        log("write 0x%llx (%u bytes): expect %llu, found %llu, value %llu -> %s", static_cast<unsigned long long>(addr),
            q.width, static_cast<unsigned long long>(expect), static_cast<unsigned long long>(found),
            static_cast<unsigned long long>(value), done ? "written" : "not written");
        char out[200];
        std::snprintf(out, sizeof(out), "{\"type\":\"written\",\"ok\":%s,\"found\":%llu}", done ? "true" : "false",
                      static_cast<unsigned long long>(found));
        return out;
    }
    return {};
}

void serve_client(SOCKET client, const GameFacts& facts) {
    log("client connected");
    if (!send_line(client, facts_json("hello", facts))) {
        log("client: hello failed");
        return;
    }
    std::string pending;
    Search search;
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
            } else if (!(reply = handle_memory(cmd, line, search)).empty()) {
                // handled
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
    log("no hooks; memory is only written when the client asks; waiting for the client on 127.0.0.1:%u", kPort);

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
