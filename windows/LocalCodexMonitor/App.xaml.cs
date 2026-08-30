using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Threading;
using System.Windows;
using System.Windows.Media;
using Microsoft.Win32;

namespace LocalCodexMonitor;

public partial class App : System.Windows.Application
{
    internal const string ShutdownEventName = @"Local\LocalCodexMonitorShutdown";
    private Mutex? _mutex;
    private EventWaitHandle? _shutdownEvent;
    private RegisteredWaitHandle? _shutdownRegistration;
    private static readonly string SettingsDirectory = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LocalCodexMonitor");
    private static readonly string SettingsPath = Path.Combine(SettingsDirectory, "settings.json");
    public string ThemePreference { get; private set; } = "system";

    protected override async void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);
        string routerUrl = ArgumentValue(e.Args, "--router-url") ?? "http://127.0.0.1:18081";
        string? diagnosticFile = ArgumentValue(e.Args, "--diagnostic-file");
        if (diagnosticFile is not null)
        {
            int code = await RunDiagnosticAsync(routerUrl, diagnosticFile);
            Shutdown(code);
            return;
        }

        _mutex = new Mutex(true, @"Local\LocalCodexMonitor", out bool createdNew);
        if (!createdNew)
        {
            Shutdown(0);
            return;
        }

        try
        {
            _shutdownEvent = new EventWaitHandle(false, EventResetMode.AutoReset, ShutdownEventName);
            _shutdownRegistration = ThreadPool.RegisterWaitForSingleObject(
                _shutdownEvent,
                (_, _) => Dispatcher.BeginInvoke(ShutdownMonitor),
                null,
                Timeout.Infinite,
                executeOnlyOnce: true);
        }
        catch { }

        ThemePreference = LoadThemePreference();
        ApplyTheme(ThemePreference);
        var window = new MainWindow(routerUrl);
        MainWindow = window;
        window.Show();
    }

    protected override void OnExit(ExitEventArgs e)
    {
        try { _shutdownRegistration?.Unregister(null); } catch { }
        _shutdownEvent?.Dispose();
        try { _mutex?.ReleaseMutex(); } catch (ApplicationException) { }
        _mutex?.Dispose();
        base.OnExit(e);
    }

    private void ShutdownMonitor()
    {
        if (Current.MainWindow is MainWindow window)
            window.ExitFromSignal();
        else
            Shutdown(0);
    }

    private static string? ArgumentValue(string[] args, string name)
    {
        int index = Array.IndexOf(args, name);
        return index >= 0 && index + 1 < args.Length ? args[index + 1] : null;
    }

    private static async Task<int> RunDiagnosticAsync(string routerUrl, string target)
    {
        object report;
        int exitCode;
        try
        {
            using var client = new HttpClient { Timeout = TimeSpan.FromSeconds(4) };
            string health = await client.GetStringAsync($"{routerUrl}/health");
            string snapshot = await client.GetStringAsync($"{routerUrl}/monitor/snapshot");
            report = new { ok = true, router_url = routerUrl, health = JsonDocument.Parse(health).RootElement, snapshot = JsonDocument.Parse(snapshot).RootElement };
            exitCode = 0;
        }
        catch (Exception ex)
        {
            report = new { ok = false, router_url = routerUrl, error = ex.Message, exception = ex.GetType().Name };
            exitCode = 1;
        }
        Directory.CreateDirectory(Path.GetDirectoryName(target) ?? ".");
        await File.WriteAllTextAsync(target, JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
        return exitCode;
    }

    public void SetThemePreference(string value)
    {
        value = value is "dark" or "light" or "system" ? value : "system";
        ThemePreference = value;
        ApplyTheme(value);
        try
        {
            Directory.CreateDirectory(SettingsDirectory);
            File.WriteAllText(SettingsPath, JsonSerializer.Serialize(new { theme = value }));
        }
        catch { }
    }

    private static string LoadThemePreference()
    {
        try
        {
            using JsonDocument document = JsonDocument.Parse(File.ReadAllText(SettingsPath));
            string? value = document.RootElement.GetProperty("theme").GetString();
            return value is "dark" or "light" or "system" ? value : "system";
        }
        catch { return "system"; }
    }

    public void ApplyTheme(string preference)
    {
        bool light = preference == "light";
        if (preference == "system")
        {
            try
            {
                using RegistryKey? personalize = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
                light = Convert.ToInt32(personalize?.GetValue("AppsUseLightTheme", 0)) != 0;
            }
            catch { light = false; }
        }
        try
        {
            if (light)
            {
                Resources["WindowBrush"] = Brush("#F4F7FB");
                Resources["CardBrush"] = Brush("#FFFFFF");
                Resources["CardHoverBrush"] = Brush("#EDF2F8");
                Resources["TextBrush"] = Brush("#182033");
                Resources["MutedBrush"] = Brush("#617087");
                Resources["ChipBrush"] = Brush("#E4F4FA");
                Resources["BorderBrush"] = Brush("#D6DFEA");
            }
            else
            {
                Resources["WindowBrush"] = Brush("#0B0F17");
                Resources["CardBrush"] = Brush("#151B27");
                Resources["CardHoverBrush"] = Brush("#1B2332");
                Resources["TextBrush"] = Brush("#F4F7FB");
                Resources["MutedBrush"] = Brush("#8D9AAF");
                Resources["ChipBrush"] = Brush("#102E3B");
                Resources["BorderBrush"] = Brush("#253044");
            }
            using RegistryKey? dwm = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\DWM");
            if (dwm?.GetValue("ColorizationColor") is int raw)
            {
                uint color = unchecked((uint)raw);
                byte red = (byte)(color >> 16);
                byte green = (byte)(color >> 8);
                byte blue = (byte)color;
                Resources["AccentBrush"] = new SolidColorBrush(System.Windows.Media.Color.FromRgb(red, green, blue));
            }
        }
        catch { }
    }

    private static SolidColorBrush Brush(string value) =>
        new((System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(value));
}
