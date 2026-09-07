#include "monitor_client.h"
#include "i18n.h"
#include <httplib.h>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <thread>
#include <utility>

namespace localcodex {
using namespace std::chrono_literals;
namespace {
double steady_seconds() {
    return std::chrono::duration<double>(
        std::chrono::steady_clock::now().time_since_epoch()
    ).count();
}


std::string url_encode(const std::string& value) {
    std::ostringstream out;
    out << std::hex << std::uppercase;
    for (unsigned char ch : value) {
        if (std::isalnum(ch) || ch == '-' || ch == '_' || ch == '.' || ch == '~') out << ch;
        else out << '%' << std::setw(2) << std::setfill('0') << static_cast<int>(ch);
    }
    return out.str();
}


}
class MonitorClient::Impl {
public:
    Impl(std::string host, int port, int refresh_ms, std::filesystem::path export_dir)
        : host_(std::move(host)), port_(port), refresh_ms_(refresh_ms),
          export_dir_(std::move(export_dir)), event_http_(host_, port_), sse_(event_http_, "/monitor/events") {
        event_http_.set_connection_timeout(1, 0);
        event_http_.set_read_timeout(20, 0);
        event_worker_ = std::thread(&Impl::run_events, this);
        statistics_worker_ = std::thread(&Impl::run_statistics, this);
    }
    ~Impl() {
        stop_ = true;
        sse_.stop();
        if (event_worker_.joinable()) event_worker_.join();
        if (statistics_worker_.joinable()) statistics_worker_.join();
    }

    localcodex::MonitorState state() const { std::scoped_lock guard(mutex_); return state_; }
    localcodex::GraphView graph_view(localcodex::GraphRange range) const {
        std::scoped_lock guard(mutex_);
        return graph_.view(range, state_.turn_id);
    }
    void select_period(const std::string& period) {
        std::scoped_lock guard(mutex_); requested_period_ = period; force_statistics_ = true;
    }
    void select_session(const std::string& session) {
        std::scoped_lock guard(mutex_); selected_session_ = session; force_statistics_ = true;
    }
    void set_refresh_ms(int value) { refresh_ms_ = std::clamp(value, 1000, 5000); }
    void set_history_visible(bool value) { history_visible_ = value; }
    bool export_usage(const std::string& format, std::string& result) {
        std::string period, session;
        { std::scoped_lock guard(mutex_); period = requested_period_; session = selected_session_; }
        std::string path = "/monitor/statistics/export?period=" + url_encode(period) + "&format=" + format;
        if (!session.empty()) path += "&session_id=" + url_encode(session);
        httplib::Client http(host_, port_);
        http.set_connection_timeout(1, 0);
        http.set_read_timeout(2, 0);
        auto response = http.Get(path);
        if (!response || response->status != 200) { result = localcodex::i18n::tr("export.failed"); return false; }
        try {
            auto dir = export_dir_;
            std::filesystem::create_directories(dir);
            auto file = dir / ("localcodex-" + period + "." + format);
            std::ofstream output(file, std::ios::binary);
            output << response->body;
            result = file.string();
            return true;
        } catch (...) { result = localcodex::i18n::tr("export.write_failed"); return false; }
    }

private:
    void apply_live(const nlohmann::json& value) {
        std::scoped_lock guard(mutex_);
        localcodex::apply_snapshot(state_, value);
        graph_.add(state_, steady_seconds());
    }

    void run_events() {
        sse_.on_event("snapshot", [this](const httplib::sse::SSEMessage& message) {
            try { apply_live(nlohmann::json::parse(message.data)); } catch (...) {}
        });
        sse_.set_reconnect_interval(1000).set_max_reconnect_attempts(1);
        sse_.start();

        // Compatibility fallback for schema-3 routers without /monitor/events.
        while (!stop_) {
            try {
                httplib::Client http(host_, port_);
                http.set_connection_timeout(0, 500000);
                http.set_read_timeout(1, 0);
                auto response = http.Get("/monitor/snapshot");
                if (response && response->status == 200) {
                    apply_live(nlohmann::json::parse(response->body));
                } else {
                    std::scoped_lock guard(mutex_); state_.online = false; state_.phase = "offline";
                }
            } catch (...) { std::scoped_lock guard(mutex_); state_.online = false; state_.phase = "offline"; }
            const int delay = refresh_ms_.load();
            for (int elapsed = 0; elapsed < delay && !stop_; elapsed += 50) std::this_thread::sleep_for(50ms);
        }
    }

    void run_statistics() {
        auto next_stats = std::chrono::steady_clock::now();
        while (!stop_) {
            bool refresh_stats{};
            std::string period, session;
            {
                std::scoped_lock guard(mutex_);
                refresh_stats = force_statistics_ ||
                    (history_visible_.load() && std::chrono::steady_clock::now() >= next_stats);
                force_statistics_ = false;
                period = requested_period_;
                session = selected_session_;
            }
            if (state().online && refresh_stats) {
                std::string path = "/monitor/statistics?period=" + url_encode(period) + "&page_size=100";
                if (!session.empty()) path += "&session_id=" + url_encode(session);
                try {
                    httplib::Client http(host_, port_);
                    auto response = http.Get(path);
                    if (response && response->status == 200) {
                        std::scoped_lock guard(mutex_);
                        localcodex::apply_statistics(state_, nlohmann::json::parse(response->body));
                    }
                } catch (...) {}
                next_stats = std::chrono::steady_clock::now() + 10s;
            }
            for (int elapsed = 0; elapsed < 250 && !stop_; elapsed += 50) std::this_thread::sleep_for(50ms);
        }
    }

    std::string host_;
    int port_{};
    std::atomic_int refresh_ms_{1000};
    std::atomic_bool history_visible_{};
    mutable std::mutex mutex_;
    localcodex::MonitorState state_;
    localcodex::SessionGraph graph_;
    std::string requested_period_{"24h"};
    std::string selected_session_;
    bool force_statistics_{true};
    std::atomic_bool stop_{};
    std::filesystem::path export_dir_;
    httplib::Client event_http_;
    httplib::sse::SSEClient sse_;
    std::thread event_worker_;
    std::thread statistics_worker_;
};

MonitorClient::MonitorClient(std::string host, int port, int refresh_ms, std::filesystem::path export_dir)
    : impl_(std::make_unique<Impl>(std::move(host), port, refresh_ms, std::move(export_dir))) {}
MonitorClient::~MonitorClient() = default;
MonitorState MonitorClient::state() const { return impl_->state(); }
GraphView MonitorClient::graph_view(GraphRange range) const { return impl_->graph_view(range); }
void MonitorClient::select_period(const std::string& value) { impl_->select_period(value); }
void MonitorClient::select_session(const std::string& value) { impl_->select_session(value); }
void MonitorClient::set_refresh_ms(int value) { impl_->set_refresh_ms(value); }
void MonitorClient::set_history_visible(bool value) { impl_->set_history_visible(value); }
bool MonitorClient::export_usage(const std::string& format, std::string& result) { return impl_->export_usage(format, result); }
}
