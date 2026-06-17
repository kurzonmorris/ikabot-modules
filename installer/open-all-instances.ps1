# open-all-instances.ps1
# Opens every numbered ikabot shortcut in your shortcuts folder, in order.
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

# ── Find shortcuts ────────────────────────────────────────────────────────────
$scDir = Join-Path $installDir "shortcuts"

if (-not (Test-Path $scDir)) {
    Write-Host "ERROR: Shortcuts folder not found: $scDir" -ForegroundColor Red
    Write-Host "Make sure '$installDir' is your ikabot install folder."
    Read-Host "Press Enter to exit"
    exit 1
}

# Only numbered instance shortcuts (e.g. "1 ikariam.lnk"), skip installer shortcut
$lnks = Get-ChildItem $scDir -Filter "*.lnk" |
        Where-Object  { $_.Name -match '^\d' } |
        Sort-Object   { [int]([regex]::Match($_.Name, '\d+').Value) }

if ($lnks.Count -eq 0) {
    Write-Host "No numbered shortcuts found in: $scDir" -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 0
}

# ── Open each instance ────────────────────────────────────────────────────────
Write-Host "Opening $($lnks.Count) ikabot instance(s) from:"
Write-Host "  $scDir"
Write-Host ""

foreach ($lnk in $lnks) {
    Write-Host "  $($lnk.Name)"
    Start-Process -FilePath $lnk.FullName
    Start-Sleep -Milliseconds 150
}

Write-Host ""
Write-Host "Done. $($lnks.Count) instance(s) launched." -ForegroundColor Green
Start-Sleep -Seconds 2
