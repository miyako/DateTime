#include <unicode/ucal.h>
#include <unicode/uchar.h>
#include <unicode/udata.h>
#include <unicode/uenum.h>
#include <unicode/ustring.h>
#include <unicode/uversion.h>
#include <unicode/utypes.h>
#include <cstdint>
#include <cstdio>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <unordered_set>
#include <vector>
#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#endif

extern "C" const char U_ICUDATA_ENTRY_POINT[];

namespace {
constexpr std::size_t max_items = 256, max_total_bytes = 1048576;
constexpr std::size_t max_instant_chars = 35, max_zone_bytes = 64;
constexpr std::size_t max_inspect_arg_bytes = 4096, max_inspect_bytes = 8192;
constexpr std::int64_t day_ms = 86400000;
struct Failure {
    const char* code;
    const char* message;
};
constexpr Failure arguments{"invalid_arguments", "invalid command arguments"};
constexpr Failure instant_error{"invalid_instant", "invalid RFC3339 instant"};
constexpr Failure zone_error{"invalid_zone", "unknown IANA time zone"};
constexpr Failure range_error{"out_of_range", "date outside years 0001-9999"};
constexpr Failure icu_error{"icu_error", "ICU conversion failed"};
std::unordered_set<std::string> system_zones;

bool valid_utf8(std::string_view s) {
    for (std::size_t i = 0; i < s.size();) {
        const auto first = static_cast<unsigned char>(s[i++]);
        if (first <= 0x7f) continue;
        unsigned remaining, codepoint, minimum;
        if (first >= 0xc2 && first <= 0xdf) {
            remaining = 1; codepoint = first & 0x1f; minimum = 0x80;
        } else if (first >= 0xe0 && first <= 0xef) {
            remaining = 2; codepoint = first & 0x0f; minimum = 0x800;
        } else if (first >= 0xf0 && first <= 0xf4) {
            remaining = 3; codepoint = first & 0x07; minimum = 0x10000;
        } else return false;
        if (remaining > s.size() - i) return false;
        while (remaining--) {
            const auto next = static_cast<unsigned char>(s[i++]);
            if ((next & 0xc0) != 0x80) return false;
            codepoint = (codepoint << 6) | (next & 0x3f);
        }
        if (codepoint < minimum || codepoint > 0x10ffff ||
            (codepoint >= 0xd800 && codepoint <= 0xdfff)) return false;
    }
    return true;
}

bool ascii(std::string_view s) {
    for (unsigned char c : s)
        if (c > 0x7f) return false;
    return true;
}

std::string quote(std::string_view s) {
    constexpr char hex[] = "0123456789abcdef";
    std::string result = "\"";
    for (unsigned char c : s) {
        switch (c) {
        case '"': result += "\\\""; break;
        case '\\': result += "\\\\"; break;
        case '\b': result += "\\b"; break;
        case '\f': result += "\\f"; break;
        case '\n': result += "\\n"; break;
        case '\r': result += "\\r"; break;
        case '\t': result += "\\t"; break;
        default:
            if (c < 0x20) {
                result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15];
            } else result += static_cast<char>(c);
        }
    }
    return result + "\"";
}
std::string error_json(Failure e) {
    return "{\"code\":" + quote(e.code) + ",\"message\":" + quote(e.message) + "}";
}
int fail(Failure e) {
    std::cerr << "{\"protocol\":1,\"error\":" << error_json(e) << "}\n";
    return 2;
}

