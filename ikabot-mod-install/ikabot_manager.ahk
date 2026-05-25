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

    logFile   := A_Temp "\ikabot_update_log.txt"
    tmpPs     := A_Temp "\ikabot_update_run.ps1"
    psTemplate := A_ScriptDir "\ikabot_update.ps1"

    if FileExist(tmpPs)
        FileDelete tmpPs
    if FileExist(logFile)
        FileDelete logFile

    if !FileExist(psTemplate) {
        MsgBox(
            "Update script not found:`n  " psTemplate
            "`n`nPlease re-run the ikabot installer to repair the installation.",
            "Missing file", "Icon!"
        )
        mainGui.Show()
        return
    }

    psContent := FileRead(psTemplate)
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
