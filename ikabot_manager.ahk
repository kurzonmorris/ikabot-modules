; ikabot Manager — open, close, or update ikabot instances.
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

; ── Config (remembers last-used folders between sessions) ────────────────────

CFG := A_ScriptDir "\ikabot_manager.ini"

SaveCfg(key, val) {
    global CFG
    IniWrite val, CFG, "Paths", key
}

LoadCfg(key, default := "") {
    global CFG
    try return IniRead(CFG, "Paths", key)
    return default
}

; ── Main GUI ──────────────────────────────────────────────────────────────────

mainGui := Gui("+AlwaysOnTop", "ikabot Manager")
mainGui.SetFont("s10")
mainGui.Add("Text", "w280 Center", "ikabot Manager")
mainGui.Add("Text", "w280 Center", "Select an action:")
mainGui.Add("Button", "w280 h40", "Open all instances").OnEvent("Click",  (*) => OpenAll())
mainGui.Add("Button", "w280 h40", "Close all instances").OnEvent("Click", (*) => CloseAll())
mainGui.Add("Button", "w280 h40", "Update ikabot").OnEvent("Click",       (*) => UpdateIkabot())
mainGui.Add("Button", "w280 h30", "Exit").OnEvent("Click",                (*) => ExitApp())
mainGui.OnEvent("Close", (*) => ExitApp())
mainGui.Show("AutoSize Center")

; ── Open all instances ────────────────────────────────────────────────────────

OpenAll() {
    global mainGui
    mainGui.Hide()

    dir := DirSelect(LoadCfg("ShortcutsDir"), , "Select your ikabot shortcuts folder")
    if dir = "" {
        mainGui.Show()
        return
    }
    SaveCfg("ShortcutsDir", dir)

    files := []
    loop files, dir "\*.lnk"
        files.Push(A_LoopFileFullPath)

    if files.Length = 0 {
        MsgBox(
            "No shortcut (.lnk) files were found in:`n  " dir
            "`n`nMake sure you selected the correct shortcuts folder.",
            "Nothing found", "Icon!"
        )
        mainGui.Show()
        return
    }

    ; Natural number sort: 9, 10, 11 ... not 1, 10, 11, 2 ...
    n := files.Length
    loop n - 1 {
        i := A_Index
        loop n - i {
            j := A_Index
            if LeadingNum(files[j]) > LeadingNum(files[j + 1]) {
                temp         := files[j]
                files[j]     := files[j + 1]
                files[j + 1] := temp
            }
        }
    }

    for _, f in files {
        Run f
        Sleep 150
    }

    MsgBox files.Length " ikabot instance(s) launched.", "Done"
    mainGui.Show()
}

; ── Close all instances ───────────────────────────────────────────────────────

CloseAll() {
    global mainGui
    mainGui.Hide()

    if MsgBox("Close all running ikabot instances?", "Confirm", "YesNo Icon?") = "No" {
        mainGui.Show()
        return
    }

    rc := RunWait('taskkill /F /IM ikabot.exe',, "Hide")

    if rc = 0
        MsgBox "All ikabot instances have been closed.", "Done"
    else if rc = 128
        MsgBox "No ikabot instances were running.", "Nothing to close", "Icon!"
    else
        MsgBox(
            "Taskkill exited with code " rc ".`nSome instances may not have closed cleanly.",
            "Warning", "Icon!"
        )

    mainGui.Show()
}

; ── Update ikabot ─────────────────────────────────────────────────────────────

