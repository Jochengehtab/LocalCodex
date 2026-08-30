#include "monitor_state.h"

#include <cassert>
#include <iostream>

int main() {
    localcodex::MonitorState state;
    localcodex::apply_snapshot(state, nlohmann::json::parse(R"({
      "active":true,"phase":"generating","model":"build","session_id":"s1",
      "elapsed_seconds":4.5,
      "tokens":{"input":12,"output":null,"estimated_output":7},
      "performance":{"tokens_per_second":2.5,"time_to_first_token_seconds":1.2},
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
        graph.add(state);
    }
    assert(graph.points().size() <= 4096);
    assert(graph.x_max() > 1000.0);
    assert(graph.y_max() >= 110.0);
    state.session_id = "new-session";
    state.updated_at = 3000.0;
    graph.add(state);
    assert(graph.points().size() == 1);
    std::cout << "monitor state tests passed\n";
}
