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
    std::int64_t sequence{};
    bool online{};
    bool active{};
    bool ever_had_launcher{};
    std::int64_t active_launchers{};
    std::string phase{"offline"};
    std::string model{"-"};
    std::string session_id;
    std::string turn_id;
    std::string role;
    std::string source_model;
    std::string route_reason;
    std::string last_tool;
    double sampled_at{};
    double updated_at{};
    std::int64_t turn_input{};
    std::int64_t turn_output{};
    std::int64_t session_input{};
    std::int64_t session_output{};
    std::int64_t period_input{};
    std::int64_t period_output{};
    std::int64_t total_input{};
    std::int64_t total_output{};
    double tokens_per_second{};
    double average_tokens_per_second{};
    bool tokens_per_second_estimated{};
    double ttft_seconds{};
    double elapsed_seconds{};
    double session_saved_usd{};
    double period_saved_usd{};
    double total_saved_usd{};
    std::string ollama_version;
    std::string runtime_name;
    std::string parameter_size;
    std::string quantization;
    std::int64_t context_length{};
    std::int64_t context_used{};
    std::int64_t model_size_bytes{};
    std::int64_t vram_size_bytes{};
    std::vector<SessionRow> sessions;
};

struct GraphPoint {
    double x{};
    double y{};
    std::string turn_id;
    std::string phase;
    bool estimated{};
};

enum class GraphRange { Live, Turn, Session };

struct GraphView {
    std::vector<GraphPoint> points;
    double x_min{};
    double x_max{1.0};
    double y_max{5.0};
    double current{};
    double average{};
    double maximum{};
    bool has_output{};
};

class SessionGraph {
public:
    void add(const MonitorState& state, double monotonic_seconds);
    const std::vector<GraphPoint>& points() const { return points_; }
    GraphView view(GraphRange range, const std::string& current_turn) const;
    double x_max() const;
    double y_max() const;
private:
    void compact();
    std::string session_id_;
    std::string turn_id_;
    double started_at_{};
    double last_at_{-1.0};
    std::vector<GraphPoint> points_;
};

void apply_snapshot(MonitorState& state, const nlohmann::json& value);
void apply_statistics(MonitorState& state, const nlohmann::json& value);

}  // namespace localcodex
