#include "i18n.h"

#include <cstdlib>
#include <string_view>
#include <unordered_map>

namespace localcodex::i18n {
namespace {
std::string current = [] {
    const char* requested = std::getenv("LOCAL_CODEX_LANGUAGE");
    if (!requested) requested = std::getenv("LANG");
    return requested && std::string_view(requested).starts_with("en") ? "en" : "de";
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
    {"hint.ollama_exact", {"exakt von Ollama", "exact from Ollama"}},
    {"hint.live_estimate", {"inkl. Live-Schätzung", "including live estimate"}},
    {"hint.tokens_second", {"Tokens/s", "tokens/s"}},
    {"hint.api_comparison", {"API-Vergleich", "API comparison"}},
    {"throughput.title", {"Live Throughput", "Live throughput"}},
    {"runtime.title", {"Ollama Runtime", "Ollama runtime"}},
    {"runtime.context", {"Kontext: %s", "Context: %s"}},
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
    {"settings.refresh", {"Live-Aktualisierung (ms)", "Live refresh (ms)"}},
    {"settings.description", {"Der Monitor liest ausschließlich lokale Router-Metriken. Er startet keine zweite Modellanfrage und speichert keine Denkzusammenfassungen.", "The monitor reads only local router metrics. It never starts a second model request and stores no reasoning summaries."}},
    {"export.failed", {"Export fehlgeschlagen", "Export failed"}},
    {"export.write_failed", {"Export konnte nicht geschrieben werden", "Export could not be written"}},
};
}  // namespace

void set_language(const std::string& value) {
    current = value.rfind("en", 0) == 0 ? "en" : "de";
}

const std::string& language() { return current; }

const char* tr(const char* key) {
    const auto found = messages.find(key);
    if (found == messages.end()) return key;
    return current == "en" ? found->second.second : found->second.first;
}
}  // namespace localcodex::i18n
