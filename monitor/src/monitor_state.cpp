#include "monitor_state.h"

#include <algorithm>

namespace localcodex {
namespace {

template <typename T>
T value_or(const nlohmann::json& value, const char* key, T fallback = {}) {
    auto found = value.find(key);
    if (found == value.end() || found->is_null()) return fallback;
    try { return found->get<T>(); } catch (...) { return fallback; }
}

const nlohmann::json& object_or_empty(const nlohmann::json& value, const char* key) {
    static const nlohmann::json empty = nlohmann::json::object();
    auto found = value.find(key);
    return found != value.end() && found->is_object() ? *found : empty;
}

}  // namespace

void apply_snapshot(MonitorState& state, const nlohmann::json& value) {
    state.online = true;
    state.active = value_or<bool>(value, "active");
    const auto& launchers = object_or_empty(value, "sessions");
    state.ever_had_launcher = value_or<bool>(launchers, "ever_had_lease");
    state.active_launchers = value_or<std::int64_t>(launchers, "active_count");
    state.phase = value_or<std::string>(value, "phase", "idle");
    state.model = value_or<std::string>(value, "model", "-");
    state.session_id = value_or<std::string>(value, "session_id");
    state.elapsed_seconds = value_or<double>(value, "elapsed_seconds");

    const auto& tokens = object_or_empty(value, "tokens");
    state.turn_input = value_or<std::int64_t>(tokens, "input");
    state.turn_output = value_or<std::int64_t>(tokens, "output");
    if (state.active) state.turn_output = value_or<std::int64_t>(tokens, "estimated_output");

    const auto& performance = object_or_empty(value, "performance");
    state.tokens_per_second = value_or<double>(performance, "tokens_per_second");
    state.ttft_seconds = value_or<double>(performance, "time_to_first_token_seconds");

    const auto& session = object_or_empty(value, "session");
    state.session_input = value_or<std::int64_t>(session, "input_tokens");
    state.session_output = value_or<std::int64_t>(session, "live_output_tokens");
    state.session_saved_usd = value_or<double>(session, "estimated_saved_usd");

    state.ollama_version = value_or<std::string>(value, "ollama_version");
    const auto& runtime = object_or_empty(value, "ollama_runtime");
    state.runtime_name = value_or<std::string>(runtime, "name");
    state.parameter_size = value_or<std::string>(runtime, "parameter_size");
    state.quantization = value_or<std::string>(runtime, "quantization_level");
    state.context_length = value_or<std::int64_t>(runtime, "context_length");
}

void apply_statistics(MonitorState& state, const nlohmann::json& value) {
    state.period_input = value_or<std::int64_t>(value, "input_tokens");
    state.period_output = value_or<std::int64_t>(value, "output_tokens");
    state.period_saved_usd = value_or<double>(value, "estimated_saved_usd");
    state.sessions.clear();
    auto rows = value.find("sessions");
    if (rows == value.end() || !rows->is_array()) return;
    for (const auto& row : *rows) {
        if (!row.is_object()) continue;
        state.sessions.push_back({
            value_or<std::string>(row, "session_id"),
            value_or<std::string>(row, "title", "Unbenannte Sitzung"),
            value_or<std::int64_t>(row, "input_tokens"),
            value_or<std::int64_t>(row, "output_tokens"),
            value_or<std::int64_t>(row, "turns"),
            value_or<double>(row, "comparison_cost_usd"),
        });
    }
}

}  // namespace localcodex
