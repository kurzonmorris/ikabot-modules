# open-all-instances.ps1
# Opens every ikabot instance in its own PowerShell window, in number order.
# Reads the install location from the ikabot installer config automatically.

$ErrorActionPreference = 'Stop'

# ── Find install dir ──────────────────────────────────────────────────────────
$cfgPath = "$env:LOCALAPPDATA\ikabot\installer_config.json"

if (Test-Path $cfgPath) {
    try {
        $installDir = (Get-Content $cfgPath -Raw | ConvertFrom-Json).install_dir
    } catch {
        $installDir = $null
    }
}

if (-not $installDir -or -not (Test-Path $installDir)) {
    $installDir = Read-Host "ikabot install folder not found in config. Enter the path"
}

# ── Find instance folders ─────────────────────────────────────────────────────
$ikabotDir = Join-Path $installDir "ikabot"

if (-not (Test-Path $ikabotDir)) {
    Write-Host "ERROR: ikabot folder not found: $ikabotDir" -ForegroundColor Red
    Write-Host "Make sure '$installDir' is your ikabot install folder."
    Read-Host "Press Enter to exit"
    exit 1
}

$folders = Get-ChildItem $ikabotDir -Directory |
           Where-Object { $_.Name -match '^ikariam \d+$' } |
           Sort-Object   { [int]($_.Name -replace '\D', '') }

if ($folders.Count -eq 0) {
    Write-Host "No ikariam instance folders found in: $ikabotDir" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 0
}

# ── Open each instance in a PowerShell window ─────────────────────────────────
Write-Host "Opening $($folders.Count) ikabot instance(s) from:"
Write-Host "  $ikabotDir"
Write-Host ""

$launched = 0
foreach ($folder in $folders) {
    $exe = Join-Path $folder.FullName "ikabot.exe"
    if (-not (Test-Path $exe)) {
        Write-Host "  Skipping $($folder.Name) - no ikabot.exe found" -ForegroundColor Yellow
        continue
    }
    Write-Host "  $($folder.Name)"
    $cmd = "`$host.UI.RawUI.WindowTitle = '$($folder.Name)'; Set-Location '$($folder.FullName)'; & '.\ikabot.exe'"
    Start-Process powershell -ArgumentList '-NoExit', '-Command', $cmd
    $launched++
    Start-Sleep -Milliseconds 150
}

Write-Host ""
Write-Host "Done. $launched instance(s) launched in PowerShell windows." -ForegroundColor Green
Start-Sleep -Seconds 2
