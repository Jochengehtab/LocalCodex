#include "i18n.h"

#include <SDL3/SDL_stdinc.h>
#include <string_view>
#include <unordered_map>

namespace localcodex::i18n {
namespace {
std::string current = [] {
    const char* requested = SDL_getenv("LOCAL_CODEX_LANGUAGE");
    return requested && std::string_view(requested).starts_with("de") ? "de" : "en";
}();

const std::unordered_map<std::string_view, std::pair<const char*, const char*>> messages{
    {"monitor.title", {"Local Codex Monitor", "Local Codex Monitor"}},
    {"monitor.show", {"Monitor anzeigen", "Show monitor"}},
    {"monitor.quit", {"Beenden", "Quit"}},
    {"router.online", {"Router online", "Router online"}},
    {"router.offline", {"Router offline", "Router offline"}},
    {"tab.dashboard", {"Dashboard", "Dashboard"}},
    {"tab.history", {"Historie", "History"}},
    {"tab.settings", {"Einstellungen", "Settings"}},
    {"metric.input", {"SITZUNG INPUT", "SESSION INPUT"}},
    {"metric.output", {"SITZUNG OUTPUT", "SESSION OUTPUT"}},
    {"metric.speed", {"GESCHWINDIGKEIT", "SPEED"}},
    {"metric.saved", {"GESPART / SITZUNG", "SAVED / SESSION"}},
    {"metric.total_input", {"GESAMT INPUT", "ALL-TIME INPUT"}},
    {"metric.total_output", {"GESAMT OUTPUT", "ALL-TIME OUTPUT"}},
    {"metric.total_saved", {"GESAMT GESPART", "ALL-TIME SAVED"}},
    {"metric.context", {"KONTEXT", "CONTEXT"}},
    {"hint.ollama_exact", {"exakt von Ollama", "exact from Ollama"}},
    {"hint.live_estimate", {"inkl. Live-Schätzung", "including live estimate"}},
    {"hint.tokens_second", {"Tokens/s", "tokens/s"}},
    {"hint.api_comparison", {"API-Vergleich", "API comparison"}},
    {"hint.usage_exact", {"Exakte Ollama-Nutzung", "Exact Ollama usage"}},
    {"hint.usage_live", {"Exakte Nutzung + live", "Exact usage + live"}},
    {"throughput.title", {"Live Throughput", "Live throughput"}},
    {"runtime.title", {"Ollama Runtime", "Ollama runtime"}},
    {"runtime.context", {"Kontext: %s", "Context: %s"}},
    {"runtime.elapsed", {"Laufzeit: %.1f s", "Elapsed: %.1f s"}},
    {"plot.seconds", {"Sitzungssekunden", "Session seconds"}},
    {"runtime.memory", {"RAM / VRAM: %s / %s", "RAM / VRAM: %s / %s"}},
    {"runtime.phase", {"Phase: %s", "Phase: %s"}},
    {"runtime.tool", {"Letztes Werkzeug: %s", "Last tool: %s"}},
    {"runtime.launchers", {"Aktive Sitzungen: %lld", "Active launchers: %lld"}},
    {"runtime.turn_tokens", {"Turn: %s Input / %s Output", "Turn: %s input / %s output"}},
    {"history.all_sessions", {"Alle Sitzungen", "All sessions"}},
    {"history.csv", {"CSV exportieren", "Export CSV"}},
    {"history.json", {"JSON exportieren", "Export JSON"}},
    {"history.range", {"Zeitraum: %s Input / %s Output / $%.6f gespart", "Range: %s input / %s output / $%.6f saved"}},
    {"history.session", {"Sitzung", "Session"}},
    {"history.turns", {"Turns", "Turns"}},
    {"history.input", {"Input", "Input"}},
    {"history.output", {"Output", "Output"}},
    {"history.saved", {"Gespart", "Saved"}},
    {"history.filter", {"Filter", "Filter"}},
    {"history.select", {"Auswählen", "Select"}},
    {"settings.appearance", {"Darstellung", "Appearance"}},
    {"settings.theme", {"Theme", "Theme"}},
    {"settings.system", {"System", "System"}},
    {"settings.dark", {"Dunkel", "Dark"}},
    {"settings.light", {"Hell", "Light"}},
    {"settings.language", {"Sprache", "Language"}},
    {"settings.german", {"Deutsch", "German"}},
    {"settings.english", {"Englisch", "English"}},
    {"settings.refresh", {"Fallback-Aktualisierung (ms)", "Fallback refresh (ms)"}},
    {"settings.description", {"Der Monitor liest ausschließlich lokale Router-Metriken. Er startet keine zweite Modellanfrage und speichert keine Denkzusammenfassungen.", "The monitor reads only local router metrics. It never starts a second model request and stores no reasoning summaries."}},
    {"export.failed", {"Export fehlgeschlagen", "Export failed"}},
    {"export.write_failed", {"Export konnte nicht geschrieben werden", "Export could not be written"}},
};
}  // namespace

void set_language(const std::string& value) {
    current = value.rfind("de", 0) == 0 ? "de" : "en";
}

const std::string& language() { return current; }

const char* tr(const char* key) {
    const auto found = messages.find(key);
    if (found == messages.end()) return key;
    return current == "en" ? found->second.second : found->second.first;
}
}  // namespace localcodex::i18n