UpdateIkabot() {
    global mainGui
    mainGui.Hide()

    savedDir := LoadCfg("InstallDir")
    if savedDir = ""
        savedDir := "C:\Program Files\ikabot"

    dir := DirSelect(savedDir, , "Select your ikabot install folder  (contains 'ikabot template' and 'ikabot' subfolders)")
    if dir = "" {
        mainGui.Show()
        return
    }

    templateDir := dir "\ikabot template"
    ikabotDir   := dir "\ikabot"

    if !DirExist(templateDir) || !DirExist(ikabotDir) {
        missing := ""
        if !DirExist(templateDir)
            missing .= "`n  - ikabot template"
        if !DirExist(ikabotDir)
            missing .= "`n  - ikabot"
        MsgBox(
            "The following required folders were not found inside:`n  " dir missing
            "`n`nPlease select the correct ikabot install folder.",
            "Wrong folder", "Icon!"
        )
        mainGui.Show()
        return
    }

    SaveCfg("InstallDir", dir)

    ; Read the locally installed version from the template folder
    localVer := "not installed"
    if FileExist(templateDir "\version.json") {
        try {
            raw := FileRead(templateDir "\version.json")
            if RegExMatch(raw, '"version"\s*:\s*"([^"]+)"', &m)
                localVer := m[1]
        }
    }

    ; Count existing ikabot instance folders
    folderCount := 0
    loop files, ikabotDir "\ikariam *", "D"
        folderCount++

    if MsgBox(
        "Current installed version:  " localVer "`n"
        "ikabot instance folders:    " folderCount "`n`n"
        "This will:`n"
        "  - Download the latest ikabot release from GitHub`n"
        "  - Replace the ikabot template folder contents`n"
        "  - Wipe and re-populate all " folderCount " instance folder(s)`n`n"
        "Proceed?",
        "Confirm update", "YesNo Icon?"
    ) = "No" {
        mainGui.Show()
        return
    }

    logFile := A_Temp "\ikabot_update_log.txt"
    tmpPs   := A_Temp "\ikabot_update.ps1"

    if FileExist(tmpPs)
        FileDelete tmpPs
    if FileExist(logFile)
        FileDelete logFile

    ; PowerShell script — written to a temp file at runtime.
    ; Uses only single-quoted PS strings to keep the AHK continuation
    ; section free of double-quotes (avoids any parsing ambiguity).
    psContent :=
(
$ErrorActionPreference = 'Stop'

function Log($msg) {
    Write-Host $msg
    Add-Content -Path 'LOG_FILE' -Value $msg -Encoding UTF8
}

try {
    Log 'Checking GitHub for the latest ikabot release...'
    $headers  = @{ 'User-Agent' = 'ikabot-manager'; 'Accept' = 'application/vnd.github+json' }
    $releases = Invoke-RestMethod -Uri 'https://api.github.com/repos/kurzonmorris/ikabot-modules/releases' -Headers $headers

    $asset = $null
    $tag   = $null
    foreach ($r in $releases) {
        foreach ($a in $r.assets) {
            if ($a.name -eq 'ikabot.zip') { $asset = $a; $tag = $r.tag_name; break }
        }
        if ($asset) { break }
    }

    if (-not $asset) {
        throw 'ikabot.zip was not found in any GitHub release. Please check that the release has been published and try again.'
    }

    Log ('Latest version available: ' + $tag)
    Log 'Downloading ikabot...'
    $tmp = [IO.Path]::GetTempFileName() + '.zip'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -UseBasicParsing
    Log 'Download complete.'

    Log 'Clearing ikabot template folder...'
    $template = 'TEMPLATE_DIR'
    Get-ChildItem $template | Where-Object { $_.Name -notlike 'version*' } | ForEach-Object {
        if ($_.PSIsContainer) { Remove-Item $_.FullName -Recurse -Force }
        else                  { Remove-Item $_.FullName -Force }
    }

    Log 'Extracting new version into template...'
    Expand-Archive -Path $tmp -DestinationPath $template -Force
    Remove-Item $tmp

    $ver = $tag.TrimStart('v')
    Set-Content -Path ($template + '\version.json')       -Value ('{"version": "' + $ver + '"}')
    Set-Content -Path ($template + '\version_' + $ver)    -Value ''

    Log 'Updating ikabot instance folders...'
    $ikabotDir = 'IKABOT_DIR'
    $folders = Get-ChildItem $ikabotDir -Directory |
               Where-Object { $_.Name -match '^ikariam \d+$' } |
               Sort-Object   { [int]($_.Name -replace '\D', '') }

    $count = 0
    foreach ($folder in $folders) {
        Log ('  ' + $folder.Name)
        Get-ChildItem $folder.FullName | ForEach-Object {
            if ($_.PSIsContainer) { Remove-Item $_.FullName -Recurse -Force }
            else                  { Remove-Item $_.FullName -Force }
        }
        Copy-Item ($template + '\ikabot.exe') ($folder.FullName + '\ikabot.exe') -Force
        Copy-Item ($template + '\_internal')  ($folder.FullName + '\_internal')  -Recurse -Force
        $count++
    }

    Log ''
    Log ('Update complete. Installed ' + $tag + '. ' + $count + ' instance folder(s) refreshed.')

} catch {
    Log ('ERROR: ' + $_.ToString())
    exit 1
}
)

    psContent := StrReplace(psContent, "LOG_FILE",     logFile)
    psContent := StrReplace(psContent, "TEMPLATE_DIR", templateDir)
    psContent := StrReplace(psContent, "IKABOT_DIR",   ikabotDir)

    FileAppend psContent, tmpPs, "UTF-8"

    rc := RunWait('powershell -ExecutionPolicy Bypass -NoProfile -File "' tmpPs '"')
    try FileDelete(tmpPs)

    log := ""
    if FileExist(logFile) {
        log := FileRead(logFile)
        try FileDelete(logFile)
    }

    if rc != 0
        MsgBox("Update failed.`n`nDetails:`n`n" log, "Update failed", "Icon!")
    else
        MsgBox(log "`n`nAll ikabot instance folders are now up to date.", "Update complete")

    mainGui.Show()
}

; ── Helper ────────────────────────────────────────────────────────────────────

LeadingNum(path) {
    SplitPath path, &name
    return RegExMatch(name, "\d+", &m) ? Integer(m[]) : 0
}
