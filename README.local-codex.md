# Lokaler Codex mit Ollama

Dieses Projekt startet die echte Codex CLI mit einem ausschließlich lokal erreichbaren
Responses-Endpunkt. Ein Router wählt je Codex-Turn automatisch eines der vorhandenen
Ollama-Modelle aus und gleicht Protokollunterschiede aus.

## Schnellstart

Ollama muss laufen und die drei Ausgangsmodelle müssen installiert sein. Die aktuelle Maschine
ist bereits vollständig eingerichtet; im Alltag genügt:

```bash
codex-local
```

Vor dem TUI prüft der Launcher Ollama, alle drei Modell-Aliase, Router, Modellkatalog und die
tatsächlich von Codex geladene Konfiguration. Eine erfolgreiche Sitzung beginnt deshalb mit
`model=local-codex provider=local_router`. Zeigt das TUI stattdessen `gpt-*`, wurde normales
`codex` und nicht `codex-local` gestartet.

Das isolierte lokale Profil vertraut dem WSL-Home `/home/jochen` und dem Launcher-Projekt
das jeweilige Installations- und Benutzerverzeichnis, sodass ein Start aus `~` nicht in einer blockierenden Trust-Auswahl landet.
Für ein anderes Projekt kann Codex weiterhin einmalig dessen Verzeichnis-Vertrauen abfragen.

Weitere Codex-Argumente werden unverändert weitergereicht:

```bash
codex-local -C /pfad/zum/projekt
codex-local exec "Untersuche die Tests und behebe den Fehler."
codex-local -i screenshot.png "Analysiere diesen Fehler."
```

Modell-, Provider-, Profil- und `--oss`-Overrides werden absichtlich abgelehnt, damit
`codex-local` nie unbemerkt auf ein Cloud-Modell oder einen anderen Provider wechseln kann. Bei
einem fehlgeschlagenen Preflight fragt ein interaktives Terminal vor dem Fortsetzen; in Skripten
wird sicher abgebrochen. `--force` übergeht nur diese Prüfung, nicht die lokale Provider-Sperre.

Beim Start erscheint zusätzlich der moderne native **Local Codex Monitor** für Windows.
Das native C++-Dashboard auf Basis von Dear ImGui, SDL3 und ImPlot zeigt Modell und Phase,
Sitzungs-Input/-Output, Live-Tokens/s, TTFT, Throughput, Ollama-Laufzeitdaten und
Vergleichsersparnis. Die Tabs **Historie** und **Einstellungen** bieten Sitzungsfilter,
CSV-/JSON-Export sowie Systemstandard-, Dunkel- und Hell-Theme.

Livewerte mit `~` sind Schätzungen aus dem bereits laufenden Ollama-Responses-Stream. Nach dem
Turn werden Input und Output durch Ollamas exakte Usage-Werte ersetzt. Modell, Quantisierung,
VRAM und Kontext kommen direkt aus Ollamas `/api/ps`. Die aktuelle Responses-Schnittstelle
liefert keine `eval_duration`; deshalb wird die finale Geschwindigkeit aus exakten Tokens und
gemessener Streamzeit berechnet und entsprechend gekennzeichnet. Codex-interne Titelanfragen
werden weiterhin für die 24h-/Kostenstatistik gezählt, ersetzen aber nicht mehr den sichtbaren
Chat-Turn im Dashboard. Reasoning-Inhalte werden weder angefordert noch gespeichert oder
angezeigt. Es gibt keine zweite
Modellanfrage und keine Datenbankwrites pro Token.

Mehrere Codex-Fenster teilen sich genau einen Monitor und einen Router. Nach der letzten Sitzung
beenden sich beide nach 20 Sekunden. Bei Bedarf lässt sich nur die Oberfläche deaktivieren:

```bash
codex-local --no-monitor
```

Windows-Pfade funktionieren in WSL direkt. Die Launcher-Übersetzung macht beispielsweise aus
`C:\GitHub\FarmingGame` automatisch `/mnt/c/GitHub/FarmingGame`; alternativ kannst du den WSL-
Pfad direkt verwenden:

```bash
codex-local -C 'C:\GitHub\FarmingGame'
```

Standardmäßig arbeitet Codex mit `workspace-write` und fragt bei riskanten Aktionen nach
Bestätigung (`on-request`). Apps, Plugins, Updateprüfung, OpenAIs Websuche und Netzwerkzugriff aus
dem Shell-Sandbox sind deaktiviert. Aktuelle Informationen kommen stattdessen kostenlos über das
lokale MCP `local_search` und einen nur an `127.0.0.1:18082` gebundenen SearXNG-Container.

## Kostenlose Websuche

`codex-local` startet SearXNG bei Bedarf automatisch über Docker. Das Modell erhält zwei
Werkzeuge:

- `web_search`: Metasuche für aktuelle oder unsichere Informationen, maximal 10 Ergebnisse.
- `fetch_page`: sicherer Abruf und Textextraktion einer gefundenen öffentlichen Webseite.

Für eine explizite Suche genügt beispielsweise:

