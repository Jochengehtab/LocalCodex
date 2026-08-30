#include "monitor_state.h"
#include "i18n.h"

#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <SDL3/SDL_opengl.h>
#include <SDL3/SDL_tray.h>
#include <httplib.h>
#include <imgui.h>
#include <imgui_impl_opengl3.h>
#include <imgui_impl_sdl3.h>
#include <implot.h>
#include <nlohmann/json.hpp>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <memory>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <fcntl.h>
#include <sys/file.h>
#include <unistd.h>
#endif

#ifndef LOCALCODEX_VERSION
#define LOCALCODEX_VERSION "dev"
#endif

namespace {
using namespace std::chrono_literals;

std::string compact_number(std::int64_t value) {
    std::ostringstream out;
    if (value >= 1'000'000) out << std::fixed << std::setprecision(2) << value / 1'000'000.0 << "M";
    else if (value >= 1'000) out << std::fixed << std::setprecision(1) << value / 1'000.0 << "K";
    else out << value;
    return out.str();
}

std::string compact_bytes(std::int64_t value) {
    if (value <= 0) return "-";
    std::ostringstream out;
    out << std::fixed << std::setprecision(1) << value / (1024.0 * 1024.0 * 1024.0) << " GiB";
    return out.str();
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

class SingletonLock {
public:
    SingletonLock() {
#ifdef _WIN32
        handle_ = CreateMutexA(nullptr, TRUE, "Local\\LocalCodexMonitorCppSingleton");
        acquired_ = handle_ && GetLastError() != ERROR_ALREADY_EXISTS;
#else
        const auto path = "/tmp/localcodex-monitor-" + std::to_string(getuid()) + ".lock";
        fd_ = open(path.c_str(), O_CREAT | O_RDWR, 0600);
        acquired_ = fd_ >= 0 && flock(fd_, LOCK_EX | LOCK_NB) == 0;
#endif
    }
    ~SingletonLock() {
#ifdef _WIN32
        if (handle_) { if (acquired_) ReleaseMutex(handle_); CloseHandle(handle_); }
#else
        if (fd_ >= 0) { if (acquired_) flock(fd_, LOCK_UN); close(fd_); }
#endif
    }
    bool acquired() const { return acquired_; }
private:
    bool acquired_{};
#ifdef _WIN32
    HANDLE handle_{};
#else
    int fd_{-1};
#endif
};

struct Settings {
    int theme{};
    std::string period{"24h"};
    int refresh_ms{1000};
    std::string language;
};

std::filesystem::path settings_path() {
    char* raw = SDL_GetPrefPath("LocalCodex", "Monitor");
    std::filesystem::path path = raw ? raw : ".";
    SDL_free(raw);
    return path / "settings.json";
}

Settings load_settings() {
    Settings result;
    try {
        std::ifstream input(settings_path());
        nlohmann::json value;
        input >> value;
        result.theme = value.value("theme", 0);
        result.period = value.value("period", "24h");
        result.refresh_ms = std::clamp(value.value("refresh_ms", 1000), 1000, 5000);
        result.language = value.value("language", "");
    } catch (...) {}
    if (result.language.empty()) result.language = localcodex::i18n::language();
    localcodex::i18n::set_language(result.language);
    return result;
}

void save_settings(const Settings& value) {
    try {
        std::filesystem::create_directories(settings_path().parent_path());
        std::ofstream output(settings_path());
        output << nlohmann::json{{"theme", value.theme}, {"period", value.period},
                                 {"refresh_ms", value.refresh_ms}, {"language", value.language}}.dump(2) << '\n';
    } catch (...) {}
}

void apply_theme(int theme) {
    const bool light = theme == 2 || (theme == 0 && SDL_GetSystemTheme() == SDL_SYSTEM_THEME_LIGHT);
    if (light) ImGui::StyleColorsLight(); else ImGui::StyleColorsDark();
    auto& style = ImGui::GetStyle();
    style.WindowRounding = 10.0f;
    style.ChildRounding = 10.0f;
    style.FrameRounding = 7.0f;
    style.GrabRounding = 7.0f;
    style.WindowPadding = {18, 18};
    style.ItemSpacing = {10, 10};
    if (!light) {
        style.Colors[ImGuiCol_WindowBg] = ImVec4(0.035f, 0.055f, 0.09f, 1.0f);
        style.Colors[ImGuiCol_ChildBg] = ImVec4(0.065f, 0.09f, 0.14f, 1.0f);
        style.Colors[ImGuiCol_Button] = ImVec4(0.03f, 0.45f, 0.72f, 1.0f);
        style.Colors[ImGuiCol_Header] = ImVec4(0.03f, 0.36f, 0.62f, 1.0f);
    }
}

class MonitorClient {
public:
    MonitorClient(std::string host, int port, int refresh_ms)
        : host_(std::move(host)), port_(port), refresh_ms_(refresh_ms),
          event_http_(host_, port_), sse_(event_http_, "/monitor/events") {
        event_http_.set_connection_timeout(1, 0);
        event_http_.set_read_timeout(20, 0);
        event_worker_ = std::thread(&MonitorClient::run_events, this);
        statistics_worker_ = std::thread(&MonitorClient::run_statistics, this);
    }
    ~MonitorClient() {
        stop_ = true;
        sse_.stop();
        if (event_worker_.joinable()) event_worker_.join();
        if (statistics_worker_.joinable()) statistics_worker_.join();
    }

    localcodex::MonitorState state() const { std::scoped_lock guard(mutex_); return state_; }
    std::vector<localcodex::GraphPoint> graph_points() const { std::scoped_lock guard(mutex_); return graph_.points(); }
    double graph_x_max() const { std::scoped_lock guard(mutex_); return graph_.x_max(); }
    double graph_y_max() const { std::scoped_lock guard(mutex_); return graph_.y_max(); }
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
            auto dir = settings_path().parent_path() / "exports";
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
        graph_.add(state_);
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
    httplib::Client event_http_;
    httplib::sse::SSEClient sse_;
    std::thread event_worker_;
    std::thread statistics_worker_;
};

void metric_card(const char* title, const std::string& value, const char* hint) {
    ImGui::BeginChild(title, ImVec2(0, 92), ImGuiChildFlags_Borders);
    ImGui::TextDisabled("%s", title);
    ImGui::SetWindowFontScale(1.55f);
    ImGui::TextUnformatted(value.c_str());
    ImGui::SetWindowFontScale(1.0f);
    ImGui::TextDisabled("%s", hint);
    ImGui::EndChild();
}

struct TrayContext { SDL_Window* window{}; bool* quit{}; };
void tray_show(void* raw, SDL_TrayEntry*) {
    auto* context = static_cast<TrayContext*>(raw);
    SDL_ShowWindow(context->window);
    SDL_RaiseWindow(context->window);
}
void tray_quit(void* raw, SDL_TrayEntry*) { *static_cast<TrayContext*>(raw)->quit = true; }

int run_monitor(int argc, char** argv) {
    std::string host = "127.0.0.1";
    int port = 18081;
    for (int i = 1; i + 1 < argc; ++i) {
        if (std::string(argv[i]) == "--router-url") {
            std::string url = argv[++i];
            const auto scheme = url.find("://");
            if (scheme != std::string::npos) url = url.substr(scheme + 3);
            const auto colon = url.rfind(':');
            if (colon != std::string::npos) { host = url.substr(0, colon); port = std::stoi(url.substr(colon + 1)); }
        }
    }

    SingletonLock singleton;
    if (!singleton.acquired()) return 0;
    if (!SDL_Init(SDL_INIT_VIDEO | SDL_INIT_EVENTS)) return 2;
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_FLAGS, 0);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_PROFILE_MASK, SDL_GL_CONTEXT_PROFILE_CORE);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MAJOR_VERSION, 3);
    SDL_GL_SetAttribute(SDL_GL_CONTEXT_MINOR_VERSION, 2);
    SDL_Window* window = SDL_CreateWindow(localcodex::i18n::tr("monitor.title"), 1120, 760,
        SDL_WINDOW_OPENGL | SDL_WINDOW_RESIZABLE | SDL_WINDOW_HIGH_PIXEL_DENSITY);
    if (!window) { SDL_Quit(); return 3; }
    SDL_GLContext gl = SDL_GL_CreateContext(window);
    SDL_GL_MakeCurrent(window, gl);
    SDL_GL_SetSwapInterval(1);

    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImPlot::CreateContext();
    ImGui::GetIO().ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;
    ImGui_ImplSDL3_InitForOpenGL(window, gl);
    ImGui_ImplOpenGL3_Init("#version 150");

