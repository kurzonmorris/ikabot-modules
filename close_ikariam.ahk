; Closes all running processes whose name contains "ikariam", "ikabot", or "ambrosia".
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

result := MsgBox("Close all Ikariam / Ikabot / Ambrosia processes?", "Confirm", "YesNo Icon?")
if result = "No"
    ExitApp

psCmd := "Get-Process | Where-Object { $_.Name -like '*ikariam*' -or $_.Name -like '*ikabot*' -or $_.Name -like '*ambrosia*' } | Stop-Process -Force"
cmd := 'powershell -WindowStyle Hidden -Command "' . psCmd . '"'
RunWait cmd,, "Hide"

MsgBox "All Ikariam / Ikabot / Ambrosia processes have been closed."