// Proleptic Gregorian civil arithmetic, independent of libc/host timezone and ICU cutovers.
std::int64_t days_from_civil(int y, unsigned m, unsigned d) {
    y -= m <= 2;
    const int era = (y >= 0 ? y : y - 399) / 400;
    const unsigned yo = static_cast<unsigned>(y - era * 400);
    const unsigned doy = (153 * (m > 2 ? m - 3 : m + 9) + 2) / 5 + d - 1;
    return static_cast<std::int64_t>(era) * 146097 + yo * 365 + yo / 4 - yo / 100 + doy - 719468;
}
std::string civil(std::int64_t ms) {
    std::int64_t days = ms / day_ms, tod = ms % day_ms;
    if (tod < 0) { tod += day_ms; --days; }
    const auto z = days + 719468;
    const auto era = (z >= 0 ? z : z - 146096) / 146097;
    const unsigned doe = static_cast<unsigned>(z - era * 146097);
    const unsigned yo = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    int y = static_cast<int>(yo) + static_cast<int>(era) * 400;
    const unsigned doy = doe - (365 * yo + yo / 4 - yo / 100);
    const unsigned mp = (5 * doy + 2) / 153;
    const unsigned d = doy - (153 * mp + 2) / 5 + 1;
    const unsigned m = mp < 10 ? mp + 3 : mp - 9;
    y += m <= 2;
    if (y < 1 || y > 9999) throw range_error;
    char result[32];
    std::snprintf(result, sizeof result, "%04d-%02u-%02uT%02d:%02d:%02d.%03d",
                  y, m, d, static_cast<int>(tod / 3600000),
                  static_cast<int>(tod / 60000 % 60), static_cast<int>(tod / 1000 % 60),
                  static_cast<int>(tod % 1000));
    return result;
}
int digits(std::string_view s, std::size_t pos, std::size_t count) {
    if (pos + count > s.size()) throw instant_error;
    int n = 0;
    for (std::size_t i = pos; i < pos + count; ++i) {
        if (s[i] < '0' || s[i] > '9') throw instant_error;
        n = n * 10 + s[i] - '0';
    }
    return n;
}
std::int64_t parse_instant(std::string_view s) {
    if (s.size() > max_instant_chars || !ascii(s)) throw instant_error;
    if (s.size() < 20 || s.size() > 29 || s[4] != '-' || s[7] != '-' ||
        s[10] != 'T' || s[13] != ':' || s[16] != ':') throw instant_error;
    int y = digits(s, 0, 4), m = digits(s, 5, 2), d = digits(s, 8, 2);
    int h = digits(s, 11, 2), minute = digits(s, 14, 2), second = digits(s, 17, 2);
    if (y == 0) throw instant_error;
    constexpr int month_days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
    if (m < 1 || m > 12 || d < 1 || h > 23 || minute > 59 || second > 59) throw instant_error;
    int limit = month_days[m - 1] + (m == 2 && y % 4 == 0 && (y % 100 != 0 || y % 400 == 0));
    if (d > limit) throw instant_error;
    std::size_t p = 19;
    int fraction = 0;
    if (s[p] == '.') {
        const auto start = ++p;
        while (p < s.size() && s[p] >= '0' && s[p] <= '9') ++p;
        const auto count = p - start;
        if (count < 1 || count > 3) throw instant_error;
        fraction = digits(s, start, count);
        for (std::size_t i = count; i < 3; ++i) fraction *= 10;
    }
    int offset = 0;
    if (p < s.size() && s[p] == 'Z' && p + 1 == s.size()) {
        // UTC is explicit, never the host's default timezone.
    } else if (p + 6 == s.size() && (s[p] == '+' || s[p] == '-') && s[p + 3] == ':') {
        int oh = digits(s, p + 1, 2), om = digits(s, p + 4, 2);
        if (oh > 23 || om > 59 || (s[p] == '-' && oh == 0 && om == 0)) throw instant_error;
        offset = (oh * 60 + om) * 60 * (s[p] == '-' ? -1 : 1);
    } else throw instant_error;
    const auto result = days_from_civil(y, m, d) * day_ms + h * 3600000LL +
                        minute * 60000LL + second * 1000LL + fraction - offset * 1000LL;
    civil(result);
    return result;
}

