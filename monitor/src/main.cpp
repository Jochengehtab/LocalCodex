#include "monitor_state.h"
#include "router_endpoint.h"
#include "i18n.h"
#include "system_metrics.h"

#include <SDL3/SDL.h>
#include <SDL3/SDL_main.h>
#include <SDL3/SDL_opengl.h>
#include <SDL3/SDL_tray.h>
#include "monitor_client.h"
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
#ifndef NOMINMAX
#define NOMINMAX
#endif
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

const char* localized_phase(const std::string& phase) {
    if (phase == "loading") return localcodex::i18n::tr("phase.loading");
    if (phase == "thinking") return localcodex::i18n::tr("phase.thinking");
    if (phase == "generating") return localcodex::i18n::tr("phase.generating");
    if (phase == "tool") return localcodex::i18n::tr("phase.tool");
    if (phase == "completed") return localcodex::i18n::tr("phase.completed");
    if (phase == "error") return localcodex::i18n::tr("phase.error");
    if (phase == "idle") return localcodex::i18n::tr("phase.idle");
    return localcodex::i18n::tr("phase.offline");
}

const char* localized_mode(const std::string& mode) {
    if (mode == "plan") return localcodex::i18n::tr("mode.plan");
    if (mode == "vision") return localcodex::i18n::tr("mode.vision");
    if (mode == "build") return localcodex::i18n::tr("mode.build");
    return localcodex::i18n::tr("value.unavailable");
}

