' SPDX-License-Identifier: MIT
' launch_file.vbs -- issue #833: RemoteApp Win32 file-open launcher.
'
' RemoteApp invokes a single program per session; this VBS is that program.
' wscript.exe is GUI-subsystem (no console window), so the only window the
' RemoteApp client paints is the target application itself.
'
' The host (winpodx.core.rdp._file_wrapper_payload) passes TWO arguments,
' both Base64(UTF-8): [0] the target executable, [1] the exact \\tsclient
' UNC of the host file. Base64 keeps spaces, commas and Unicode byte-exact
' through FreeRDP's /app: parsing and the RAIL command line; this script
' decodes both without any shell involvement.
'
' Flow: decode -> validate -> wait for the UNC to appear on the redirected
' share (they take a moment to attach) -> one no-wait Exec of the target
' executable with the file. Never shows a dialog: every failure path exits
' with a fixed code.
'
' Exit codes: 2 usage, 3 decode, 4 rejected characters, 5 file never
' appeared, 6 target launch failed.
'
' Usage:
'   wscript.exe launch_file.vbs <exe-base64> <unc-base64>

Option Explicit

Const POLL_INTERVAL_MS = 200
Const POLL_MAX_MS = 8000

If WScript.Arguments.Count <> 2 Then
    WScript.Quit 2
End If

Dim targetExe, filePath
targetExe = Base64ToUtf8(WScript.Arguments(0))
filePath = Base64ToUtf8(WScript.Arguments(1))

If HasUnsafeChars(targetExe) Or HasUnsafeChars(filePath) Then
    WScript.Quit 4
End If

Dim fso
Set fso = CreateObject("Scripting.FileSystemObject")

' Immediate check, then 200ms polling bounded to 8s total: the \\tsclient
' share usually attaches within the first poll on a warm session.
Dim waited
waited = 0
Do While Not fso.FileExists(filePath)
    If waited >= POLL_MAX_MS Then
        WScript.Quit 5
    End If
    WScript.Sleep POLL_INTERVAL_MS
    waited = waited + POLL_INTERVAL_MS
Loop

Dim shell, launched
Set shell = CreateObject("WScript.Shell")
On Error Resume Next
Err.Clear
Set launched = shell.Exec(QuoteArgument(targetExe) & " " & QuoteArgument(filePath))
If Err.Number <> 0 Then
    WScript.Quit 6
End If
On Error GoTo 0

Function Base64ToUtf8(b64)
    ' MSXML bin.base64 -> raw bytes, then ADODB.Stream re-reads them as
    ' UTF-8 text. Pure COM: no shell, no script host dependencies.
    On Error Resume Next
    Dim doc, stream, bytes
    Set doc = CreateObject("MSXML2.DOMDocument.6.0")
    If doc Is Nothing Then
        WScript.Quit 3
    End If
    If Not doc.loadXML("<b64 xmlns:dt=""urn:schemas-microsoft-com:datatypes"" dt:dt=""bin.base64"">" & b64 & "</b64>") Then
        WScript.Quit 3
    End If
    bytes = doc.documentElement.nodeTypedValue
    Set stream = CreateObject("ADODB.Stream")
    stream.Type = 1
    stream.Open
    stream.Write bytes
    stream.Position = 0
    stream.Type = 2
    stream.Charset = "utf-8"
    Base64ToUtf8 = stream.ReadText
    If Err.Number <> 0 Then
        WScript.Quit 3
    End If
End Function

Function HasUnsafeChars(value)
    ' Reject double quotes and C0 control characters: both can smuggle
    ' extra arguments past the quoting below. AscW of a surrogate-pair
    ' half can be negative, so the control-range test is bounded below
    ' by 0 to keep high Unicode (Korean, CJK, emoji) passing.
    HasUnsafeChars = False
    Dim i, code
    For i = 1 To Len(value)
        code = AscW(Mid(value, i, 1))
        If code = 34 Or (code >= 0 And code < 32) Then
            HasUnsafeChars = True
            Exit Function
        End If
    Next
End Function

Function QuoteArgument(value)
    ' Exec receives arguments as a Win32 command-line string. Double
    ' trailing backslashes so they cannot escape the closing double quote.
    Dim i, trailingBackslashes
    trailingBackslashes = 0
    For i = Len(value) To 1 Step -1
        If Mid(value, i, 1) <> "\" Then
            Exit For
        End If
        trailingBackslashes = trailingBackslashes + 1
    Next
    QuoteArgument = Chr(34) & value & String(trailingBackslashes, "\") & Chr(34)
End Function
