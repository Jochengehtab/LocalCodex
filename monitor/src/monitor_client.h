#pragma once
#include "monitor_state.h"
#include <filesystem>
#include <memory>
#include <string>

namespace localcodex {
class MonitorClient {
public:
    MonitorClient(std::string host, int port, int refresh_ms, std::filesystem::path export_dir);
    ~MonitorClient();
    MonitorClient(const MonitorClient&) = delete;
    MonitorClient& operator=(const MonitorClient&) = delete;
    MonitorState state() const;
    GraphView graph_view(GraphRange range) const;
    void select_period(const std::string& period);
    void select_session(const std::string& session);
    void set_refresh_ms(int value);
    void set_history_visible(bool value);
    bool export_usage(const std::string& format, std::string& result);
private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};
}
