#Requires AutoHotkey v2.0
#SingleInstance Off
DetectHiddenWindows true
SetControlDelay -1
SetWinDelay -1
SetTimer () => ExitApp(124), -5000

try {
    action := A_Args[1]
    dialog := Integer(A_Args[2])
    control := Integer(A_Args[3])
    expectedPid := Integer(A_Args[4])
    output := A_Args[5]
    if !WinExist("ahk_id " dialog)
        throw Error("Dialog no longer exists")
    if WinGetClass("ahk_id " dialog) != "#32770"
        throw Error("Not a captcha dialog")
    actualPid := 0
    DllCall("GetWindowThreadProcessId", "Ptr", dialog, "UInt*", &actualPid)
    if actualPid != expectedPid
        throw Error("Dialog process changed")
    image := DllCall("GetDlgItem", "Ptr", dialog, "Int", 0x965, "Ptr")
    editor := DllCall("GetDlgItem", "Ptr", dialog, "Int", 0x964, "Ptr")
    if !image || !editor
        throw Error("Captcha controls unavailable")
    if DllCall("GetParent", "Ptr", control, "Ptr") != dialog
        throw Error("Control no longer belongs to dialog")
    id := DllCall("GetDlgCtrlID", "Ptr", control, "Int")

    if action = "fill" {
        text := A_Args[6]
        if control != editor || !RegExMatch(text, "^[A-Za-z0-9]{4}$")
            throw Error("Invalid captcha input")
        ; Do not use WM_SETTEXT, Ctrl+A, Delete or modifier-key simulation.
        Loop 64
            PostMessage 0x0102, 8, 1, control, "ahk_id " dialog
        Sleep 40
        ControlSendText text, control, "ahk_id " dialog
    } else if action = "click" {
        if id != 1 && id != 2 && control != image
            throw Error("Unsupported captcha control")
        ControlClick control, "ahk_id " dialog, , "Left", 1, "NA"
    } else {
        throw Error("Unsupported action")
    }
    FileAppend "ok", output, "UTF-8"
    ExitApp 0
} catch Error as err {
    if A_Args.Length >= 5
        FileAppend "error", A_Args[5], "UTF-8"
    ExitApp 1
}
