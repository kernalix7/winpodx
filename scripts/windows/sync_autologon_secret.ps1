# SPDX-License-Identifier: MIT
# Definitions only: callers supply the already-verified account password.

function Get-WinpodxAutologonSettings {
    $key = Get-Item -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -ErrorAction Stop
    $names = @($key.GetValueNames())
    $enabledKind = 'Missing'
    $userKind = 'Missing'
    $domainKind = 'Missing'
    $plaintextKind = 'Missing'
    if ($names -contains 'AutoAdminLogon') { $enabledKind = [string]$key.GetValueKind('AutoAdminLogon') }
    if ($names -contains 'DefaultUserName') { $userKind = [string]$key.GetValueKind('DefaultUserName') }
    if ($names -contains 'DefaultDomainName') { $domainKind = [string]$key.GetValueKind('DefaultDomainName') }
    if ($names -contains 'DefaultPassword') { $plaintextKind = [string]$key.GetValueKind('DefaultPassword') }
    [pscustomobject]@{
        Enabled = $key.GetValue('AutoAdminLogon')
        EnabledKind = $enabledKind
        UserName = $key.GetValue('DefaultUserName')
        UserKind = $userKind
        Domain = $key.GetValue('DefaultDomainName')
        DomainKind = $domainKind
        HasPlaintext = $names -contains 'DefaultPassword'
        PlaintextKind = $plaintextKind
    }
}

function Remove-WinpodxAutologonPlaintext {
    Remove-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon' -Name 'DefaultPassword' -ErrorAction Stop
}

function Set-WinpodxAutologonSecret([string]$Password) {
    if (-not ('WinpodxAutologonSecret' -as [type])) {
        Add-Type -ErrorAction Stop -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class WinpodxAutologonSecret {
    [StructLayout(LayoutKind.Sequential)]
    public struct UnicodeString {
        public ushort Length;
        public ushort MaximumLength;
        public IntPtr Buffer;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct ObjectAttributes {
        public uint Length;
        public IntPtr RootDirectory;
        public IntPtr ObjectName;
        public uint Attributes;
        public IntPtr SecurityDescriptor;
        public IntPtr SecurityQualityOfService;
    }
    [DllImport("advapi32.dll", ExactSpelling=true)]
    private static extern uint LsaOpenPolicy(IntPtr system, ref ObjectAttributes attributes,
        uint access, out IntPtr policy);
    [DllImport("advapi32.dll", ExactSpelling=true)]
    private static extern uint LsaStorePrivateData(IntPtr policy, ref UnicodeString key,
        ref UnicodeString data);
    [DllImport("advapi32.dll", ExactSpelling=true)]
    private static extern uint LsaClose(IntPtr policy);
    public const uint PolicyCreateSecret = 0x20;
    private static UnicodeString Allocate(string value) {
        if (value.Length > 32766) throw new ArgumentOutOfRangeException("value");
        UnicodeString result = new UnicodeString();
        result.Length = checked((ushort)(value.Length * 2));
        result.MaximumLength = checked((ushort)(result.Length + 2));
        result.Buffer = Marshal.StringToHGlobalUni(value);
        return result;
    }
    public static int Store(string password) {
        if (String.IsNullOrEmpty(password)) return 11;
        IntPtr policy = IntPtr.Zero;
        UnicodeString key = new UnicodeString();
        UnicodeString data = new UnicodeString();
        try {
            ObjectAttributes attributes = new ObjectAttributes();
            attributes.Length = (uint)Marshal.SizeOf(typeof(ObjectAttributes));
            uint status = LsaOpenPolicy(IntPtr.Zero, ref attributes, PolicyCreateSecret, out policy);
            if (status != 0) return 11;
            key = Allocate("DefaultPassword");
            data = Allocate(password);
            status = LsaStorePrivateData(policy, ref key, ref data);
            return status == 0 ? 10 : 12;
        } finally {
            if (data.Buffer != IntPtr.Zero) Marshal.ZeroFreeGlobalAllocUnicode(data.Buffer);
            if (key.Buffer != IntPtr.Zero) Marshal.ZeroFreeGlobalAllocUnicode(key.Buffer);
            if (policy != IntPtr.Zero) LsaClose(policy);
        }
    }
}
'@
    }
    [WinpodxAutologonSecret]::Store($Password)
}

function Invoke-WinpodxAutologonSync {
    param([string]$UserName, [string]$Domain, [AllowEmptyString()][string]$Password)
    if ([string]::IsNullOrEmpty($Password)) { return 'WINPODX_AUTOLOGON_STATUS=11' }
    try { $settings = Get-WinpodxAutologonSettings } catch { return 'WINPODX_AUTOLOGON_STATUS=11' }
    if ($settings.EnabledKind -eq 'String') {
        if ($settings.Enabled -isnot [string] -or $settings.Enabled -cne '1') { return 'WINPODX_AUTOLOGON_STATUS=0' }
    } elseif ($settings.EnabledKind -eq 'DWord') {
        if ($settings.Enabled -isnot [int] -or $settings.Enabled -ne 1) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    } else { return 'WINPODX_AUTOLOGON_STATUS=0' }
    if ($settings.UserKind -ne 'String' -or $settings.UserName -isnot [string]) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    if ($settings.DomainKind -notin @('String', 'Missing')) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    if ($settings.DomainKind -eq 'String' -and $settings.Domain -isnot [string]) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    $user = $UserName.Trim()
    $expectedDomain = $Domain.Trim()
    if ($user.Contains('@')) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    $parts = $user.Split('\')
    if ($parts.Count -gt 2) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    if ($parts.Count -eq 2) {
        if ($expectedDomain -and $expectedDomain -ine $parts[0].Trim()) { return 'WINPODX_AUTOLOGON_STATUS=0' }
        if (-not $expectedDomain) { $expectedDomain = $parts[0].Trim() }
        $user = $parts[1].Trim()
    }
    # Account rotation is local-only; never rewrite a domain account's secret.
    if ($expectedDomain -and $expectedDomain -ne '.' -and $expectedDomain -ine [Environment]::MachineName) {
        return 'WINPODX_AUTOLOGON_STATUS=0'
    }
    if (-not $user -or $user -ine ([string]$settings.UserName).Trim()) { return 'WINPODX_AUTOLOGON_STATUS=0' }
    $actualDomain = ([string]$settings.Domain).Trim()
    if ($actualDomain -and $actualDomain -ne '.' -and $actualDomain -ine [Environment]::MachineName) {
        return 'WINPODX_AUTOLOGON_STATUS=0'
    }
    if ($settings.HasPlaintext -and $settings.PlaintextKind -ne 'String') { return 'WINPODX_AUTOLOGON_STATUS=11' }
    try { $status = Set-WinpodxAutologonSecret $Password } catch { return 'WINPODX_AUTOLOGON_STATUS=12' }
    if ($status -eq 11) { return 'WINPODX_AUTOLOGON_STATUS=11' }
    if ($status -ne 10) { return 'WINPODX_AUTOLOGON_STATUS=12' }
    if ($settings.HasPlaintext) {
        try { Remove-WinpodxAutologonPlaintext } catch { return 'WINPODX_AUTOLOGON_STATUS=12' }
    }
    return 'WINPODX_AUTOLOGON_STATUS=10'
}
