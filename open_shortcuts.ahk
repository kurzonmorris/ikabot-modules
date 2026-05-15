; Opens all .lnk shortcut files found in the chosen directory in natural number order.
; Requires AutoHotkey v2.x  (https://www.autohotkey.com/)

#Requires AutoHotkey v2.0

dir := DirSelect(, , "Select the folder containing your shortcuts")
if dir = ""
    ExitApp

files := []
loop files, dir "\*.lnk"
    files.Push(A_LoopFileFullPath)

if files.Length = 0 {
    MsgBox "No .lnk shortcut files found in:`n" dir
    ExitApp
}

; Sort by the first number found in the filename (natural order: 9, 10, 11, 12...)
n := files.Length
loop n - 1 {
    i := A_Index
    loop n - i {
        j := A_Index
        if LeadingNum(files[j]) > LeadingNum(files[j + 1]) {
            temp := files[j]
            files[j] := files[j + 1]
            files[j + 1] := temp
        }
    }
}

for _, f in files {
    Run f
    Sleep 150
}

MsgBox files.Length " shortcut(s) opened."

LeadingNum(path) {
    SplitPath path, &name
    return RegExMatch(name, "\d+", &m) ? Integer(m[]) : 0
}
