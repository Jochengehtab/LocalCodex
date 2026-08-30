#include "monitor_state.h"

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
    int refresh_ms{250};
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
        result.refresh_ms = std::clamp(value.value("refresh_ms", 250), 100, 2000);
    } catch (...) {}
    return result;
}

void save_settings(const Settings& value) {
    try {
        std::filesystem::create_directories(settings_path().parent_path());
        std::ofstream output(settings_path());
        output << nlohmann::json{{"theme", value.theme}, {"period", value.period},
                                 {"refresh_ms", value.refresh_ms}}.dump(2) << '\n';
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
        : host_(std::move(host)), port_(port), refresh_ms_(refresh_ms), worker_(&MonitorClient::run, this) {}
    ~MonitorClient() { stop_ = true; if (worker_.joinable()) worker_.join(); }

    localcodex::MonitorState state() const { std::scoped_lock guard(mutex_); return state_; }
    std::vector<double> samples() const { std::scoped_lock guard(mutex_); return samples_; }
    void select_period(const std::string& period) {
        std::scoped_lock guard(mutex_); requested_period_ = period; force_statistics_ = true;
    }
    void select_session(const std::string& session) {
        std::scoped_lock guard(mutex_); selected_session_ = session; force_statistics_ = true;
    }
    void set_refresh_ms(int value) { refresh_ms_ = std::clamp(value, 100, 2000); }
    bool export_usage(const std::string& format, std::string& result) {
        std::string period, session;
        { std::scoped_lock guard(mutex_); period = requested_period_; session = selected_session_; }
        std::string path = "/monitor/statistics/export?period=" + url_encode(period) + "&format=" + format;
        if (!session.empty()) path += "&session_id=" + url_encode(session);
        httplib::Client http(host_, port_);
        http.set_connection_timeout(1, 0);
        http.set_read_timeout(2, 0);
        auto response = http.Get(path);
        if (!response || response->status != 200) { result = "Export fehlgeschlagen"; return false; }
        try {
            auto dir = settings_path().parent_path() / "exports";
            std::filesystem::create_directories(dir);
            auto file = dir / ("localcodex-" + period + "." + format);
            std::ofstream output(file, std::ios::binary);
            output << response->body;
            result = file.string();
            return true;
        } catch (...) { result = "Export konnte nicht geschrieben werden"; return false; }
    }

private:
    void run() {
        auto next_stats = std::chrono::steady_clock::now();
        while (!stop_) {
            bool online = false;
            try {
                httplib::Client http(host_, port_);
                http.set_connection_timeout(0, 500000);
                http.set_read_timeout(1, 0);
                auto response = http.Get("/monitor/snapshot");
                if (response && response->status == 200) {
                    auto json = nlohmann::json::parse(response->body);
                    std::scoped_lock guard(mutex_);
                    localcodex::apply_snapshot(state_, json);
                    samples_.push_back(state_.tokens_per_second);
                    if (samples_.size() > 240) samples_.erase(samples_.begin());
                    online = true;
                }
            } catch (...) {}
            if (!online) { std::scoped_lock guard(mutex_); state_.online = false; state_.phase = "offline"; }

            bool refresh_stats{};
            std::string period, session;
            {
                std::scoped_lock guard(mutex_);
                refresh_stats = force_statistics_ || std::chrono::steady_clock::now() >= next_stats;
                force_statistics_ = false;
                period = requested_period_;
                session = selected_session_;
            }
            if (online && refresh_stats) {
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
            int delay;
            { std::scoped_lock guard(mutex_); delay = state_.active ? refresh_ms_.load() : 1000; }
            for (int elapsed = 0; elapsed < delay && !stop_; elapsed += 50) std::this_thread::sleep_for(50ms);
        }
    }

    std::string host_;
    int port_{};
    std::atomic_int refresh_ms_{250};
    mutable std::mutex mutex_;
    localcodex::MonitorState state_;
    std::vector<double> samples_;
    std::string requested_period_{"24h"};
    std::string selected_session_;
    bool force_statistics_{true};
    std::atomic_bool stop_{};
    std::thread worker_;
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
    SDL_Window* window = SDL_CreateWindow("Local Codex Monitor", 1120, 760,
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
    SDL_Tray* tray = SDL_CreateTray(nullptr, "Local Codex Monitor");
    if (tray) {
        auto* menu = SDL_CreateTrayMenu(tray);
        auto* show = SDL_InsertTrayEntryAt(menu, -1, "Monitor anzeigen", SDL_TRAYENTRY_BUTTON);
        auto* exit = SDL_InsertTrayEntryAt(menu, -1, "Beenden", SDL_TRAYENTRY_BUTTON);
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
        ImGui::Text("Local Codex Monitor  %s", LOCALCODEX_VERSION);
        ImGui::SetWindowFontScale(1.0f);
        ImGui::SameLine(ImGui::GetWindowWidth() - 170);
        ImGui::TextColored(state.online ? ImVec4(0.25f, 0.85f, 0.65f, 1) : ImVec4(1, .35f, .35f, 1),
                           state.online ? "Router online" : "Router offline");
        if (ImGui::Button("Dashboard")) tab = 0;
        ImGui::SameLine(); if (ImGui::Button("Historie")) tab = 1;
        ImGui::SameLine(); if (ImGui::Button("Einstellungen")) tab = 2;
        ImGui::Separator();

        if (tab == 0) {
            ImGui::TextColored(ImVec4(.3f, .8f, 1, 1), "%s", state.phase.c_str());
            ImGui::SameLine(); ImGui::Text("%s", state.model.c_str());
            if (ImGui::BeginTable("cards", 4, ImGuiTableFlags_SizingStretchSame)) {
                ImGui::TableNextColumn(); metric_card("SITZUNG INPUT", compact_number(state.session_input), "exakt von Ollama");
                ImGui::TableNextColumn(); metric_card("SITZUNG OUTPUT", compact_number(state.session_output), state.active ? "inkl. Live-Schätzung" : "exakt von Ollama");
                ImGui::TableNextColumn(); metric_card("GESCHWINDIGKEIT", std::to_string(state.tokens_per_second).substr(0, 5), "Tokens/s");
                std::ostringstream saved; saved << '$' << std::fixed << std::setprecision(6) << state.session_saved_usd;
                ImGui::TableNextColumn(); metric_card("GESPART / SITZUNG", saved.str(), "API-Vergleich");
                ImGui::EndTable();
            }
            ImGui::BeginChild("throughput", ImVec2(ImGui::GetContentRegionAvail().x * .62f, 300), ImGuiChildFlags_Borders);
            ImGui::Text("Live Throughput");
            auto samples = client.samples();
            if (ImPlot::BeginPlot("##throughput_plot", ImVec2(-1, -1))) {
                ImPlot::SetupAxes(nullptr, "Tokens/s", ImPlotAxisFlags_NoTickLabels, ImPlotAxisFlags_AutoFit);
                if (!samples.empty()) ImPlot::PlotLine("Tokens/s", samples.data(), static_cast<int>(samples.size()));
                ImPlot::EndPlot();
            }
            ImGui::EndChild();
            ImGui::SameLine();
            ImGui::BeginChild("runtime", ImVec2(0, 300), ImGuiChildFlags_Borders);
            ImGui::Text("Ollama Runtime");
            ImGui::Separator();
            ImGui::TextWrapped("%s", state.runtime_name.empty() ? state.model.c_str() : state.runtime_name.c_str());
            ImGui::TextDisabled("%s  %s", state.parameter_size.c_str(), state.quantization.c_str());
            ImGui::Text("Kontext: %s", compact_number(state.context_length).c_str());
            ImGui::Text("Ollama: %s", state.ollama_version.c_str());
            ImGui::Text("TTFT: %.2f s", state.ttft_seconds);
            ImGui::Text("Laufzeit: %.1f s", state.elapsed_seconds);
            ImGui::EndChild();
        } else if (tab == 1) {
            const char* periods[] = {"24h", "7d", "30d", "all"};
            for (const char* period : periods) {
                if (ImGui::RadioButton(period, settings.period == period)) {
                    settings.period = period; client.select_period(period); save_settings(settings);
                }
                ImGui::SameLine();
            }
            if (ImGui::Button("Alle Sitzungen")) client.select_session("");
            ImGui::SameLine(); if (ImGui::Button("CSV exportieren")) client.export_usage("csv", export_status);
            ImGui::SameLine(); if (ImGui::Button("JSON exportieren")) client.export_usage("json", export_status);
            if (!export_status.empty()) ImGui::TextDisabled("%s", export_status.c_str());
            ImGui::Text("Zeitraum: %s Input / %s Output / $%.6f gespart",
                compact_number(state.period_input).c_str(), compact_number(state.period_output).c_str(), state.period_saved_usd);
            if (ImGui::BeginTable("sessions", 6, ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY)) {
                ImGui::TableSetupColumn("Sitzung"); ImGui::TableSetupColumn("Turns");
                ImGui::TableSetupColumn("Input"); ImGui::TableSetupColumn("Output");
                ImGui::TableSetupColumn("Gespart"); ImGui::TableSetupColumn("Filter");
                ImGui::TableHeadersRow();
                for (const auto& row : state.sessions) {
                    ImGui::TableNextRow(); ImGui::TableNextColumn(); ImGui::TextUnformatted(row.title.c_str());
                    ImGui::TableNextColumn(); ImGui::Text("%lld", static_cast<long long>(row.turns));
                    ImGui::TableNextColumn(); ImGui::TextUnformatted(compact_number(row.input_tokens).c_str());
                    ImGui::TableNextColumn(); ImGui::TextUnformatted(compact_number(row.output_tokens).c_str());
                    ImGui::TableNextColumn(); ImGui::Text("$%.6f", row.saved_usd);
                    ImGui::TableNextColumn();
                    ImGui::PushID(row.id.c_str());
                    if (ImGui::SmallButton("Auswählen")) client.select_session(row.id);
                    ImGui::PopID();
                }
                ImGui::EndTable();
            }
        } else {
            ImGui::Text("Darstellung");
            const char* themes[] = {"System", "Dunkel", "Hell"};
            if (ImGui::Combo("Theme", &settings.theme, themes, 3)) { apply_theme(settings.theme); save_settings(settings); }
            if (ImGui::SliderInt("Live-Aktualisierung (ms)", &settings.refresh_ms, 100, 2000)) {
                client.set_refresh_ms(settings.refresh_ms);
                save_settings(settings);
            }
            ImGui::Separator();
            ImGui::TextWrapped("Der Monitor liest ausschließlich lokale Router-Metriken. Er startet keine zweite Modellanfrage und speichert keine Denkzusammenfassungen.");
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