const char* localized_route_reason(const std::string& reason) {
    if (reason == "image input") return localcodex::i18n::tr("route.image");
    if (reason == "tool error recovery") return localcodex::i18n::tr("route.recovery");
    if (reason == "same Codex turn") return localcodex::i18n::tr("route.same_turn");
    if (reason == "plan or review") return localcodex::i18n::tr("route.plan");
    if (reason == "implementation default") return localcodex::i18n::tr("route.implementation");
    return reason.empty() ? localcodex::i18n::tr("value.unavailable") : reason.c_str();
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


void metric_card(const char* title, const std::string& value, const char* hint) {
    ImGui::BeginChild(
        title, ImVec2(0, 92), ImGuiChildFlags_Borders,
        ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoScrollWithMouse
    );
    ImGui::TextDisabled("%s", title);
    ImGui::SetWindowFontScale(1.55f);
    ImGui::TextUnformatted(value.c_str());
    ImGui::SetWindowFontScale(1.0f);
    ImGui::TextDisabled("%s", hint);
    ImGui::EndChild();
}

void resource_bar(
    const char* label,
    double percent,
    const std::string& detail = {}
) {
    ImGui::TextUnformatted(label);
    std::string overlay = localcodex::i18n::tr("value.unavailable");
    float fraction{};
    if (percent >= 0) {
        std::ostringstream value;
        value << std::fixed << std::setprecision(1) << percent << '%';
        if (!detail.empty()) value << "  " << detail;
        overlay = value.str();
        fraction = static_cast<float>(std::clamp(percent / 100.0, 0.0, 1.0));
    }
    ImGui::ProgressBar(fraction, ImVec2(-1, 18), overlay.c_str());
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
    for (int i = 1; i < argc; ++i) {
        if (std::string(argv[i]) == "--router-url") {
            if (++i >= argc || !localcodex::parse_router_port(argv[i], port)) return 2;
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
    localcodex::MonitorClient client(host, port, settings.refresh_ms, settings_path().parent_path() / "exports");
    localcodex::SystemMetricsSampler system_metrics;
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
    int graph_range = 0;
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
        system_metrics.set_visible(window_visible);
        auto state = client.state();
        const auto hardware = system_metrics.snapshot();
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
            ImGui::TextColored(
                ImVec4(.3f, .8f, 1, 1), "%s: %s",
                localcodex::i18n::tr("runtime.mode"), localized_mode(state.role)
            );
            ImGui::SameLine();
            ImGui::Text("%s: %s", localcodex::i18n::tr("runtime.phase_label"), localized_phase(state.phase));
            ImGui::SameLine();
            ImGui::Text("%s: %s", localcodex::i18n::tr("runtime.model"),
                state.source_model.empty() ? state.model.c_str() : state.source_model.c_str());
            ImGui::TextDisabled("%s  |  %s", state.model.c_str(), localized_route_reason(state.route_reason));
            const int metric_columns = ImGui::GetContentRegionAvail().x >= 800.0f ? 4 : 2;
            if (ImGui::BeginTable("cards", metric_columns, ImGuiTableFlags_SizingStretchSame)) {
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.input"), compact_number(state.session_input), localcodex::i18n::tr("hint.ollama_exact"));
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.output"), compact_number(state.session_output), state.active ? localcodex::i18n::tr("hint.live_estimate") : localcodex::i18n::tr("hint.ollama_exact"));
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.speed"), std::to_string(state.tokens_per_second).substr(0, 5), localcodex::i18n::tr("hint.tokens_second"));
                std::ostringstream saved; saved << '$' << std::fixed << std::setprecision(6) << state.session_saved_usd;
                ImGui::TableNextColumn(); metric_card(localcodex::i18n::tr("metric.saved"), saved.str(), localcodex::i18n::tr("hint.api_comparison"));
                ImGui::EndTable();
            }
            if (ImGui::BeginTable("total_cards", metric_columns, ImGuiTableFlags_SizingStretchSame)) {
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
            const auto dashboard_space = ImGui::GetContentRegionAvail();
            const bool side_by_side = dashboard_space.x >= 850.0f;
            const float panel_height = std::max(330.0f, dashboard_space.y);
            const float graph_width = side_by_side ? dashboard_space.x * .62f : dashboard_space.x;
            ImGui::BeginChild("throughput", ImVec2(graph_width, panel_height), ImGuiChildFlags_Borders);
            ImGui::TextUnformatted(localcodex::i18n::tr("throughput.title"));
            ImGui::SameLine();
            if (ImGui::RadioButton(localcodex::i18n::tr("graph.live"), graph_range == 0)) graph_range = 0;
            ImGui::SameLine();
            if (ImGui::RadioButton(localcodex::i18n::tr("graph.turn"), graph_range == 1)) graph_range = 1;
            ImGui::SameLine();
            if (ImGui::RadioButton(localcodex::i18n::tr("graph.session"), graph_range == 2)) graph_range = 2;
            const auto selected_range = graph_range == 0 ? localcodex::GraphRange::Live :
                graph_range == 1 ? localcodex::GraphRange::Turn : localcodex::GraphRange::Session;
            auto graph = client.graph_view(selected_range);
            ImGui::TextDisabled(
                localcodex::i18n::tr("graph.statistics"), graph.current, graph.average, graph.maximum
            );
            if (!graph.has_output && state.active) {
                ImGui::TextColored(
                    ImVec4(.95f, .72f, .25f, 1.0f), "%s",
                    localcodex::i18n::tr("graph.waiting")
                );
            }
            if (ImPlot::BeginPlot(
                "##throughput_plot", ImVec2(-1, -1),
                ImPlotFlags_NoLegend | ImPlotFlags_NoMenus
            )) {
                ImPlot::SetupAxes(
                    localcodex::i18n::tr("plot.seconds"), "Tokens/s",
                    ImPlotAxisFlags_None, ImPlotAxisFlags_None
                );
                ImPlot::SetupAxisLimits(ImAxis_X1, graph.x_min, graph.x_max, ImGuiCond_Always);
                ImPlot::SetupAxisLimits(ImAxis_Y1, 0.0, graph.y_max, ImGuiCond_Always);
                if (!graph.points.empty()) {
                    ImPlotSpec fill;
                    fill.FillColor = ImVec4(.05f, .55f, .95f, .22f);
                    fill.FillAlpha = .35f;
                    fill.Stride = sizeof(localcodex::GraphPoint);
                    ImPlot::PlotShaded(
                        "##throughput_fill", &graph.points.front().x, &graph.points.front().y,
                        static_cast<int>(graph.points.size()), 0.0, fill
                    );
                    ImPlotSpec line;
                    line.LineColor = ImVec4(.15f, .72f, 1.0f, 1.0f);
                    line.LineWeight = 2.5f;
                    line.Stride = sizeof(localcodex::GraphPoint);
                    ImPlot::PlotLine(
                        "Tokens/s", &graph.points.front().x, &graph.points.front().y,
                        static_cast<int>(graph.points.size()), line
                    );
                    const auto& current = graph.points.back();
                    ImPlotSpec marker;
                    marker.Marker = ImPlotMarker_Circle;
                    marker.MarkerSize = 5.0f;
                    marker.MarkerFillColor = ImVec4(.35f, .9f, 1.0f, 1.0f);
                    ImPlot::PlotScatter("##current", &current.x, &current.y, 1, marker);
                }
                ImPlot::EndPlot();
            }
            ImGui::EndChild();
            if (side_by_side) ImGui::SameLine();
            ImGui::BeginChild("runtime", ImVec2(0, panel_height), ImGuiChildFlags_Borders);
            ImGui::TextUnformatted(localcodex::i18n::tr("runtime.title"));
            ImGui::Separator();
            ImGui::Text("%s: %s", localcodex::i18n::tr("runtime.mode"), localized_mode(state.role));
            ImGui::Text("%s: %s", localcodex::i18n::tr("runtime.phase_label"), localized_phase(state.phase));
            ImGui::TextWrapped("%s: %s", localcodex::i18n::tr("runtime.model"),
                state.source_model.empty() ? state.model.c_str() : state.source_model.c_str());
            ImGui::TextDisabled("%s", state.model.c_str());
            if (!state.parameter_size.empty() || !state.quantization.empty()) {
                ImGui::TextDisabled("%s  %s", state.parameter_size.c_str(), state.quantization.c_str());
            }
            ImGui::Separator();
            resource_bar(localcodex::i18n::tr("hardware.cpu"), hardware.cpu_percent);
            resource_bar(localcodex::i18n::tr("hardware.gpu"), hardware.gpu_percent);
            resource_bar(
                localcodex::i18n::tr("hardware.ram"),
                localcodex::memory_percent(hardware.ram_used_bytes, hardware.ram_total_bytes),
                compact_bytes(hardware.ram_used_bytes) + " / " + compact_bytes(hardware.ram_total_bytes)
            );
            resource_bar(
                localcodex::i18n::tr("hardware.vram"),
                localcodex::memory_percent(hardware.vram_used_bytes, hardware.vram_total_bytes),
                compact_bytes(hardware.vram_used_bytes) + " / " + compact_bytes(hardware.vram_total_bytes)
            );
            if (!hardware.gpu_name.empty()) {
                ImGui::TextDisabled("%s (%s)", hardware.gpu_name.c_str(), hardware.gpu_source.c_str());
            }
            ImGui::Separator();
            const char* context_format = localcodex::i18n::tr("runtime.context");
            ImGui::Text(context_format, compact_number(state.context_length).c_str());
            ImGui::Text("Ollama: %s", state.ollama_version.c_str());
            ImGui::Text("TTFT: %.2f s", state.ttft_seconds);
            ImGui::Text(localcodex::i18n::tr("runtime.elapsed"), state.elapsed_seconds);
            ImGui::Text(localcodex::i18n::tr("runtime.turn_tokens"), compact_number(state.turn_input).c_str(), compact_number(state.turn_output).c_str());
            const auto model_ram = std::max<std::int64_t>(0, state.model_size_bytes - state.vram_size_bytes);
            ImGui::Text(
                localcodex::i18n::tr("runtime.model_memory"),
                compact_bytes(model_ram).c_str(), compact_bytes(state.vram_size_bytes).c_str(),
                compact_bytes(state.model_size_bytes).c_str()
            );
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
            ImGui::Separator();
            ImGui::TextWrapped("%s", localcodex::i18n::tr("license.notice"));
            ImGui::TextWrapped("https://github.com/Jochengehtab/LocalCodex");
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
