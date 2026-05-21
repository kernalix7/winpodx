' SPDX-License-Identifier: MIT
' hidden-launcher.vbs -- universal "run any command, never show a window"
'
' wscript.exe runs as a GUI-subsystem process (no console of its own), and
' WshShell.Run with intWindowStyle=0 (SW_HIDE) propagates STARTF_USESHOWWINDOW
' to CreateProcess, so the spawned child also starts with its window hidden
' before any flash is possible.
'
' Used by agent autostart and any other path that would otherwise flash a
' PowerShell or cmd console at the user. Args are forwarded verbatim to
' WshShell.Run after quoting.
'
' Usage:
'   wscript.exe hidden-launcher.vbs <program> [args...]
' Example (agent autostart from HKCU\Run):
'   wscript.exe C:\OEM\hidden-launcher.vbs powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\OEM\agent.ps1

Option Explicit

If WScript.Arguments.Count = 0 Then
    WScript.Quit 1
End If

Dim shell, cmd, i
Set shell = CreateObject("WScript.Shell")

cmd = ""
For i = 0 To WScript.Arguments.Count - 1
    If i > 0 Then cmd = cmd & " "
    cmd = cmd & """" & WScript.Arguments(i) & """"
Next

' 0 = SW_HIDE; False = don't wait, return immediately so wscript itself exits.
shell.Run cmd, 0, False
