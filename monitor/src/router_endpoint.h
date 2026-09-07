#pragma once

#include <charconv>
#include <string_view>

namespace localcodex {

// Normalize localhost to numeric IPv4: no DNS lookup or remote endpoint.
inline bool parse_router_port(std::string_view url, int& port) {
    constexpr std::string_view numeric = "http://127.0.0.1:";
    constexpr std::string_view localhost = "http://localhost:";
    if (url.substr(0, numeric.size()) == numeric) url.remove_prefix(numeric.size());
    else if (url.substr(0, localhost.size()) == localhost) url.remove_prefix(localhost.size());
    else return false;
    if (!url.empty() && url.back() == '/') url.remove_suffix(1);
    if (url.empty()) return false;
    int parsed{};
    const auto result = std::from_chars(url.data(), url.data() + url.size(), parsed);
    if (result.ec != std::errc{} || result.ptr != url.data() + url.size()
        || parsed < 1 || parsed > 65535) return false;
    port = parsed;
    return true;
}

} // namespace localcodex