    Settings settings = load_settings();
    apply_theme(settings.theme);
    MonitorClient client(host, port, settings.refresh_ms);
    client.select_period(settings.period);

    bool quit = false;
    bool window_visible = true;
    TrayContext tray_context{window, &quit};
    SDL_Tray* tray = SDL_CreateTray(nullptr, localcodex::i18n::tr("monitor.title"));
    if (tray) {
        auto* menu = SDL_CreateTrayMenu(tray);
        auto* show = SDL_InsertTrayEntryAt(menu, -1, localcodex::i18n::tr("monitor.show"), SDL_TRAYENTRY_BUTTON);
        auto* exit = SDL_InsertTrayEntryAt(menu, -1, localcodex::i18n::tr("monitor.quit"), SDL_TRAYENTRY_BUTTON);
        SDL_SetTrayEntryCallback(show, tray_show, &tray_context);
        SDL_SetTrayEntryCallback(exit, tray_quit, &tray_context);
    }

    int tab = 0;
    std::string export_status;
    auto offline_since = std::chrono::steady_clock::time_point{};
    while (!quit) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            ImGui_ImplSDL3_ProcessEvent(&event);
            if (event.type == SDL_EVENT_QUIT) quit = true;
            if (event.type == SDL_EVENT_WINDOW_CLOSE_REQUESTED && event.window.windowID == SDL_GetWindowID(window)) {
                if (tray) { SDL_HideWindow(window); window_visible = false; }
                else quit = true;
            }
            if (event.type == SDL_EVENT_WINDOW_SHOWN) window_visible = true;
        }
        auto state = client.state();
        if (state.ever_had_launcher && state.active_launchers == 0) quit = true;
        if (!state.online && state.ever_had_launcher) {
            if (offline_since == std::chrono::steady_clock::time_point{}) {
                offline_since = std::chrono::steady_clock::now();
            } else if (std::chrono::steady_clock::now() - offline_since > 3s) {
                quit = true;
            }
        } else {
            offline_since = {};
        }
        if (!window_visible) { SDL_Delay(100); continue; }

        ImGui_ImplOpenGL3_NewFrame();
        ImGui_ImplSDL3_NewFrame();
        ImGui::NewFrame();
        const auto viewport = ImGui::GetMainViewport();
        ImGui::SetNextWindowPos(viewport->WorkPos);
        ImGui::SetNextWindowSize(viewport->WorkSize);
        ImGui::Begin("LocalCodexRoot", nullptr,
            ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoSavedSettings);

        ImGui::SetWindowFontScale(1.35f);
        ImGui::Text("%s  %s", localcodex::i18n::tr("monitor.title"), LOCALCODEX_VERSION);
        ImGui::SetWindowFontScale(1.0f);
        ImGui::SameLine(ImGui::GetWindowWidth() - 170);
        ImGui::TextColored(state.online ? ImVec4(0.25f, 0.85f, 0.65f, 1) : ImVec4(1, .35f, .35f, 1),
                           "%s", state.online ? localcodex::i18n::tr("router.online") : localcodex::i18n::tr("router.offline"));
        if (ImGui::Button(localcodex::i18n::tr("tab.dashboard"))) tab = 0;
        ImGui::SameLine(); if (ImGui::Button(localcodex::i18n::tr("tab.history"))) tab = 1;
        ImGui::SameLine(); if (ImGui::Button(localcodex::i18n::tr("tab.settings"))) tab = 2;
        client.set_history_visible(tab == 1);
        ImGui::Separator();

        if (tab == 0) {
            ImGui::TextColored(ImVec4(.3f, .8f, 1, 1), "%s", state.phase.c_str());
            ImGui::SameLine(); ImGui::Text("%s", state.model.c_str());
            if (ImGui::BeginTable("cards", 4, ImGuiTableFlags_SizingStretchSame)) {
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.input"), compact_number(state.session_input), localcodex::i18n::tr("hint.ollama_exact"));
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.output"), compact_number(state.session_output), state.active ? localcodex::i18n::tr("hint.live_estimate") : localcodex::i18n::tr("hint.ollama_exact"));
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.speed"), std::to_string(state.tokens_per_second).substr(0, 5), localcodex::i18n::tr("hint.tokens_second"));
                std::ostringstream saved; saved << '$' << std::fixed << std::setprecision(6) << state.session_saved_usd;
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.saved"), saved.str(), localcodex::i18n::tr("hint.api_comparison"));
                ImGui::EndTable();
            }
            if (ImGui::BeginTable("total_cards", 4, ImGuiTableFlags_SizingStretchSame)) {
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.total_input"), compact_number(state.total_input), localcodex::i18n::tr("hint.usage_exact"));
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.total_output"), compact_number(state.total_output), localcodex::i18n::tr("hint.usage_live"));
                std::ostringstream total_saved; total_saved << '$' << std::fixed << std::setprecision(6) << state.total_saved_usd;
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.total_saved"), total_saved.str(), localcodex::i18n::tr("hint.api_comparison"));
                const double context_percent = state.context_length > 0 ? 100.0 * state.context_used / state.context_length : 0.0;
                std::ostringstream context_value; context_value << compact_number(state.context_used) << " / " << compact_number(state.context_length);
                std::ostringstream context_hint; context_hint << std::fixed << std::setprecision(1) << context_percent << "%";
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.context"), context_value.str(), context_hint.str().c_str());
                ImGui::EndTable();
            }
            ImGui::BeginChild("throughput", ImVec2(ImGui::GetContentRegionAvail().x * .62f, 300), ImGuiChildFlags_Borders);
            ImGui::TextUnformatted(localcodex::i18n::tr("throughput.title"));
            auto points = client.graph_points();
            if (ImPlot::BeginPlot("##throughput_plot", ImVec2(-1, -1))) {
                ImPlot::SetupAxes(localcodex::i18n::tr("plot.seconds"), "Tokens/s", ImPlotAxisFlags_AutoFit, ImPlotAxisFlags_AutoFit);
                ImPlot::SetupAxisLimits(ImAxis_X1, 0.0, client.graph_x_max(), ImGuiCond_Always);
                ImPlot::SetupAxisLimits(ImAxis_Y1, 0.0, client.graph_y_max(), ImGuiCond_Always);
                if (!points.empty()) ImPlot::PlotLine("Tokens/s", &points.front().x, &points.front().y,
                    static_cast<int>(points.size()), {ImPlotProp_Stride, sizeof(localcodex::GraphPoint)});
                ImPlot::EndPlot();
            }
            ImGui::EndChild();
            ImGui::SameLine();
            ImGui::BeginChild("runtime", ImVec2(0, 300), ImGuiChildFlags_Borders);
            ImGui::TextUnformatted(localcodex::i18n::tr("runtime.title"));
            ImGui::Separator();
            ImGui::TextWrapped("%s", state.runtime_name.empty() ? state.model.c_str() : state.runtime_name.c_str());
            ImGui::TextDisabled("%s  %s", state.parameter_size.c_str(), state.quantization.c_str());
            const char* context_format = localcodex::i18n::tr("runtime.context");
            ImGui::Text(context_format, compact_number(state.context_length).c_str());
            ImGui::Text("Ollama: %s", state.ollama_version.c_str());
            ImGui::Text("TTFT: %.2f s", state.ttft_seconds);
            ImGui::Text(localcodex::i18n::tr("runtime.elapsed"), state.elapsed_seconds);
            ImGui::Text(localcodex::i18n::tr("runtime.turn_tokens"), compact_number(state.turn_input).c_str(), compact_number(state.turn_output).c_str());
            ImGui::Text(localcodex::i18n::tr("runtime.memory"), compact_bytes(state.model_size_bytes).c_str(), compact_bytes(state.vram_size_bytes).c_str());
            ImGui::Text(localcodex::i18n::tr("runtime.phase"), state.phase.c_str());
            ImGui::Text(localcodex::i18n::tr("runtime.tool"), state.last_tool.empty() ? "-" : state.last_tool.c_str());
            ImGui::Text(localcodex::i18n::tr("runtime.launchers"), static_cast<long long>(state.active_launchers));
            ImGui::EndChild();
        } else if (tab == 1) {
            const char* periods[] = {"24h", "7d", "30d", "all"};
            for (const char* period : periods) {
                if (ImGui::RadioButton(period, settings.period == period)) {
                    settings.period = period; client.select_period(period); save_settings(settings);
                }
                ImGui::SameLine();
            }
            if (ImGui::Button(localcodex::i18n::tr("history.all_sessions"))) client.select_session("");
            ImGui::SameLine(); if (ImGui::Button(localcodex::i18n::tr("history.csv"))) client.export_usage("csv", export_status);
            ImGui::SameLine(); if (ImGui::Button(localcodex::i18n::tr("history.json"))) client.export_usage("json", export_status);
            if (!export_status.empty()) ImGui::TextDisabled("%s", export_status.c_str());
            ImGui::Text(localcodex::i18n::tr("history.range"),
                compact_number(state.period_input).c_str(), compact_number(state.period_output).c_str(), state.period_saved_usd);
            if (ImGui::BeginTable("sessions", 6, ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY)) {
                ImGui::TableSetupColumn(localcodex::i18n::tr("history.session")); ImGui::TableSetupColumn(localcodex::i18n::tr("history.turns"));
                ImGui::TableSetupColumn(localcodex::i18n::tr("history.input")); ImGui::TableSetupColumn(localcodex::i18n::tr("history.output"));
                ImGui::TableSetupColumn(localcodex::i18n::tr("history.saved")); ImGui::TableSetupColumn(localcodex::i18n::tr("history.filter"));
                ImGui::TableHeadersRow();
                for (const auto& row : state.sessions) {
                    ImGui::TableNextRow(); ImGui::TableNextColumn(); ImGui::TextUnformatted(row.title.c_str());
                    ImGui::TableNextColumn(); ImGui::Text("%lld", static_cast<long long>(row.turns));
                    ImGui::TableNextColumn(); ImGui::TextUnformatted(compact_number(row.input_tokens).c_str());
                    ImGui::TableNextColumn(); ImGui::TextUnformatted(compact_number(row.output_tokens).c_str());
                    ImGui::TableNextColumn(); ImGui::Text("$%.6f", row.saved_usd);
                    ImGui::TableNextColumn();
                    ImGui::PushID(row.id.c_str());
                    if (ImGui::SmallButton(localcodex::i18n::tr("history.select"))) client.select_session(row.id);
                    ImGui::PopID();
                }
                ImGui::EndTable();
            }
        } else {
            ImGui::TextUnformatted(localcodex::i18n::tr("settings.appearance"));
            const char* themes[] = {localcodex::i18n::tr("settings.system"), localcodex::i18n::tr("settings.dark"), localcodex::i18n::tr("settings.light")};
            if (ImGui::Combo(localcodex::i18n::tr("settings.theme"), &settings.theme, themes, 3)) { apply_theme(settings.theme); save_settings(settings); }
            const char* languages[] = {localcodex::i18n::tr("settings.german"), localcodex::i18n::tr("settings.english")};
            int language_index = localcodex::i18n::language() == "en" ? 1 : 0;
            if (ImGui::Combo(localcodex::i18n::tr("settings.language"), &language_index, languages, 2)) {
                settings.language = language_index == 1 ? "en" : "de";
                localcodex::i18n::set_language(settings.language);
                save_settings(settings);
            }
            if (ImGui::SliderInt(localcodex::i18n::tr("settings.refresh"), &settings.refresh_ms, 1000, 5000)) {
                client.set_refresh_ms(settings.refresh_ms);
                save_settings(settings);
            }
            ImGui::Separator();
            ImGui::TextWrapped("%s", localcodex::i18n::tr("settings.description"));
            ImGui::Text("Router: http://%s:%d", host.c_str(), port);
            ImGui::Text("Version: %s", LOCALCODEX_VERSION);
        }
        ImGui::End();
        ImGui::Render();
        glViewport(0, 0, static_cast<int>(viewport->Size.x), static_cast<int>(viewport->Size.y));
        glClearColor(0.035f, 0.055f, 0.09f, 1.0f);
        glClear(GL_COLOR_BUFFER_BIT);
        ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
        SDL_GL_SwapWindow(window);
    }

    if (tray) SDL_DestroyTray(tray);
    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplSDL3_Shutdown();
    ImPlot::DestroyContext();
    ImGui::DestroyContext();
    SDL_GL_DestroyContext(gl);
    SDL_DestroyWindow(window);
    SDL_Quit();
    return 0;
}
}  // namespace

int main(int argc, char** argv) { return run_monitor(argc, argv); }
