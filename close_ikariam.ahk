; Closes all running ikabot.exe processes.
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

result := MsgBox("Close all Ikabot windows?", "Confirm", "YesNo Icon?")
if result = "No"
    ExitApp

RunWait 'taskkill /F /IM ikabot.exe',, "Hide"

MsgBox "All Ikabot windows have been closed."
