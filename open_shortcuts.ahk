; Opens all .lnk shortcut files found in the chosen directory.
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

dir := DirSelect(, , "Select the folder containing your shortcuts")
if dir = ""
    ExitApp

count := 0
loop files, dir "\*.lnk" {
    Run A_LoopFileFullPath
    count++
    Sleep 150   ; small delay so Windows doesn't get overwhelmed
}

if count = 0
    MsgBox "No .lnk shortcut files found in:`n" dir
else
    MsgBox count " shortcut(s) opened."
