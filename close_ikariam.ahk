; Closes all running processes whose name contains "ikariam", "ikabot", or "ambrosia".
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

result := MsgBox("Close all Ikariam / Ikabot / Ambrosia processes?", "Confirm", "YesNo Icon?")
if result = "No"
    ExitApp

RunWait 'taskkill /F /FI "IMAGENAME eq *ikariam*"',, "Hide"
RunWait 'taskkill /F /FI "IMAGENAME eq *ikabot*"',, "Hide"
RunWait 'taskkill /F /FI "IMAGENAME eq *ambrosia*"',, "Hide"

MsgBox "All Ikariam / Ikabot / Ambrosia processes have been closed."
