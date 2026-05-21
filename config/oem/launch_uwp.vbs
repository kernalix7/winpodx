' SPDX-License-Identifier: MIT
' launch_uwp.vbs -- RemoteApp-friendly UWP launcher
'
' RemoteApp invokes a single program; this VBS is that program. wscript.exe
' has no console of its own, and WshShell.Run with intWindowStyle=0 spawns
' powershell.exe with SW_HIDE so launch_uwp.ps1 never flashes a console.
' launch_uwp.ps1 then activates the AUMID via IApplicationActivationManager
' and exits. The UWP frame becomes the visible RemoteApp window directly --
' no explorer.exe transition flash.
'
' Usage:
'   wscript.exe launch_uwp.vbs <AUMID>
' RemoteApp wiring (xfreerdp):
'   /app:program:wscript.exe,cmd:C:\OEM\launch_uwp.vbs <AUMID>

Option Explicit

If WScript.Arguments.Count = 0 Then
    WScript.Quit 1
End If

Dim shell, aumid, cmd
Set shell = CreateObject("WScript.Shell")

aumid = WScript.Arguments(0)

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\Public\winpodx\launchers\launch_uwp.ps1"" """ & aumid & """"

' 0 = SW_HIDE so the powershell.exe child also starts windowless. Don't wait
' on the call so wscript itself exits immediately and the UWP frame is the
' only window the RemoteApp client sees.
shell.Run cmd, 0, False
