# SPDX-License-Identifier: MIT
# launch_uwp.ps1 -- activate a UWP/MSIX app by AUMID via COM, no explorer.exe
#
# The default RemoteApp UWP launch (`explorer.exe shell:AppsFolder\<AUMID>`)
# briefly shows an explorer.exe RemoteApp window before dispatching to the
# UWP frame -- that's the "PowerShell-looking flash" users see when launching
# Calculator / Settings / Terminal et al. Calling IApplicationActivationManager
# directly skips the explorer transition: the UWP frame appears immediately
# without an intermediate window.
#
# Invoked via launch_uwp.vbs which keeps powershell.exe itself hidden so
# this script never flashes either.
#
# Usage: powershell.exe -NoProfile -ExecutionPolicy Bypass -File launch_uwp.ps1 <AUMID>

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Aumid
)

$ErrorActionPreference = 'Stop'

# IApplicationActivationManager -- Windows shell COM interface for launching
# packaged apps by AUMID. Documented in MSDN; available since Windows 8.
#
# Why a C# helper instead of calling the COM object directly from PowerShell:
# the interface (IID 2E941141-...) inherits IUnknown only, not IDispatch, so
# PS's dynamic-method dispatch can't find ActivateApplication ("does not
# contain a method named 'ActivateApplication'"). And the PowerShell-level
# cast `[IApplicationActivationManager]$rcw` doesn't carry the QueryInterface
# the CLR needs. Doing both inside a compiled C# scope works because the
# managed cast there resolves to a real COM QI.
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[ComImport, Guid("2E941141-7F97-4756-BA1D-9DECDE894A3D"),
 InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IApplicationActivationManager
{
    int ActivateApplication(
        [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
        [MarshalAs(UnmanagedType.LPWStr)] string arguments,
        int options,
        out uint processId);

    int ActivateForFile(
        [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
        IntPtr itemArray,
        [MarshalAs(UnmanagedType.LPWStr)] string verb,
        out uint processId);

    int ActivateForProtocol(
        [MarshalAs(UnmanagedType.LPWStr)] string appUserModelId,
        IntPtr itemArray,
        out uint processId);
}

public static class WinpodxUwpLauncher
{
    public static uint Activate(string aumid)
    {
        Type t = Type.GetTypeFromCLSID(new Guid("45BA127D-10A8-46EA-8AB7-56EA9078943C"));
        if (t == null)
            throw new InvalidOperationException("ApplicationActivationManager CLSID not registered");
        object instance = Activator.CreateInstance(t);
        IApplicationActivationManager iam = (IApplicationActivationManager)instance;
        uint pid;
        int hr = iam.ActivateApplication(aumid, null, 0, out pid);
        if (hr < 0)
            throw new InvalidOperationException("ActivateApplication HRESULT 0x" + hr.ToString("X8"));
        return pid;
    }
}
"@ -ErrorAction Stop

try {
    [void][WinpodxUwpLauncher]::Activate($Aumid)
    exit 0
} catch {
    # Don't surface to the user with a console -- write to a log the agent can
    # tail. Swallowing keeps RemoteApp's "session ended" return code clean
    # rather than dumping an uncaught .NET stack on the silent path.
    try {
        $logDir = Join-Path $env:LOCALAPPDATA 'winpodx'
        if (-not (Test-Path $logDir)) {
            [void](New-Item -ItemType Directory -Path $logDir -Force)
        }
        $logPath = Join-Path $logDir 'uwp-launcher.log'
        $line = "$((Get-Date).ToUniversalTime().ToString('o')) FAIL aumid=$Aumid err=$($_.Exception.Message)"
        Add-Content -Path $logPath -Value $line -ErrorAction SilentlyContinue
    } catch { }
    exit 1
}
