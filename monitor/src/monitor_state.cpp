#include "monitor_state.h"

#include <algorithm>
#include <cmath>

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
    state.sequence = value_or<std::int64_t>(value, "sequence");
    state.online = true;
    state.active = value_or<bool>(value, "active");
    const auto& launchers = object_or_empty(value, "sessions");
    state.ever_had_launcher = value_or<bool>(launchers, "ever_had_lease");
    state.active_launchers = value_or<std::int64_t>(launchers, "active_count");
    state.phase = value_or<std::string>(value, "phase", "idle");
    state.model = value_or<std::string>(value, "model", "-");
    state.session_id = value_or<std::string>(value, "session_id");
    state.turn_id = value_or<std::string>(value, "turn_id");
    state.updated_at = value_or<double>(value, "updated_at");
    state.elapsed_seconds = value_or<double>(value, "elapsed_seconds");

    const auto& tokens = object_or_empty(value, "tokens");
    state.turn_input = value_or<std::int64_t>(tokens, "input");
    state.turn_output = value_or<std::int64_t>(tokens, "output");
    if (state.active) state.turn_output = value_or<std::int64_t>(tokens, "estimated_output");

    const auto& performance = object_or_empty(value, "performance");
    state.tokens_per_second = value_or<double>(performance, "tokens_per_second");
    state.ttft_seconds = value_or<double>(performance, "time_to_first_token_seconds");

    const auto& session = object_or_empty(value, "session");
    state.session_input = value_or<std::int64_t>(session, "live_input_tokens",
                                                 value_or<std::int64_t>(session, "input_tokens"));
    state.session_output = value_or<std::int64_t>(session, "live_output_tokens");
    state.session_saved_usd = value_or<double>(session, "live_saved_usd",
                                               value_or<double>(session, "estimated_saved_usd"));

    const auto& turn = object_or_empty(value, "turn");
    state.role = value_or<std::string>(turn, "role");
    state.last_tool = value_or<std::string>(turn, "last_tool");
    const auto& total = object_or_empty(value, "total");
    state.total_input = value_or<std::int64_t>(total, "live_input_tokens",
                                               value_or<std::int64_t>(total, "input_tokens"));
    state.total_output = value_or<std::int64_t>(total, "live_output_tokens",
                                                value_or<std::int64_t>(total, "output_tokens"));
    state.total_saved_usd = value_or<double>(total, "live_saved_usd",
                                             value_or<double>(total, "estimated_saved_usd"));
    const auto& context = object_or_empty(value, "context");
    state.context_used = value_or<std::int64_t>(context, "used_tokens");
    state.context_length = value_or<std::int64_t>(context, "capacity_tokens");

    state.ollama_version = value_or<std::string>(value, "ollama_version");
    const auto& runtime = object_or_empty(value, "ollama_runtime");
    state.runtime_name = value_or<std::string>(runtime, "name");
    state.parameter_size = value_or<std::string>(runtime, "parameter_size");
    state.quantization = value_or<std::string>(runtime, "quantization_level");
    state.context_length = value_or<std::int64_t>(runtime, "context_length", state.context_length);
    state.model_size_bytes = value_or<std::int64_t>(runtime, "size_bytes");
    state.vram_size_bytes = value_or<std::int64_t>(runtime, "size_vram_bytes");
}

void SessionGraph::add(const MonitorState& state) {
    if (state.session_id.empty() || state.updated_at <= 0 || !std::isfinite(state.tokens_per_second)) return;
    if (session_id_ != state.session_id) {
        session_id_ = state.session_id; turn_id_.clear(); points_.clear();
        started_at_ = state.updated_at; last_at_ = 0;
    }
    if (state.updated_at <= last_at_) return;
    const double x = std::max(0.0, state.updated_at - started_at_);
    if (!turn_id_.empty() && turn_id_ != state.turn_id) points_.push_back({x, 0.0});
    turn_id_ = state.turn_id;
    points_.push_back({x, std::max(0.0, state.tokens_per_second)});
    last_at_ = state.updated_at;
    if (points_.size() > 4096) compact();
}

void SessionGraph::compact() {
    std::vector<GraphPoint> reduced;
    reduced.reserve(points_.size() / 2 + 2);
    for (std::size_t begin = 0; begin < points_.size(); begin += 4) {
        const auto end = std::min(points_.size(), begin + 4);
        auto minimum = points_.begin() + static_cast<std::ptrdiff_t>(begin);
        auto maximum = minimum;
        for (auto item = minimum; item != points_.begin() + static_cast<std::ptrdiff_t>(end); ++item) {
            if (item->y < minimum->y) minimum = item;
            if (item->y > maximum->y) maximum = item;
        }
        if (minimum->x <= maximum->x) { reduced.push_back(*minimum); if (minimum != maximum) reduced.push_back(*maximum); }
        else { reduced.push_back(*maximum); reduced.push_back(*minimum); }
    }
    points_.swap(reduced);
}

double SessionGraph::x_max() const { return points_.empty() ? 1.0 : std::max(1.0, points_.back().x); }
double SessionGraph::y_max() const {
    double maximum = 1.0;
    for (const auto& point : points_) maximum = std::max(maximum, point.y);
    return maximum * 1.1;
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
            value_or<std::string>(row, "title", "Untitled session"),
            value_or<std::int64_t>(row, "input_tokens"),
            value_or<std::int64_t>(row, "output_tokens"),
            value_or<std::int64_t>(row, "turns"),
            value_or<double>(row, "comparison_cost_usd"),
        });
    }
}

}  // namespace localcodex
