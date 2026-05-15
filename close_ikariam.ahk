; Closes all windows whose title contains "ikariam", "ikabot", or "ambrosia".
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

result := MsgBox("Close all Ikariam / Ikabot / Ambrosia windows?", "Confirm", "YesNo Icon?")
if result = "No"
    ExitApp

keywords := ["ikariam", "ikabot", "ambrosia"]
count := 0

for _, kw in keywords {
    for hwnd in WinGetList("*" kw "*") {
        WinClose hwnd
        count++
    }
}

MsgBox count " window(s) closed."
