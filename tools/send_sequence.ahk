; Sends a fixed sequence of inputs, pressing Enter after each one.
; Trigger: Ctrl+X
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

^x:: {
    sequence := ["30", "33", "0", "32", "4", "1", "34", "s", "1", "2", "36", "2", "2", "2", "24", "", "0"]
    for _, item in sequence {
        if item != ""
            Send item
        Send "{Enter}"
        Sleep 200
    }
}
