; Closes all running processes whose name contains "ikariam".
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

result := MsgBox("Close all Ikariam processes?", "Confirm", "YesNo Icon?")
if result = "No"
    ExitApp

RunWait 'taskkill /F /FI "IMAGENAME eq *ikariam*"',, "Hide"

MsgBox "All Ikariam processes have been closed."
