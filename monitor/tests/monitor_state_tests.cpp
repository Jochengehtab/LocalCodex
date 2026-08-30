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
      "session":{"input_tokens":100,"output_tokens":20,"live_output_tokens":27,"estimated_saved_usd":0.2}
    })"));
    assert(state.online && state.active);
    assert(state.turn_output == 7);
    assert(state.session_output == 27);
    assert(state.phase == "generating");

    localcodex::apply_statistics(state, nlohmann::json::parse(R"({
      "input_tokens":1000,"output_tokens":200,"estimated_saved_usd":1.25,
      "sessions":[{"session_id":"s1","title":"Test","input_tokens":100,
                   "output_tokens":20,"turns":2,"comparison_cost_usd":0.2}]
    })"));
    assert(state.period_input == 1000);
    assert(state.sessions.size() == 1);
    assert(state.sessions.front().title == "Test");
    std::cout << "monitor state tests passed\n";
}
