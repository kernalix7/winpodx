# winpodx Windows debloat script
# Disables telemetry, ads, and unnecessary services for a clean RDP experience.
# Run once after Windows installation.

Write-Host "=== winpodx debloat ==="

# --- Disable Telemetry ---
Write-Host "Disabling telemetry..."
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection" -Name "AllowTelemetry" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue
Set-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection" -Name "AllowTelemetry" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue

# Disable Connected User Experiences
Stop-Service -Name "DiagTrack" -Force -ErrorAction SilentlyContinue
Set-Service -Name "DiagTrack" -StartupType Disabled -ErrorAction SilentlyContinue
Stop-Service -Name "dmwappushservice" -Force -ErrorAction SilentlyContinue
Set-Service -Name "dmwappushservice" -StartupType Disabled -ErrorAction SilentlyContinue

# --- Disable Ads & Suggestions ---
Write-Host "Disabling ads and suggestions..."
$adKeys = @(
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338388Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-338389Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SubscribedContent-353698Enabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SilentInstalledAppsEnabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SystemPaneSuggestionsEnabled"; Value=0},
    @{Path="HKCU:\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"; Name="SoftLandingEnabled"; Value=0}
)

foreach ($key in $adKeys) {
    New-Item -Path $key.Path -Force -ErrorAction SilentlyContinue | Out-Null
    Set-ItemProperty -Path $key.Path -Name $key.Name -Value $key.Value -Type DWord -Force -ErrorAction SilentlyContinue
}

# --- Disable Cortana ---
Write-Host "Disabling Cortana..."
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search" -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\Windows Search" -Name "AllowCortana" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue

# --- Disable Windows Search indexing (saves CPU in VM) ---
Write-Host "Disabling search indexing..."
Stop-Service -Name "WSearch" -Force -ErrorAction SilentlyContinue
Set-Service -Name "WSearch" -StartupType Disabled -ErrorAction SilentlyContinue

# --- Disable Superfetch/SysMain (saves disk I/O in VM) ---
Write-Host "Disabling SysMain..."
Stop-Service -Name "SysMain" -Force -ErrorAction SilentlyContinue
Set-Service -Name "SysMain" -StartupType Disabled -ErrorAction SilentlyContinue

# --- Disable hibernation (saves disk space in VM) ---
Write-Host "Disabling hibernation..."
powercfg /h off 2>$null

# --- Disable Windows Update auto-restart ---
Write-Host "Disabling auto-restart for updates..."
New-Item -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Force -ErrorAction SilentlyContinue | Out-Null
Set-ItemProperty -Path "HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" -Name "NoAutoRebootWithLoggedOnUsers" -Value 1 -Type DWord -Force -ErrorAction SilentlyContinue

# --- Optimize for RDP ---
Write-Host "Optimizing for RDP..."
# Disable animations
Set-ItemProperty -Path "HKCU:\Control Panel\Desktop" -Name "UserPreferencesMask" -Value ([byte[]](0x90,0x12,0x03,0x80,0x10,0x00,0x00,0x00)) -Type Binary -Force -ErrorAction SilentlyContinue
# Disable transparency
Set-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" -Name "EnableTransparency" -Value 0 -Type DWord -Force -ErrorAction SilentlyContinue

# --- Set timezone to UTC ---
Write-Host "Setting timezone to UTC..."
tzutil /s "UTC" 2>$null

Write-Host "=== debloat complete ==="
