param(
    [string]$Version = "",
    [switch]$Yes,
    [switch]$SkipModels,
    [switch]$SkipSearch,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$Repository = "Jochengehtab/LocalCodex"

if ($DryRun) {
    if ([string]::IsNullOrWhiteSpace($Version)) { $Version = "0.0.0-dry-run" }
    Write-Host "[dry-run] Würde LocalCodex $Version mit WSL2-Backend und nativer Windows-UI installieren."
    exit 0
}

function Confirm-Step([string]$Message) {
    if ($Yes) { return $true }
    return (Read-Host "$Message [j/N]") -match '^(j|ja|y|yes)$'
}

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    if (-not (Confirm-Step "WSL2 mit Ubuntu automatisch installieren?")) { throw "WSL2 wird benötigt." }
    wsl.exe --install -d Ubuntu
    Write-Host "Windows muss gegebenenfalls neu gestartet werden. Führe denselben Installer danach erneut aus; er setzt idempotent fort."
    exit 3010
}
wsl.exe -d Ubuntu -e true 2>$null
if ($LASTEXITCODE -ne 0) {
    if (-not (Confirm-Step "Die WSL2-Distribution Ubuntu automatisch installieren?")) { throw "Ubuntu unter WSL2 wird benötigt." }
    wsl.exe --install -d Ubuntu
    Write-Host "Führe denselben Installer nach Abschluss beziehungsweise einem Neustart erneut aus."
    exit 3010
}
if ([string]::IsNullOrWhiteSpace($Version)) {
    $release = Invoke-RestMethod "https://api.github.com/repos/$Repository/releases/latest"
    $Version = $release.tag_name.TrimStart('v')
}
$Base = "https://github.com/$Repository/releases/download/v$Version"
$Temp = Join-Path ([IO.Path]::GetTempPath()) ("LocalCodex-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $Temp | Out-Null
try {
    $Core = Join-Path $Temp "core.tar.gz"
    $Monitor = Join-Path $Temp "monitor.zip"
    $Sums = Join-Path $Temp "SHA256SUMS"
    Invoke-WebRequest "$Base/localcodex-core-v$Version.tar.gz" -OutFile $Core
    Invoke-WebRequest "$Base/localcodex-monitor-windows-x64-v$Version.zip" -OutFile $Monitor
    Invoke-WebRequest "$Base/SHA256SUMS" -OutFile $Sums
    $sumText = Get-Content $Sums -Raw
    foreach ($entry in @(
        @{Path=$Core; Name="localcodex-core-v$Version.tar.gz"},
        @{Path=$Monitor; Name="localcodex-monitor-windows-x64-v$Version.zip"}
    )) {
        $expected = ([regex]::Match($sumText, "(?m)^([a-f0-9]{64})\s+\*?$([regex]::Escape($entry.Name))$")).Groups[1].Value
        $actual = (Get-FileHash $entry.Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($expected) -or $actual -ne $expected) { throw "Checksum für $($entry.Name) ungültig." }
    }

    $CoreDir = Join-Path $Temp "core"
    New-Item -ItemType Directory -Path $CoreDir | Out-Null
    tar.exe -xzf $Core -C $CoreDir
    $MonitorDir = Join-Path $env:LOCALAPPDATA "LocalCodex\versions\v$Version"
    New-Item -ItemType Directory -Force -Path $MonitorDir | Out-Null
    Expand-Archive -Force $Monitor $MonitorDir
    $MonitorExe = Join-Path $MonitorDir "localcodex-monitor.exe"
    if (-not (Test-Path $MonitorExe)) { throw "Monitor-EXE fehlt im Release-Archiv." }

    $CoreWsl = (wsl.exe wslpath -a -u $CoreDir).Trim()
    $MonitorWsl = (wsl.exe wslpath -a -u $MonitorExe).Trim()
    $InstallArgs = @("--from-payload", "--version", $Version)
    if ($Yes) { $InstallArgs += "--yes" }
    if ($SkipModels) { $InstallArgs += "--skip-models" }
    if ($SkipSearch) { $InstallArgs += "--skip-search" }
    wsl.exe env "LOCAL_CODEX_MONITOR_EXE=$MonitorWsl" bash "$CoreWsl/install.sh" $InstallArgs
    if ($LASTEXITCODE -ne 0) { throw "WSL-Installation ist mit Exitcode $LASTEXITCODE fehlgeschlagen." }
    Write-Host "LocalCodex $Version wurde für WSL2 und Windows installiert."
} finally {
    Remove-Item -Recurse -Force $Temp -ErrorAction SilentlyContinue
}
