using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.Json.Nodes;
using System.Windows;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Media.Animation;
using System.Windows.Threading;
using WinForms = System.Windows.Forms;

namespace LocalCodexMonitor;

public partial class MainWindow : Window
{
    private readonly string _routerUrl;
    private readonly HttpClient _client = new() { Timeout = TimeSpan.FromSeconds(2) };
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromMilliseconds(250) };
    private readonly List<double> _speedSamples = [];
    private readonly string _dataDirectory;
    private readonly string _logPath;
    private readonly string _readyPath;
    private readonly WinForms.NotifyIcon _tray;
    private bool _polling;
    private bool _allowClose;
    private DateTime _lastHistory = DateTime.MinValue;
    private DateTime _lastReady = DateTime.MinValue;
    private DateTime _lastErrorLog = DateTime.MinValue;
    private DateTime? _offlineSince;
    private DateTime? _noSessionsSince;
    private bool _initializingTheme;

    public MainWindow(string routerUrl)
    {
        InitializeComponent();
        _routerUrl = routerUrl.TrimEnd('/');
        _dataDirectory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalCodexMonitor");
        Directory.CreateDirectory(Path.Combine(_dataDirectory, "logs"));
        _logPath = Path.Combine(_dataDirectory, "logs", "monitor.log");
        _readyPath = Path.Combine(_dataDirectory, "monitor.ready.json");

        _tray = new WinForms.NotifyIcon
        {
            Icon = SystemIcons.Application,
            Visible = true,
            Text = "Local Codex Monitor"
        };
        var menu = new WinForms.ContextMenuStrip();
        menu.Items.Add("Dashboard anzeigen", null, (_, _) => Dispatcher.Invoke(ShowDashboard));
        menu.Items.Add("Log öffnen", null, (_, _) => OpenLog());
        menu.Items.Add(new WinForms.ToolStripSeparator());
        menu.Items.Add("Monitor beenden", null, (_, _) => Dispatcher.Invoke(ExitMonitor));
        _tray.ContextMenuStrip = menu;
        _tray.DoubleClick += (_, _) => Dispatcher.Invoke(ShowDashboard);

        Loaded += (_, _) =>
        {
            EnableBackdrop();
            RouterUrlText.Text = _routerUrl;
            _initializingTheme = true;
            ThemeComboBox.SelectedIndex = ((App)System.Windows.Application.Current).ThemePreference switch
            {
                "dark" => 1,
                "light" => 2,
                _ => 0,
            };
            _initializingTheme = false;
            ThemeStatusText.Text = ThemeLabel(((App)System.Windows.Application.Current).ThemePreference);
            _timer.Start();
        };
        Closing += OnClosing;
        SparkCanvas.SizeChanged += (_, _) => RenderSparkline();
        _timer.Tick += async (_, _) => await PollAsync();
        Log("Monitor started");
    }

    private async Task PollAsync()
    {
        if (_polling) return;
        _polling = true;
        try
        {
            string json = await _client.GetStringAsync($"{_routerUrl}/monitor/snapshot");
            JsonNode root = JsonNode.Parse(json) ?? throw new InvalidDataException("Empty snapshot");
            UpdateSnapshot(root);
            _offlineSince = null;
            OfflineBanner.Visibility = Visibility.Collapsed;
            if (DateTime.UtcNow - _lastReady >= TimeSpan.FromSeconds(2))
            {
                _lastReady = DateTime.UtcNow;
                await File.WriteAllTextAsync(_readyPath, $"{{\"updated_at\":\"{DateTime.UtcNow:O}\",\"router\":\"{_routerUrl}\"}}");
            }
            if (DateTime.UtcNow - _lastHistory >= TimeSpan.FromSeconds(10))
            {
                _lastHistory = DateTime.UtcNow;
                await UpdateHistoryAsync();
            }
        }
        catch (Exception ex)
        {
            _offlineSince ??= DateTime.UtcNow;
            OfflineBanner.Visibility = Visibility.Visible;
            OfflineText.Text = "Router nicht erreichbar – automatischer Reconnect läuft.";
            StatusText.Text = "Local Codex offline";
            StatusDot.Fill = new SolidColorBrush(ColorFrom("#D94C5C"));
            if ((DateTime.UtcNow - _offlineSince.Value) > TimeSpan.FromSeconds(30)) ExitMonitor();
            if (DateTime.UtcNow - _lastErrorLog >= TimeSpan.FromSeconds(5))
            {
                _lastErrorLog = DateTime.UtcNow;
                Log($"Poll failed: {ex.GetType().Name}: {ex.Message}");
            }
        }
        finally { _polling = false; }
    }

    private void UpdateSnapshot(JsonNode root)
    {
        JsonNode? turn = root["turn"];
        JsonNode? tokens = root["tokens"];
        JsonNode? performance = root["performance"];
        JsonNode? runtime = root["ollama_runtime"];
        JsonNode? cost = root["cost"];
        string phase = String(turn?["phase"]) ?? String(root["phase"]) ?? "idle";
        bool active = Bool(turn?["active"]);
        string model = String(turn?["model"]) ?? String(root["model"]) ?? "Kein Modell aktiv";
        int sessions = Int(root["sessions"]?["active_count"]);

        StatusText.Text = PhaseLabel(phase);
        ModelText.Text = $"{FriendlyModel(model)}  •  {sessions} lokale Sitzung{(sessions == 1 ? "" : "en")}";
        StatusDot.Fill = new SolidColorBrush(ColorFrom(phase == "error" ? "#D94C5C" : active ? "#56E39F" : "#62D4FF"));
        _tray.Text = TrimTray($"Local Codex • {PhaseLabel(phase)}");

        ElapsedText.Text = TimeSpan.FromSeconds(Double(turn?["elapsed_seconds"])).ToString(@"hh\:mm\:ss");
        int input = NullableInt(tokens?["input"]);
        int output = NullableInt(tokens?["output"]);
        int estimated = Int(tokens?["estimated_output"]);
        bool exact = Bool(tokens?["exact"]);
        InputText.Text = input >= 0 ? Count(input) : "-";
        OutputText.Text = exact && output >= 0 ? Count(output) : $"~{Count(estimated)}";
        OutputSourceText.Text = exact ? "exakt • Ollama Usage" : "live • Ollama Stream";

        double speed = NullableDouble(performance?["tokens_per_second"]);
        bool speedEstimated = Bool(performance?["estimated"]);
        SpeedText.Text = speed >= 0 ? $"{(speedEstimated ? "~" : "")}{speed:0.0}" : "-";
        SpeedSourceText.Text = speedEstimated ? "live geschätzt" : SourceLabel(String(performance?["source"]));
        double ttft = NullableDouble(performance?["time_to_first_token_seconds"]);
        TtftText.Text = ttft >= 0 ? $"TTFT: {ttft:0.00} s" : "TTFT: -";
        if (speed >= 0) AddSpeedSample(speed); else AddSpeedSample(0);

        double turnCost = NullableDouble(cost?["comparison_usd"]);
        CostText.Text = turnCost >= 0 ? $"${turnCost:0.000000}" : "-";
        CostModelText.Text = $"gegen {String(cost?["comparison_model"]) ?? "API"}";

        string thinking = String(turn?["thinking_summary"]) ?? "";
        ThinkingText.Text = string.IsNullOrWhiteSpace(thinking) ? "Noch keine Zusammenfassung für diesen Turn." : thinking;
        if (!string.IsNullOrWhiteSpace(String(turn?["error"]))) ThinkingText.Text = $"Fehler: {String(turn?["error"])}";

        RuntimeModelText.Text = FriendlyModel(String(runtime?["name"]) ?? "Kein Modell geladen");
        long vram = Long(runtime?["size_vram_bytes"]);
        long size = Long(runtime?["size_bytes"]);
        string parameters = String(runtime?["parameter_size"]) ?? "-";
        string quant = String(runtime?["quantization_level"]) ?? "-";
        RuntimeDetailsText.Text = $"{parameters} Parameter  •  {quant}\nVRAM {Bytes(vram)}  •  Modell {Bytes(size)}\nOllama {String(root["ollama_version"]) ?? "-"}";
        int context = Int(runtime?["context_length"]);
        ContextText.Text = context > 0 ? $"Kontext {Count(context)} Tokens" : "Kontext -";

        if (sessions == 0)
        {
            _noSessionsSince ??= DateTime.UtcNow;
            if (DateTime.UtcNow - _noSessionsSince.Value > TimeSpan.FromSeconds(20)) ExitMonitor();
        }
        else _noSessionsSince = null;
    }

    private async Task UpdateHistoryAsync()
    {
        try
        {
            JsonNode root = JsonNode.Parse(await _client.GetStringAsync($"{_routerUrl}/monitor/history"))!;
            int total = Int(root["total_tokens"]);
            double saved = Double(root["estimated_saved_usd"]);
            TodayText.Text = $"24h: {Count(total)} Tokens  •  ${saved:0.000000} gespart";
        }
        catch (Exception ex) { Log($"History failed: {ex.Message}"); }
    }

    private void AddSpeedSample(double value)
    {
        _speedSamples.Add(value);
        if (_speedSamples.Count > 240) _speedSamples.RemoveAt(0);
        RenderSparkline();
    }

    private void RenderSparkline()
    {
        double width = SparkCanvas.ActualWidth;
        double height = SparkCanvas.ActualHeight;
        if (width <= 0 || height <= 0 || _speedSamples.Count < 2) return;
        double maximum = Math.Max(_speedSamples.Max(), 1);
        var points = new PointCollection();
        for (int index = 0; index < _speedSamples.Count; index++)
        {
            double x = index * width / Math.Max(_speedSamples.Count - 1, 1);
            double y = height - (_speedSamples[index] / maximum * (height - 8)) - 4;
            points.Add(new System.Windows.Point(x, y));
        }
        SpeedLine.Points = points;
    }

    private void OnClosing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        if (_allowClose) return;
        e.Cancel = true;
        Hide();
        _tray.ShowBalloonTip(1500, "Local Codex Monitor", "Der Monitor läuft im Infobereich weiter.", WinForms.ToolTipIcon.Info);
    }

    private void ShowDashboard()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private void ExitMonitor()
    {
        if (_allowClose) return;
        _allowClose = true;
        _timer.Stop();
        _tray.Visible = false;
        _tray.Dispose();
        _client.Dispose();
        try { if (File.Exists(_readyPath)) File.Delete(_readyPath); } catch { }
        Close();
    }

    private void OpenLog()
    {
        try { System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(_logPath) { UseShellExecute = true }); }
        catch { }
    }

    private void ThemeComboBox_SelectionChanged(object sender, System.Windows.Controls.SelectionChangedEventArgs e)
    {
        if (_initializingTheme || ThemeComboBox.SelectedItem is not System.Windows.Controls.ComboBoxItem item) return;
        string value = item.Tag as string ?? "system";
        ((App)System.Windows.Application.Current).SetThemePreference(value);
        ThemeStatusText.Text = $"Aktiv: {ThemeLabel(value)}";
    }

    private async void RefreshButton_Click(object sender, RoutedEventArgs e)
    {
        _lastHistory = DateTime.MinValue;
        await PollAsync();
    }

    private void OpenLogButton_Click(object sender, RoutedEventArgs e) => OpenLog();

    private void ExitButton_Click(object sender, RoutedEventArgs e) => ExitMonitor();

    internal void ExitFromSignal() => ExitMonitor();

    private void Log(string message)
    {
        try { File.AppendAllText(_logPath, $"{DateTime.Now:O} {message}{Environment.NewLine}"); } catch { }
    }

    private void EnableBackdrop()
    {
        try
        {
            IntPtr handle = new WindowInteropHelper(this).Handle;
            int dark = 1;
            int backdrop = 2;
            DwmSetWindowAttribute(handle, 20, ref dark, sizeof(int));
            DwmSetWindowAttribute(handle, 38, ref backdrop, sizeof(int));
        }
        catch { }
    }

    [DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int size);

    private static string PhaseLabel(string phase) => phase switch
    {
        "loading" => "Modell wird geladen", "thinking" => "Qwen denkt nach", "generating" => "Antwort wird erzeugt",
        "tool" => "Lokales Werkzeug läuft", "completed" => "Turn abgeschlossen", "error" => "Fehler", _ => "Bereit"
    };
    private static string FriendlyModel(string model) => model switch
    {
        var value when value.Contains("plan") => "Qwen 3.8 • Plan",
        var value when value.Contains("build") => "Qwen 3.6 • Build",
        var value when value.Contains("vision") => "Qwen VL • Vision",
        _ => model
    };
    private static string SourceLabel(string? source) => source == "ollama.eval_duration" ? "exakt • Ollama eval_duration" : "Ollama Usage + Streamzeit";
    private static string ThemeLabel(string value) => value switch
    {
        "dark" => "Dunkel",
        "light" => "Hell",
        _ => "Systemstandard",
    };
    private static string Count(long value) => value.ToString("N0", CultureInfo.GetCultureInfo("de-DE"));
    private static string Bytes(long value) => value <= 0 ? "-" : $"{value / 1024d / 1024d / 1024d:0.0} GiB";
    private static string TrimTray(string value) => value.Length <= 63 ? value : value[..63];
    private static System.Windows.Media.Color ColorFrom(string value) => (System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(value);
    private static string? String(JsonNode? node) => node?.GetValue<string>();
    private static bool Bool(JsonNode? node) => node?.GetValue<bool>() ?? false;
    private static int Int(JsonNode? node) => NullableInt(node) is int value && value >= 0 ? value : 0;
    private static int NullableInt(JsonNode? node) { try { return node is null ? -1 : node.GetValue<int>(); } catch { return -1; } }
    private static long Long(JsonNode? node) { try { return node?.GetValue<long>() ?? 0; } catch { return 0; } }
    private static double Double(JsonNode? node) => NullableDouble(node) is double value && value >= 0 ? value : 0;
    private static double NullableDouble(JsonNode? node) { try { return node is null ? -1 : node.GetValue<double>(); } catch { return -1; } }
}
