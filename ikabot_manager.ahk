; ikabot Manager — open instances, close instances, or update ikabot.
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

; ── Main menu ────────────────────────────────────────────────────────────────

menu := Gui("+AlwaysOnTop", "ikabot Manager")
menu.SetFont("s10")
menu.Add("Text", "w260 Center", "What would you like to do?")
menu.Add("Button", "w260 h40 vBtnOpen",   "Open all instances").OnEvent("Click", (*) => (menu.Hide(), OpenAll()))
menu.Add("Button", "w260 h40 vBtnClose",  "Close all instances").OnEvent("Click", (*) => (menu.Hide(), CloseAll()))
menu.Add("Button", "w260 h40 vBtnUpdate", "Update ikabot").OnEvent("Click", (*) => (menu.Hide(), UpdateIkabot()))
menu.OnEvent("Close", (*) => ExitApp())
menu.Show("AutoSize Center")

; ── Open all instances ───────────────────────────────────────────────────────

OpenAll() {
    dir := DirSelect(, , "Select your shortcuts folder")
    if dir = "" {
        menu.Show()
        return
    }

    files := []
    loop files, dir "\*.lnk"
        files.Push(A_LoopFileFullPath)

    if files.Length = 0 {
        MsgBox "No .lnk files found in:`n" dir
        menu.Show()
        return
    }

    n := files.Length
    loop n - 1 {
        i := A_Index
        loop n - i {
            j := A_Index
            if LeadingNum(files[j]) > LeadingNum(files[j + 1]) {
                temp    := files[j]
                files[j] := files[j + 1]
                files[j + 1] := temp
            }
        }
    }

    for _, f in files {
        Run f
        Sleep 150
    }

    MsgBox files.Length " instance(s) opened."
    ExitApp()
}

; ── Close all instances ──────────────────────────────────────────────────────

CloseAll() {
    if MsgBox("Close all ikabot instances?", "Confirm", "YesNo Icon?") = "No" {
        menu.Show()
        return
    }
    RunWait 'taskkill /F /IM ikabot.exe',, "Hide"
    MsgBox "All ikabot instances have been closed."
    ExitApp()
}

; ── Update ikabot ─────────────────────────────────────────────────────────────

UpdateIkabot() {
    installDir := DirSelect(, , "Select your ikabot install folder (the one containing 'ikabot template' and 'ikabot')")
    if installDir = "" {
        menu.Show()
        return
    }

    templateDir := installDir "\ikabot template"
    ikabotDir   := installDir "\ikabot"

    if !DirExist(templateDir) || !DirExist(ikabotDir) {
        MsgBox(
            "Could not find 'ikabot template' or 'ikabot' subfolders in:`n" installDir
            "`n`nPlease select the correct install folder.",
            "Wrong folder", "Icon!"
        )
        menu.Show()
        return
    }

    if MsgBox(
        "This will:`n`n"
        "  • Download the latest ikabot.zip from GitHub`n"
        "  • Replace the ikabot template folder`n"
        "  • Wipe and re-populate every ikariam folder`n`n"
        "Proceed?",
        "Confirm update", "YesNo Icon?"
    ) = "No" {
        menu.Show()
        return
    }

    ; Write the PowerShell update script to a temp file (avoids all quoting issues)
    psLines := "
(
$ErrorActionPreference = 'Stop'
try {
    Write-Host 'Fetching releases from GitHub...'
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

    if (-not $asset) { throw 'ikabot.zip not found in any GitHub release.' }

    Write-Host "Downloading ikabot $tag ..."
    $tmp = [IO.Path]::GetTempFileName() + '.zip'
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -UseBasicParsing

    Write-Host 'Clearing template folder...'
    $template = 'TEMPLATE_DIR'
    Get-ChildItem $template | Where-Object { $_.Name -notlike 'version*' } | ForEach-Object {
        if ($_.PSIsContainer) { Remove-Item $_.FullName -Recurse -Force }
        else                  { Remove-Item $_.FullName -Force }
    }

    Write-Host 'Extracting into template...'
    Expand-Archive -Path $tmp -DestinationPath $template -Force
    Remove-Item $tmp

    Write-Host 'Updating ikariam folders...'
    $ikabotDir = 'IKABOT_DIR'
    $folders = Get-ChildItem $ikabotDir -Directory | Where-Object { $_.Name -match '^ikariam \d+$' }

    foreach ($folder in $folders) {
        Write-Host "  $($folder.Name)"
        Get-ChildItem $folder.FullName | ForEach-Object {
            if ($_.PSIsContainer) { Remove-Item $_.FullName -Recurse -Force }
            else                  { Remove-Item $_.FullName -Force }
        }
        Copy-Item "$template\ikabot.exe"  "$($folder.FullName)\ikabot.exe"  -Force
        Copy-Item "$template\_internal"   "$($folder.FullName)\_internal"   -Recurse -Force
    }

    Write-Host "Done. $($folders.Count) folder(s) updated."
} catch {
    Write-Error $_
    exit 1
}
)"

    psLines := StrReplace(psLines, "TEMPLATE_DIR", templateDir)
    psLines := StrReplace(psLines, "IKABOT_DIR",   ikabotDir)

    tmpPs := A_Temp "\ikabot_update.ps1"
    FileAppend psLines, tmpPs, "UTF-8"

    rc := RunWait('powershell -ExecutionPolicy Bypass -NoProfile -File "' tmpPs '"')
    try FileDelete(tmpPs)

    if rc != 0
        MsgBox "Update failed — check the console output for details.", "Error", "Icon!"
    else
        MsgBox "Update complete!`nAll ikariam folders have been refreshed.", "Done"

    ExitApp()
}

; ── Helper ───────────────────────────────────────────────────────────────────

LeadingNum(path) {
    SplitPath path, &name
    return RegExMatch(name, "\d+", &m) ? Integer(m[]) : 0
}
