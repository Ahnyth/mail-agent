# MailAgent - Einrichtung auf einem Windows-PC. Kann jederzeit erneut laufen (auch zum Aktualisieren).
# Vorher:  git clone https://github.com/<github-benutzer>/mail-agent.git "$env:USERPROFILE\mail-agent"
# Aufruf:  powershell -ExecutionPolicy Bypass -File "$env:USERPROFILE\mail-agent\scripts\einrichten.ps1"
#
# Installiert fehlende Programme (Git, uv, Claude Code), holt den aktuellen Stand von GitHub, richtet
# Python ein, legt die lokale Konfiguration an, fragt Postfachzugang und Claude-Anmeldung ab, führt die
# Tests aus, legt eine Desktop-Verknüpfung und den Autostart an und startet den Agent im Hintergrund. Das
# Postfach-Passwort fragt nur Windows (cmdkey) ab; das Skript sieht, speichert und zeigt es nie.

$ErrorActionPreference = 'Continue'
$Projekt = Split-Path -Parent $PSScriptRoot
# An das eigene Postfach anpassen (wie credential_target und allowed_username in mail_agent.toml).
$Zugang = 'MAIL_AGENT/postfach'
$Adresse = 'info@musterpflege.example'
$LokalBin = Join-Path $env:USERPROFILE '.local\bin'

trap {
    Write-Host "`nAbbruch: $_" -ForegroundColor Red
    Write-Host 'Bitte diese Meldung an Claude schicken.'
    exit 1
}

function Schritt([string]$Text) { Write-Host "`n== $Text" -ForegroundColor Cyan }

function Vorhanden([string]$Befehl) { [bool](Get-Command $Befehl -ErrorAction SilentlyContinue) }

function Pruefe([string]$Was) {
    if ($LASTEXITCODE -ne 0) { throw "$Was ist fehlgeschlagen (Code $LASTEXITCODE)." }
}

function PfadNeuLaden {
    $env:Path = @(
        [Environment]::GetEnvironmentVariable('Path', 'Machine'),
        [Environment]::GetEnvironmentVariable('Path', 'User'),
        $LokalBin
    ) -join ';'
}

Schritt 'Programme (Git, uv, Claude Code)'
PfadNeuLaden
if (-not (Vorhanden 'git')) {
    if (-not (Vorhanden 'winget')) {
        throw 'Git fehlt und winget ist nicht verfügbar. Bitte Git von https://git-scm.com/download/win installieren.'
    }
    winget install --id Git.Git -e --accept-package-agreements --accept-source-agreements
    Pruefe 'Installation von Git'
    PfadNeuLaden
}
if (-not (Vorhanden 'uv')) {
    powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    Pruefe 'Installation von uv'
    PfadNeuLaden
}
if (-not (Vorhanden 'claude')) {
    powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://claude.ai/install.ps1 | iex'
    Pruefe 'Installation von Claude Code'
    PfadNeuLaden
}
# uv und Claude Code liegen in %USERPROFILE%\.local\bin; dauerhaft in den Benutzer-PATH aufnehmen.
$BenutzerPfad = [Environment]::GetEnvironmentVariable('Path', 'User')
$Eintraege = @("$BenutzerPfad" -split ';' | Where-Object { $_ })
if ((Test-Path $LokalBin) -and ($Eintraege -notcontains $LokalBin)) {
    [Environment]::SetEnvironmentVariable('Path', (($Eintraege + $LokalBin) -join ';'), 'User')
}
foreach ($Befehl in 'git', 'uv', 'claude') {
    if (-not (Vorhanden $Befehl)) {
        throw "$Befehl wurde nicht gefunden. Bitte ein neues PowerShell-Fenster öffnen und das Skript erneut starten."
    }
}
Write-Host 'Git, uv und Claude Code sind vorhanden.'

Schritt 'Laufenden Agent beenden (sonst ist die Python-Umgebung gesperrt)'
$AgentIds = @()
foreach ($Verbindung in @(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue)) {
    $Prozess = Get-Process -Id $Verbindung.OwningProcess -ErrorAction SilentlyContinue
    if ($Prozess -and $Prozess.ProcessName -match '^(pythonw?|mail-agent)$') { $AgentIds += $Prozess.Id }
}
if ($AgentIds.Count -eq 0) {
    Write-Host 'Der Agent lief nicht.'
} else {
    # Erst sanft wie mit dem Knopf „Beenden“: das Symbol neben der Uhr verschwindet, eine offene Übergabe wird
    # geschrieben. Ältere Versionen kennen das noch nicht; dann nach höchstens 30 Sekunden hart beenden.
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:8765/beenden' -Method Post -Body @{ trotzdem = '1' } -UseBasicParsing -TimeoutSec 10 | Out-Null
    } catch {
        Write-Host 'Sanftes Beenden ging nicht; der Agent wird gleich hart beendet.'
    }
    for ($Runde = 0; $Runde -lt 30 -and @(Get-Process -Id $AgentIds -ErrorAction SilentlyContinue).Count -gt 0; $Runde++) {
        Start-Sleep -Seconds 1
    }
    Get-Process -Id $AgentIds -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Seconds 1  # Windows gibt die Dateien in .venv erst kurz nach dem Prozessende frei
    Write-Host 'Agent beendet; er startet am Ende neu.'
}

