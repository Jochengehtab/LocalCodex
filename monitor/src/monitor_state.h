#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace localcodex {

struct SessionRow {
    std::string id;
    std::string title;
    std::int64_t input_tokens{};
    std::int64_t output_tokens{};
    std::int64_t turns{};
    double saved_usd{};
};

struct MonitorState {
    bool online{};
    bool active{};
    bool ever_had_launcher{};
    std::int64_t active_launchers{};
    std::string phase{"offline"};
    std::string model{"-"};
    std::string session_id;
    std::int64_t turn_input{};
    std::int64_t turn_output{};
    std::int64_t session_input{};
    std::int64_t session_output{};
    std::int64_t period_input{};
    std::int64_t period_output{};
    double tokens_per_second{};
    double ttft_seconds{};
    double elapsed_seconds{};
    double session_saved_usd{};
    double period_saved_usd{};
    std::string ollama_version;
    std::string runtime_name;
    std::string parameter_size;
    std::string quantization;
    std::int64_t context_length{};
    std::vector<SessionRow> sessions;
};

void apply_snapshot(MonitorState& state, const nlohmann::json& value);
void apply_statistics(MonitorState& state, const nlohmann::json& value);

}  // namespace localcodex
