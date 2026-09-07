#include "monitor_state.h"
#include "router_endpoint.h"
#include "system_metrics.h"

#include <cassert>
#include <chrono>
#include <iostream>
#include <thread>

int main() {
    int port = 18081;
    assert(localcodex::parse_router_port("http://127.0.0.1:18081", port) && port == 18081);
    assert(localcodex::parse_router_port("http://localhost:1234/", port) && port == 1234);
    for (const auto* url : {"http://example.com:80", "https://127.0.0.1:80",
            "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1:12/path",
            "http://127.0.0.1:12oops", "http://127.0.0.1:", "http://127.0.0.1:-1"}) {
        assert(!localcodex::parse_router_port(url, port));
        assert(port == 1234);
    }
    localcodex::MonitorState state;
    localcodex::apply_snapshot(state, nlohmann::json::parse(R"({
      "schema_version":5,"sampled_at":1000.5,
      "active":true,"phase":"generating","model":"local-codex-build:latest","session_id":"s1",
      "elapsed_seconds":4.5,
      "tokens":{"input":12,"output":null,"estimated_output":7},
      "performance":{"tokens_per_second":2.5,"average_tokens_per_second":2.2,
                     "estimated":true,"time_to_first_token_seconds":1.2},
      "turn":{"mode":"build","source_model":"qwen3.6:35b-a3b",
              "route_reason":"implementation default"},
      "context":{"used_tokens":19,"capacity_tokens":8192},
      "total":{"input_tokens":1000,"live_input_tokens":1012,"output_tokens":200,"live_output_tokens":207,
               "estimated_saved_usd":1.2,"live_saved_usd":1.21},
      "session":{"input_tokens":100,"live_input_tokens":112,"output_tokens":20,"live_output_tokens":27,
                 "estimated_saved_usd":0.2,"live_saved_usd":0.21}
    })"));
    assert(state.online && state.active);
    assert(state.turn_output == 7);
    assert(state.session_output == 27);
    assert(state.session_input == 112 && state.total_input == 1012);
    assert(state.session_saved_usd == 0.21 && state.total_saved_usd == 1.21);
    assert(state.phase == "generating");
    assert(state.context_used == 19 && state.context_length == 8192);
    assert(state.total_output == 207);
    assert(state.sampled_at == 1000.5 && state.average_tokens_per_second == 2.2);
    assert(state.tokens_per_second_estimated && state.role == "build");
    assert(state.source_model == "qwen3.6:35b-a3b");

    localcodex::apply_statistics(state, nlohmann::json::parse(R"({
      "input_tokens":1000,"output_tokens":200,"estimated_saved_usd":1.25,
      "sessions":[{"session_id":"s1","title":"Test","input_tokens":100,
                   "output_tokens":20,"turns":2,"comparison_cost_usd":0.2}]
    })"));
    assert(state.period_input == 1000);
    assert(state.sessions.size() == 1);
    assert(state.sessions.front().title == "Test");

    localcodex::SessionGraph graph;
    state.session_id = "session";
    state.turn_id = "turn-1";
    for (int index = 0; index < 5000; ++index) {
        state.updated_at = 1000.0 + index * 0.25;
        state.tokens_per_second = index == 2500 ? 100.0 : 2.0;
        graph.add(state, 1000.0 + index * 0.25);
    }
    assert(graph.points().size() <= 4096);
    assert(graph.x_max() > 1000.0);
    assert(graph.y_max() >= 110.0);
    const auto live = graph.view(localcodex::GraphRange::Live, state.turn_id);
    assert(!live.points.empty());
    assert(live.x_max - live.x_min <= 120.0001);
    assert(live.maximum == 2.0);
    const auto turn = graph.view(localcodex::GraphRange::Turn, state.turn_id);
    assert(!turn.points.empty() && turn.x_min == 0.0);
    assert(turn.y_max >= 5.0);
    state.session_id = "new-session";
    state.updated_at = 3000.0;
    graph.add(state, 3000.0);
    assert(graph.points().size() == 1);
    assert(localcodex::utilization_percent(20, 100, 30, 150) == 80.0);
    assert(localcodex::memory_percent(50, 200) == 25.0);

    localcodex::SystemMetricsSampler sampler;
    localcodex::SystemMetrics hardware;
    for (int attempt = 0; attempt < 25; ++attempt) {
        hardware = sampler.snapshot();
        if (hardware.ram_total_bytes > 0 && hardware.cpu_percent >= 0) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    assert(hardware.ram_total_bytes > 0);
    assert(hardware.ram_used_bytes >= 0);
    assert(hardware.cpu_percent >= 0 && hardware.cpu_percent <= 100);
    std::cout << "monitor state tests passed\n";
}