```text
Suche im Web nach den aktuellen Python-3.14-Release-Notes und nenne deine Quellen.
```

Die lokalen Anweisungen verlangen auch ohne diese Formulierung eine Suche, wenn Fakten aktuell,
veränderlich oder unsicher sind. Antworten sollen die verwendeten URLs zitieren. Suchen werden 15
Minuten und Seiten 30 Minuten in `.codex-local/state/web_cache.sqlite3` zwischengespeichert.

Der Seitenabruf erlaubt ausschließlich HTTP(S) auf Port 80/443, blockiert lokale, private,
reservierte und Link-Local-Ziele, prüft Redirects neu und begrenzt Download, Laufzeit und
Textausgabe. Webinhalte werden als nicht vertrauenswürdige Daten gekennzeichnet. PDFs und
JavaScript-only-Seiten werden in der ersten Version nicht extrahiert.

## Sprache

CLI- und Monitortexte unterstützen Deutsch und Englisch. Setze vor dem Start
`LOCAL_CODEX_LANGUAGE=de` oder `LOCAL_CODEX_LANGUAGE=en`; im nativen Monitor kann die Sprache
zusätzlich unter **Einstellungen → Sprache** geändert werden. Die Auswahl wird lokal gespeichert.

## Modellrouting

| Anfrage | Ollama-Alias | Ausgangsmodell |
| --- | --- | --- |
| Implementierung und normale Coding-Aufgaben | `local-codex-build:latest` | `qwen3.6:35b-a3b` |
| Planung, Review und Fehlerbehebung nach fehlgeschlagenem Tool | `local-codex-plan:latest` | `qwen3.8:27b` |
| Anfragen mit Bild | `local-codex-vision:latest` | `qwen3-vl:30b` |

Die Wahl bleibt innerhalb eines Codex-Turns stabil. Bei einem Toolfehler darf der Router auf das
Plan-/Debug-Modell wechseln. Vor einem Modellwechsel wird das vorherige Modell aus Ollama
entladen, damit die 12-GB-GPU und 24 GB RAM nicht mit mehreren Modellen gleichzeitig belastet
werden.

Alle neuen Threads starten standardmäßig mit `xhigh`. Bei Qwen wird dies zusätzlich als aktiviertes
Thinking an Ollama weitergegeben; die tatsächliche Tiefe bleibt modellabhängig und kann nicht exakt
mit OpenAIs Reasoning-Tokens gleichgesetzt werden.

## Einrichtung und erneuter Benchmark

Für eine Neuinstallation der Python-Abhängigkeiten:

```bash
cd /pfad/zu/LocalCodex
python3 -m venv .venv
./.venv/bin/pip install -r requirements-local-codex.txt
./.venv/bin/python start_codex.py --setup --benchmark
```

Ein schneller Neuaufbau ohne Benchmark behält das bisher gewählte Kontextfenster bei; bei einer
Erstinstallation ohne Messwerte werden 8K genutzt:

```bash
./.venv/bin/python start_codex.py --setup
```

Der Hardwarebenchmark prüft 8K, 16K, 32K, 64K und 128K. Für jedes Kontextprofil
werden Plan und Build zweimal auf einen korrekten Tool-Call geprüft; Vision muss zweimal die
Farben eines lokal erzeugten Testbildes erkennen. Das größte Profil wird nur gewählt, wenn alle Prüfungen
bestehen, mindestens 3 GiB RAM frei bleiben, der Swap-Anstieg höchstens 1 GiB beträgt und kein
Modell mehr als doppelt so lange wie bei 8K benötigt. Messwerte stehen in
`.codex-local/runtime.json`.

Qwen3.8, Qwen3.6 und Qwen3-VL unterstützen laut Modellkatalog nativ 256K. Das bedeutet
nicht automatisch, dass 128K auf jeder Hardware sinnvoll läuft: der KV-Cache wächst stark mit
dem Kontext. Ohne erfolgreichen Benchmark bleibt das zuletzt getestete Profil aktiv (bei deiner
aktuellen Installation 64K). Für einen gezielten Versuch kannst du nach dem Stoppen laufender
Codex-Sitzungen `./.venv/bin/python start_codex.py --setup --context 65536` oder `--context 131072`
ausführen; dabei werden die Ollama-Aliase mit dem neuen `num_ctx` neu erstellt.

## Architektur

- `start_codex.py` startet den Router, setzt ein isoliertes `CODEX_HOME` und startet die echte
  Codex CLI.
- `local_codex/app.py` stellt `/v1/models` und `/v1/responses` nur auf `127.0.0.1:18081` bereit.
  Weil Codex 0.151 MCP-Werkzeuge für neuere OpenAI-Modelle verzögert lädt, führt der Router die
  beiden lokalen Web-Tool-Calls für Qwen selbst aus und gibt erst die fertige Antwort zurück.
- `monitor` enthält die plattformübergreifende C++20-Singleton-App. Releases veröffentlichen
  sie nach `%LOCALAPPDATA%\LocalCodex` beziehungsweise ins Linux-Installationsverzeichnis; das UI pollt nur den flüchtigen
  `/monitor/snapshot`-Status mit maximal vier Anfragen pro Sekunde.