void initialize_icu() {
    UErrorCode status = U_ZERO_ERROR;
    udata_setFileAccess(UDATA_NO_FILES, &status);
    if (U_FAILURE(status)) throw icu_error;
    udata_setCommonData(U_ICUDATA_ENTRY_POINT, &status);
    if (U_FAILURE(status)) throw icu_error;
    const char* tz = ucal_getTZDataVersion(&status);
    UVersionInfo info;
    u_getVersion(info);
    if (U_FAILURE(status) || !tz || std::string_view(tz) != "2025a" || info[0] != 77 || info[1] != 1)
        throw icu_error;
    using Enumeration = std::unique_ptr<UEnumeration, decltype(&uenum_close)>;
    Enumeration zones(ucal_openTimeZoneIDEnumeration(UCAL_ZONE_TYPE_ANY, nullptr, nullptr, &status),
                      uenum_close);
    if (U_FAILURE(status) || !zones) throw icu_error;
    int32_t length;
    while (const char* id = uenum_next(zones.get(), &length, &status))
        system_zones.emplace(id, static_cast<std::size_t>(length));
    if (U_FAILURE(status) || system_zones.empty()) throw icu_error;
}
std::string convert(std::string_view text, std::string_view zone, bool protocol) {
    const auto ms = parse_instant(text);
    if (zone.empty() || zone.size() > max_zone_bytes || !ascii(zone) ||
        system_zones.find(std::string(zone)) == system_zones.end()) throw zone_error;
    UChar id[256], canonical[256];
    int32_t length = 0;
    UErrorCode status = U_ZERO_ERROR;
    u_strFromUTF8(id, 256, &length, zone.data(), static_cast<int32_t>(zone.size()), &status);
    if (U_FAILURE(status)) throw zone_error;
    UBool system = false;
    ucal_getCanonicalTimeZoneID(id, length, canonical, 256, &system, &status);
    if (U_FAILURE(status) || !system) throw zone_error;
    using Calendar = std::unique_ptr<UCalendar, decltype(&ucal_close)>;
    Calendar cal(ucal_open(id, length, "en_US_POSIX@calendar=gregorian", UCAL_GREGORIAN, &status), ucal_close);
    if (U_FAILURE(status) || !cal) throw icu_error;
    ucal_setGregorianChange(cal.get(), -8.64e15, &status);
    ucal_setMillis(cal.get(), static_cast<UDate>(ms), &status);
    const auto raw = ucal_get(cal.get(), UCAL_ZONE_OFFSET, &status);
    const auto dst = ucal_get(cal.get(), UCAL_DST_OFFSET, &status);
    if (U_FAILURE(status) || (raw + dst) % 1000 != 0) throw icu_error;
    const int seconds = (raw + dst) / 1000, absolute = seconds < 0 ? -seconds : seconds;
    char suffix[20];
    if (absolute % 60)
        std::snprintf(suffix, sizeof suffix, "%c%02d:%02d:%02d", seconds < 0 ? '-' : '+',
                      absolute / 3600, absolute / 60 % 60, absolute % 60);
    else
        std::snprintf(suffix, sizeof suffix, "%c%02d:%02d", seconds < 0 ? '-' : '+',
                      absolute / 3600, absolute / 60 % 60);
    return std::string(protocol ? "{\"protocol\":1," : "{") +
        "\"instant\":" + quote(civil(ms) + "Z") + ",\"zone\":" + quote(zone) +
        ",\"local\":" + quote(civil(ms + seconds * 1000LL) + suffix) +
        ",\"offsetSeconds\":" + std::to_string(seconds) + "}";
}
int run(const std::vector<std::string>& args) {
    if (args.size() > max_items * 3 + 1) return fail(arguments);
    std::size_t total = 0;
    for (const auto& s : args) {
        total += s.size();
        if (total > max_total_bytes || !valid_utf8(s)) return fail(arguments);
    }
    if (args == std::vector<std::string>{"--version"}) {
        std::cout << "{\"protocol\":1,\"version\":\"0.1.2\"}\n";
        return 0;
    }
    if (!args.empty() && args[0] == "inspect-args") {
        if (args.size() > max_items + 1 || total - args[0].size() > max_inspect_bytes)
            return fail(arguments);
        for (std::size_t i = 1; i < args.size(); ++i)
            if (args[i].size() > max_inspect_arg_bytes) return fail(arguments);
        std::string result = "{\"protocol\":1,\"args\":[";
        for (std::size_t i = 1; i < args.size(); ++i) {
            if (i > 1) result += ',';
            result += quote(args[i]);
        }
        std::cout << result << "]}\n";
        return 0;
    }
    try {
        if (args == std::vector<std::string>{"--info"}) {
            initialize_icu();
            UVersionInfo unicode;
            char version[U_MAX_VERSION_STRING_LENGTH];
            u_getUnicodeVersion(unicode);
            u_versionToString(unicode, version);
            std::cout << "{\"protocol\":1,\"version\":\"0.1.2\",\"icuVersion\":\"77.1\","
                         "\"dataVersion\":\"77.1\",\"unicodeVersion\":"
                      << quote(version) << ",\"tzdataVersion\":\"2025a\","
                         "\"dataMode\":\"embedded-static\"}\n";
            return 0;
        }
        if (args.size() == 5 && args[0] == "convert") {
            const std::string *text, *zone;
            if (args[1] == "--instant" && args[3] == "--zone") { text = &args[2]; zone = &args[4]; }
            else if (args[1] == "--zone" && args[3] == "--instant") { text = &args[4]; zone = &args[2]; }
            else return fail(arguments);
            initialize_icu();
            const auto value = convert(*text, *zone, true);
            std::cout << value << '\n';
            return 0;
        }
        if (!args.empty() && args[0] == "batch" && args.size() >= 4 &&
            (args.size() - 1) % 3 == 0 && (args.size() - 1) / 3 <= max_items) {
            for (std::size_t i = 1; i < args.size(); i += 3)
                if (args[i] != "--item") return fail(arguments);
            bool initialized = true;
            try { initialize_icu(); }
            catch (Failure) { initialized = false; }
            std::string result = "{\"protocol\":1,\"results\":[";
            for (std::size_t i = 1; i < args.size(); i += 3) {
                if (i > 1) result += ',';
                try {
                    if (!initialized) throw icu_error;
                    result += "{\"ok\":true,\"value\":" + convert(args[i + 1], args[i + 2], false) + "}";
                }
                catch (Failure e) { result += "{\"ok\":false,\"error\":" + error_json(e) + "}"; }
            }
            std::cout << result << "]}\n";
            return 0;
        }
        return fail(arguments);
    } catch (Failure e) { return fail(e); }
}
}

#ifdef _WIN32
int wmain(int argc, wchar_t* argv[]) {
    _setmode(_fileno(stdout), _O_BINARY);
    _setmode(_fileno(stderr), _O_BINARY);
    if (argc < 1 || static_cast<std::size_t>(argc - 1) > max_items * 3 + 1)
        return fail(arguments);
    try {
        std::vector<std::string> args;
        for (int i = 1; i < argc; ++i) {
            const int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, nullptr, 0, nullptr, nullptr);
            if (!size || size > static_cast<int>(max_total_bytes + 1)) return fail(arguments);
            std::string s(size, '\0');
            if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[i], -1, s.data(), size, nullptr, nullptr))
                return fail(arguments);
            s.pop_back();
            args.push_back(std::move(s));
        }
        return run(args);
    } catch (...) { return fail(icu_error); }
}
#else
int main(int argc, char* argv[]) {
    if (argc < 1 || static_cast<std::size_t>(argc - 1) > max_items * 3 + 1)
        return fail(arguments);
    try { return run(std::vector<std::string>(argv + 1, argv + argc)); }
    catch (...) { return fail(icu_error); }
}
#endif
