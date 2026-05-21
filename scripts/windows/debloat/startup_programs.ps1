# SPDX-License-Identifier: MIT
# winpodx debloat: common startup autorun entries.
#
# We only target the HKCU\...\Run + HKCU\...\StartupApproved keys,
# not HKLM, so this is reversible per-user (HKLM autostarts are usually
# OEM machine-level and shouldn't be touched without consent).

$names = @(
    "OneDrive",
    "OneDriveSetup",
    "Teams",
    "MicrosoftTeams",
    "Skype",
    "Spotify",
    "Discord",
    "Adobe Updater",
    "Adobe ARM",
    "iTunesHelper",
    "RealPlayerSetup",
    "GoogleUpdate"
)

foreach ($name in $names) {
    Write-Host "[startup_programs] Removing autostart entry: $name"
    Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name $name -Force -ErrorAction SilentlyContinue
    # Toggle the "approved" bitfield so even if the Run key resurfaces
    # via an installer, Windows shows it as disabled in the Startup
    # Apps UI rather than silently re-enabling it.
    $approvedKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
    if (Test-Path $approvedKey) {
        # 0x03000000 0x00000000 ... is the "disabled" sentinel Windows
        # writes when the user toggles a Startup App off via Settings.
        Set-ItemProperty -Path $approvedKey -Name $name -Value ([byte[]](0x03,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00,0x00)) -Type Binary -Force -ErrorAction SilentlyContinue
    }
}