- `local_codex/monitor.py` koordiniert parallele Launcher über kurzlebige Heartbeat-Leases,
  damit nicht das zuerst geschlossene Codex-Fenster den gemeinsamen Router beendet.
- `local_codex/routing.py` übernimmt automatische, turn-stabile Modellwahl.
- `local_codex/protocol.py` übersetzt typische Qwen-Tool-Aliase wie `Bash` in Codex-Werkzeuge.
- `local_codex/state.py` bildet `previous_response_id` lokal über eine komprimierte SQLite-Historie
  ab, weil Ollama diesen Responses-Zustand nicht selbst fortsetzt.
- `local_search/mcp_server.py` stellt `web_search` und `fetch_page` als lokale MCP-Werkzeuge bereit.
- `local_search/core.py` übernimmt Suchzugriff, Text-Extraktion, Größenlimits und SSRF-Schutz.
- `local_search/docker-compose.yml` startet die fest angeheftete SearXNG-Version nur auf Loopback.
- `.codex-local/config.toml` ist vollständig vom normalen Codex-Profil getrennt.

## Diagnose

Schnellen vollständigen Preflight ohne Modellgenerierung ausführen:

```bash
codex-local --doctor
codex-local --doctor --json
```

Eine echte ephemere Codex-Inferenz inklusive Sentinel-Antwort, Ollama-Tokens, geladenem Modell
und Router-/Monitor-Shutdown prüfen:

```bash
codex-local --self-test
codex-local --self-test --json
```

Der Self-Test darf bis zu 180 Sekunden für die Inferenz warten. Er schreibt keinen dauerhaften
Codex-Thread, die von Ollama tatsächlich verbrauchten Tokens erscheinen aber wie jeder reale
Modellaufruf in der Nutzungsstatistik.

Router separat starten:

```bash
./.venv/bin/python -m local_codex.app
curl http://127.0.0.1:18081/health
```

Websuche, Docker, Such-API, SSRF-Schutz und MCP-Registrierung prüfen:

```bash
codex-local --search-doctor
```

Monitorinstallation, native EXE, Router und Windows→WSL-Verbindung prüfen:

```bash
codex-local --monitor-doctor
```

Monitor-Einstellungen und Exporte liegen unter dem plattformspezifischen SDL-Anwendungsverzeichnis;
das Router-Log liegt unter `.codex-local/state/router.log`.

Tokenverbrauch und die geschätzte Tokenkosten-Ersparnis gegenüber GPT-5.6 Luna anzeigen:

```bash
codex-local --usage       # letzte 30 Tage
codex-local --usage --all # gesamte lokale Statistik
codex-local --usage-json  # maschinenlesbares JSON
```

Die Anzeige basiert auf den vom Ollama-Responses-Endpunkt gelieferten Usage-Werten. Der
API-Vergleich nutzt standardmäßig den konfigurierten GPT-5.6-Luna-Tarif; lokale Inferenz kostet
keine API-Gebühr, aber Strom, Hardware und eventuelle Suchanbieter-Limits sind nicht enthalten.
Für einen anderen Vergleichstarif kannst du beim Start beispielsweise
`LOCAL_CODEX_COMPARISON_MODEL=gpt-5.6-sol codex-local` verwenden. Unterstützt werden
`gpt-5.6-luna`, `gpt-5.6-terra` und `gpt-5.6-sol`; bereits gespeicherte Turns behalten ihren
damaligen Vergleichstarif.

SearXNG manuell verwalten:

```bash
docker compose --env-file .codex-local/search.env -f local_search/docker-compose.yml ps
docker compose --env-file .codex-local/search.env -f local_search/docker-compose.yml logs
docker compose --env-file .codex-local/search.env -f local_search/docker-compose.yml down
```

Tests ausführen:

```bash
./.venv/bin/python -m unittest discover -s tests -v
```

Die echten GPU-/TUI-Integrationstests sind standardmäßig übersprungen und werden explizit
aktiviert:

```bash
LOCAL_CODEX_LIVE_TESTS=1 ./.venv/bin/python -m unittest tests.test_live_codex -v
```

Die Router-Ausgabe zeigt für jeden Turn das gewählte Modell und den Routinggrund.

## Bewusste Grenzen

Das Ergebnis bildet den Codex-Arbeitsablauf lokal nach: CLI, Sandbox, Freigaben, Tool-Loops,
Dateiänderungen, Bildübergabe und Sitzungsfortsetzung funktionieren. Die Modellqualität kann nicht
identisch zu OpenAIs Codex-Modellen sein. Der Router beobachtet Ollamas Stream live für den
Monitor, gibt ihn wegen der lokalen Web-Tool-Schleife aber erst als vollständig kompatible
Responses-Antwort an Codex weiter. Wegen der Hardware wird Inferenz absichtlich serialisiert. Websuche reduziert
den Wissensstichtag, beseitigt ihn aber nicht: Treffer können unvollständig, veraltet oder falsch
sein und wichtige Aussagen sollten über mehrere Primärquellen geprüft werden.