Schritt 'Aktuellen Stand von GitHub holen'
Set-Location $Projekt
git pull --ff-only
if ($LASTEXITCODE -ne 0) { Write-Warning 'git pull hat nicht geklappt; es geht mit dem vorhandenen Stand weiter.' }

Schritt 'Python-Umgebung (uv sync)'
uv sync --inexact
Pruefe 'uv sync'

Schritt 'Konfiguration'
$Konfig = Join-Path $Projekt 'mail_agent.toml'
if (Test-Path $Konfig) {
    Write-Host 'mail_agent.toml ist vorhanden und bleibt unverändert.'
} else {
    Copy-Item (Join-Path $Projekt 'mail_agent.example.toml') $Konfig
    Write-Host 'mail_agent.toml aus der Beispielkonfiguration angelegt.'
}

Schritt 'Postfachzugang im Windows-Anmeldeinformationsspeicher'
$Liste = cmdkey "/list:$Zugang" | Out-String
if ($Liste -match [regex]::Escape($Adresse)) {
    Write-Host "Zugang $Zugang für $Adresse ist eingerichtet."
} else {
    Write-Host "Bitte das Postfach-Passwort für $Adresse eingeben (die Eingabe bleibt unsichtbar):"
    cmdkey "/generic:$Zugang" "/user:$Adresse" /pass
    Pruefe 'cmdkey'
}

Schritt 'Claude-Anmeldung (Abo)'
claude auth status --text
$Antwort = Read-Host 'Jetzt mit dem Claude-Abo anmelden? Nur nötig, wenn oben keine Abo-Anmeldung steht (j/n)'
if ($Antwort -match '^[jJyY]') {
    claude auth login --claudeai
    Pruefe 'claude auth login'
}

Schritt 'Prüfungen (uv run pytest)'
uv run pytest -q
if ($LASTEXITCODE -ne 0) {
    Write-Warning 'Es sind Tests fehlgeschlagen. Bitte die Ausgabe an Claude schicken.'
} else {
    Write-Host 'Alle Tests bestanden.' -ForegroundColor Green
}

Schritt 'Desktop-Verknüpfung'
$Verknuepfung = Join-Path ([Environment]::GetFolderPath('Desktop')) 'MailAgent.lnk'
$Shell = New-Object -ComObject WScript.Shell
$Link = $Shell.CreateShortcut($Verknuepfung)
$Link.TargetPath = Join-Path $PSScriptRoot 'starten.cmd'
$Link.WorkingDirectory = $Projekt
$Link.Save()
Write-Host "Angelegt: $Verknuepfung"

Schritt 'Autostart bei der Windows-Anmeldung (unsichtbar im Hintergrund)'
$Hintergrund = Join-Path $Projekt '.venv\Scripts\pythonw.exe'
$Autostart = Join-Path ([Environment]::GetFolderPath('Startup')) 'MailAgent.lnk'
$Link = $Shell.CreateShortcut($Autostart)
$Link.TargetPath = $Hintergrund
$Link.Arguments = '-m mail_agent.background'
$Link.WorkingDirectory = $Projekt
$Link.Description = 'MailAgent im Hintergrund starten (Meldungen in daten\agent.log)'
$Link.Save()
Write-Host "Angelegt: $Autostart"

if (-not (Vorhanden 'tesseract') -and -not (Test-Path 'C:\Program Files\Tesseract-OCR\tesseract.exe')) {
    Write-Host "`nOptional: Für eingescannte Anhänge Tesseract installieren (siehe README, Texterkennung)."
}

Schritt 'Agent im Hintergrund starten'
Start-Process -FilePath $Hintergrund -ArgumentList '-m', 'mail_agent.background' -WorkingDirectory $Projekt
Write-Host 'Gestartet. Er startet ab jetzt bei jeder Windows-Anmeldung von selbst.'

Write-Host "`nFertig. Ein Doppelklick auf 'MailAgent' auf dem Desktop öffnet den Browser." -ForegroundColor Green
