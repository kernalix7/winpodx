@echo off
REM First-boot OEM setup for winpodx Windows guest. Runs once during dockur's unattended install. Every action must stay idempotent - there is no guest-side re-run channel in 0.1.6 (push/exec bridge planned for a later release).

set WINPODX_OEM_VERSION=26

echo [WinPodX] Starting post-install configuration (version %WINPODX_OEM_VERSION%)...

REM ---------------------------------------------------------------------------
REM Windows Defender exclusions - ABSOLUTE FIRST STEP.
REM
REM Background: kernalix7's fresh installs 2026-05-02 through 2026-05-04
REM consistently died at line ~248 (PS Expand-Archive of the rdprrap zip
REM under C:\OEM\). install.log ended at "--- extract attempt 1" with no
REM follow-up; setup.log was never created; agent.ps1 never copied; agent
REM never came up. 3 disconnected User sessions accumulated in qwinsta on
REM every attempt. v0.3.0 (OEM v12) on the same hardware worked.
REM
REM Diff against v0.3.0 install.bat: the install.bat code itself is
REM essentially unchanged in the pre-extract section. The rdprrap zip
REM blob hash is identical. What changed: (1) the OEM bundle grew from 6
REM files to 13+ (added rdprrap-activate.ps1, hidden-launcher.vbs,
REM launch_uwp.ps1/vbs, agent-respawn.ps1, agent/agent.ps1); (2) the
REM dockur image was pinned to a specific Windows 11 digest in PR #83
REM (v0.3.1), and that newer Windows build ships a stricter Defender
REM real-time policy. The combination - more PS/VBS files staged in
REM C:\OEM\ alongside rdprrap-installer.exe (which patches termsrv.dll
REM and is naturally classifier-flagged as PUP) - triggers Defender
REM real-time scanning of C:\OEM\ during install.bat's first PS call.
REM Defender locks one of the files; PS Expand-Archive blocks waiting on
REM the lock; install.bat blocks waiting on PS; whole thing deadlocks
REM until something kicks the autologon session.
REM
REM Excluding C:\OEM and C:\winpodx from real-time scanning at the very
REM start of install.bat means none of our staged files ever get
REM scanned. This is safe because install.bat is the only thing writing
REM there, the contents are SHA-pinned to the bundle, and the user
REM workload runs from C:\Users\... (still scanned). It also keeps
REM rdprrap-installer.exe itself out of Defender's process list.
REM
REM Add-MpPreference is idempotent - re-running install.bat (e.g., on
REM container recreate) just re-asserts the exclusion silently.
REM C:\Users\Public\winpodx is where register-apps.ps1 stages the
REM reverse-open shim + its per-slug .exe copies (#425). That tiny,
REM stripped, unsigned Rust binary trips Defender's ML heuristic
REM (Trojan:Win32/Rafvartar!rfn -- a false positive), so it gets
REM quarantined and reverse-open silently breaks. Exclude the path (and
REM the shim process) here, first-step, so the exclusion is in place
REM before per-user logon stages the files. Add-MpPreference accepts a
REM not-yet-existent path, so registering it early is fine.
echo [WinPodX] Adding Windows Defender exclusions for C:\OEM, C:\winpodx, C:\Users\Public\winpodx, and the winpodx helper processes...
powershell -NoProfile -Command "try { Add-MpPreference -ExclusionPath 'C:\OEM','C:\winpodx','C:\Users\Public\winpodx' -ErrorAction Stop; Add-MpPreference -ExclusionProcess 'rdprrap-installer.exe','winpodx-reverse-open-shim.exe' -ErrorAction Stop } catch { Write-Output ('defender-exclusion: ' + $_.Exception.Message) }" >nul 2>&1

echo [WinPodX] Setting DNS...
netsh interface ip set dns "Ethernet" static 1.1.1.1
netsh interface ip add dns "Ethernet" 1.0.0.1 index=2

echo [WinPodX] Enabling Remote Desktop...
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fSingleSessionPerUser /t REG_DWORD /d 0 /f

REM NLA off for automated FreeRDP; SecurityLayer=2 keeps TLS on the RDP channel.
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v UserAuthentication /t REG_DWORD /d 0 /f
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v SecurityLayer /t REG_DWORD /d 2 /f

echo [WinPodX] Enabling RemoteApp...
reg add "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList" /v fDisabledAllowList /t REG_DWORD /d 1 /f

REM Without fInheritInitialProgram, Windows ignores /shell: and /app: from the RDP client.
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v fInheritInitialProgram /t REG_DWORD /d 1 /f
REM v0.2.1: cap bumped 10 -> 50. cfg.pod.max_sessions still controls the
REM actual desired count via _apply_max_sessions; this is just the
REM ceiling so the cfg value isn't silently clamped at OEM time.
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxInstanceCount /t REG_DWORD /d 50 /f

REM Bug B (v0.1.9 / OEM v7): host suspend / long idle commonly leaves Windows
REM with TermService stalled or the virtual NIC in power-save, breaking RDP
REM while VNC keeps working. Two preventive measures so the host-side
REM recover_rdp_if_needed() helper has less to do.
echo [WinPodX] Disabling NIC power-save...
powershell -NoProfile -Command "Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object {$_.Status -ne 'Disabled'} | Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false -ErrorAction SilentlyContinue" >nul 2>&1

echo [WinPodX] Configuring TermService recovery actions...
REM 3 attempts at 5s spacing, 24h reset window - Windows itself recovers
REM TermService crashes without needing host intervention.
sc.exe failure TermService reset= 86400 actions= restart/5000/restart/5000/restart/5000 >nul 2>&1

REM v0.1.9.1: RDP session timeout + keep-alive. v0.2.1 adjusts the
REM disconnection-time semantics:
REM   * MaxIdleTime          0 = no idle timeout (active sessions never auto-disconnect)
REM   * MaxConnectionTime    0 = no max session duration
REM   * MaxDisconnectionTime 30000 ms = 30 sec - disconnected sessions
REM     auto-LOGOFF after 30 s. Previously this was 0 ("never logoff"),
REM     which left zombie disconnected sessions accumulating every time
REM     the user closed a FreeRDP window. The next launch then triggered
REM     "Select a session to reconnect to" dialog because Windows saw
REM     the user had N old disconnected sessions. rdprrap allows
REM     concurrent sessions but doesn't suppress that prompt - only
REM     auto-logoff does.
REM Both the machine policy and the RDP-Tcp WinStation keys are set;
REM whichever Windows consults first finds the saner default.
echo [WinPodX] Disabling RDP session timeouts + enabling keep-alive...
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v MaxIdleTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v MaxDisconnectionTime /t REG_DWORD /d 30000 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v MaxConnectionTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v KeepAliveEnable /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services" /v KeepAliveInterval /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxIdleTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxDisconnectionTime /t REG_DWORD /d 30000 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v MaxConnectionTime /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp" /v KeepAliveTimeout /t REG_DWORD /d 1 /f >nul 2>&1

echo [WinPodX] Configuring firewall...
REM Delete-then-add keeps the rule idempotent; plain add creates duplicates on re-run.
netsh advfirewall firewall set rule group="Remote Desktop" new enable=yes 2>nul
netsh advfirewall firewall delete rule name="RDP TCP" >nul 2>&1
netsh advfirewall firewall delete rule name="RDP UDP" >nul 2>&1
netsh advfirewall firewall add rule name="RDP TCP" dir=in action=allow protocol=tcp localport=3389 2>nul
netsh advfirewall firewall add rule name="RDP UDP" dir=in action=allow protocol=udp localport=3389 2>nul

REM Allow inbound on the winpodx agent port. QEMU's user-mode NAT forwards
REM container:8765 -> Windows VM 10.0.2.15:8765, which Windows Firewall
REM blocks by default - kernalix7 saw curl timeout from the host on
REM 2026-04-30 even with agent.ps1 bound to 0.0.0.0:8765 because the SYN
REM never made it past the firewall. RDP got auto-allowed via the
REM "Remote Desktop" group rule above; 8765 needs an explicit rule.
netsh advfirewall firewall delete rule name="winpodx-agent" >nul 2>&1
netsh advfirewall firewall add rule name="winpodx-agent" dir=in action=allow protocol=tcp localport=8765 2>nul

echo [WinPodX] Optimizing for RDP...
reg add "HKCU\Control Panel\Desktop" /v DragFullWindows /t REG_SZ /d 0 /f
reg add "HKCU\Control Panel\Desktop" /v MenuShowDelay /t REG_SZ /d 0 /f
reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" /v VisualFXSetting /t REG_DWORD /d 2 /f

REM Pin Windows build so termsrv.dll stays stable; feature/build upgrades come via winpodx releases only.
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v TargetReleaseVersion /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v ProductVersion /t REG_SZ /d "Windows 11" /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v TargetReleaseVersionInfo /t REG_SZ /d "25H2" /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v DeferFeatureUpdates /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /v DeferFeatureUpdatesPeriodInDays /t REG_DWORD /d 365 /f

REM Timezone is handled upstream now: winpodx sets the dockur TZ env var
REM in compose.yaml from cfg.pod.timezone (or host autodetect), and
REM dockur writes the <TimeZone> element into the Sysprep unattend.xml
REM on first boot. No tzutil call needed here -- Windows reads the
REM Sysprep value during OOBE. Leaving this block as a no-op marker so
REM the OEM script structure stays predictable across releases.

reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Windows Search" /v AllowCortana /t REG_DWORD /d 0 /f

reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" /v AllowTelemetry /t REG_DWORD /d 0 /f

sc config WSearch start= disabled
net stop WSearch 2>nul

sc config SysMain start= disabled
net stop SysMain 2>nul

powercfg /h off

REM Print Spooler stays enabled for RDP printer redirection.

sc config WerSvc start= disabled
net stop WerSvc 2>nul

sc config DiagTrack start= disabled
net stop DiagTrack 2>nul

powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c

REM Force every idle timeout to "never" so the RDP service / virtio NIC
REM don't get suspended out from under the host while the user is on a
REM coffee break. Without this the host pod_status() probe sees RDP
REM unreachable on a healthy container and shows "starting" forever
REM (#TBD, observed by kernalix7).
powercfg /change standby-timeout-ac 0
powercfg /change standby-timeout-dc 0
powercfg /change hibernate-timeout-ac 0
powercfg /change hibernate-timeout-dc 0
powercfg /change monitor-timeout-ac 0
powercfg /change monitor-timeout-dc 0
powercfg /change disk-timeout-ac 0
powercfg /change disk-timeout-dc 0
REM Modern Standby (S0 low-power idle) -- even with the timeouts above
REM it can drop the NIC. The platform role registry key forces
REM Desktop class so Modern Standby logic is bypassed.
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Power" /v PlatformAoAcOverride /t REG_DWORD /d 0 /f
REM Belt-and-braces: disable hardware-initiated wake / sleep transitions
REM via the AC profile too.
powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 0 2>nul
powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 0 2>nul
powercfg /setactive SCHEME_CURRENT 2>nul

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize" /v StartupDelayInMSec /t REG_DWORD /d 0 /f

reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\BackgroundAccessApplications" /v GlobalUserDisabled /t REG_DWORD /d 1 /f

echo [WinPodX] Home folder is available at \\tsclient\home via RDP drive redirection

REM dockur's base image ships a "Shared" desktop item pointing to \\host.lan\Data (SMB) that we don't use; replace with Home/USB shortcuts to the RDP redirections.
echo [WinPodX] Creating desktop shortcuts to tsclient shares...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$d=[Environment]::GetFolderPath('Desktop'); foreach($n in 'Shared','Shared.lnk'){ $p=Join-Path $d $n; if(Test-Path -LiteralPath $p){ Remove-Item -Force -Recurse -LiteralPath $p -ErrorAction SilentlyContinue } }; $s=New-Object -ComObject WScript.Shell; foreach($x in @(@('Home','\\tsclient\home'), @('USB','\\tsclient\media'))){ $l=$s.CreateShortcut((Join-Path $d ($x[0]+'.lnk'))); $l.TargetPath=$x[1]; $l.Save() }"

echo [WinPodX] Setting up USB media auto-mapping...
mkdir C:\winpodx 2>nul

REM Prefer compose-mounted C:\winpodx-scripts; fall back to well-known install locations over \\tsclient\home. See config/oem/README.md.
set "WINPODX_SRC_OK="
if exist "C:\winpodx-scripts\media_monitor.ps1" (
    copy /Y "C:\winpodx-scripts\media_monitor.ps1" C:\winpodx\media_monitor.ps1 >nul 2>&1
    set "WINPODX_SRC_OK=1"
)
if not defined WINPODX_SRC_OK (
    for %%P in (
        "\\tsclient\home\.local\share\winpodx\scripts\windows\media_monitor.ps1"
        "\\tsclient\home\.local\pipx\venvs\winpodx\share\winpodx\scripts\windows\media_monitor.ps1"
        "\\tsclient\home\winpodx\scripts\windows\media_monitor.ps1"
        "\\tsclient\home\.local\bin\winpodx-app\scripts\windows\media_monitor.ps1"
    ) do (
        if not defined WINPODX_SRC_OK if exist %%P (
            copy /Y %%P C:\winpodx\media_monitor.ps1 >nul 2>&1
            if not errorlevel 1 set "WINPODX_SRC_OK=1"
        )
    )
)
if not defined WINPODX_SRC_OK (
    echo [WinPodX] WARNING: media_monitor.ps1 not found in any known location.
    echo [WinPodX] Mount the scripts dir at C:\winpodx-scripts via compose, or
    echo [WinPodX] place media_monitor.ps1 under ~/.local/share/winpodx/scripts/windows/.
)
REM WinpodxMedia HKCU\Run registration moved later in install.bat -- the
REM same PowerShell block that registers WinpodxAgent now writes both
REM entries with shared launcher-existence gating + setup.log diagnostics.
REM See "Registering HKCU\Run entries" below.

REM Clean up any legacy OEM updater task / file from pre-0.1.6 installs.
schtasks /delete /tn "WinpodxOEMUpdate" /f >nul 2>&1
if exist C:\winpodx\oem_updater.ps1 del /F /Q C:\winpodx\oem_updater.ps1 >nul 2>&1

REM Parenthesized echo strips the trailing space that `echo X > file` leaves behind.
(echo %WINPODX_OEM_VERSION%)>C:\winpodx\oem_version.txt

echo [WinPodX] Installing multi-session RDP (rdprrap) - offline bundle...
REM Bundle ships under config/oem/ and is staged into C:\OEM\ by dockur's unattended install.
REM The pin file (version / filename / sha256) lives next to the zip. No network
REM access is required - everything is copied straight from the staged folder.
set "RDPRRAP_PIN="
if exist "C:\OEM\rdprrap_version.txt" set "RDPRRAP_PIN=C:\OEM\rdprrap_version.txt"

set "RDPRRAP_VERSION="
set "RDPRRAP_FILENAME="
set "RDPRRAP_SHA256="
if defined RDPRRAP_PIN (
    for /f "usebackq tokens=1,* delims==" %%A in ("%RDPRRAP_PIN%") do (
        if /I "%%A"=="version"  set "RDPRRAP_VERSION=%%B"
        if /I "%%A"=="filename" set "RDPRRAP_FILENAME=%%B"
        if /I "%%A"=="sha256"   set "RDPRRAP_SHA256=%%B"
    )
)

if not defined RDPRRAP_VERSION  goto :rdprrap_skip
if not defined RDPRRAP_FILENAME goto :rdprrap_skip
if not defined RDPRRAP_SHA256   goto :rdprrap_skip

set "RDPRRAP_DIR=C:\winpodx\rdprrap"
set "RDPRRAP_INSTALLED=%RDPRRAP_DIR%\.installed_version"
set "RDPRRAP_CUR="
if exist "%RDPRRAP_INSTALLED%" (
    for /f "usebackq delims=" %%V in ("%RDPRRAP_INSTALLED%") do set "RDPRRAP_CUR=%%V"
)
REM Check sits on its own line so %RDPRRAP_CUR% expands AFTER the for-loop above (no delayed expansion needed).
if defined RDPRRAP_CUR if /I "%RDPRRAP_CUR%"=="%RDPRRAP_VERSION%" (
    echo [WinPodX] rdprrap %RDPRRAP_VERSION% already installed, skipping.
    goto :rdprrap_done
)

REM Locate the bundled zip inside the OEM staging folder.
set "RDPRRAP_ZIP_SRC="
if exist "C:\OEM\%RDPRRAP_FILENAME%" set "RDPRRAP_ZIP_SRC=C:\OEM\%RDPRRAP_FILENAME%"
if not defined RDPRRAP_ZIP_SRC (
    echo [WinPodX] WARNING: bundled %RDPRRAP_FILENAME% not found at C:\winpodx or C:\OEM; staying single-session.
    goto :rdprrap_done
)

REM certutil prints 3 lines; line 2 has the hex digest with spaces between bytes.
set "RDPRRAP_GOT="
for /f "usebackq skip=1 delims=" %%H in (`certutil -hashfile "%RDPRRAP_ZIP_SRC%" SHA256 ^| findstr /R "^[0-9a-fA-F ]*$"`) do (
    if not defined RDPRRAP_GOT set "RDPRRAP_GOT=%%H"
)
set "RDPRRAP_GOT=%RDPRRAP_GOT: =%"
if /I not "%RDPRRAP_GOT%"=="%RDPRRAP_SHA256%" (
    echo [WinPodX] WARNING: rdprrap sha256 mismatch on bundle; staying single-session.
    echo [WinPodX]   expected %RDPRRAP_SHA256%
    echo [WinPodX]   got      %RDPRRAP_GOT%
    goto :rdprrap_done
)

mkdir "%RDPRRAP_DIR%" 2>nul

REM --- Diagnostic logs ------------------------------------------------------
REM Status marker (one-line classification, machine-readable):
REM   enabled / extract-failed / installer-failed / not-activated / missing-bundle
REM Detailed log (full timestamps + retry-by-retry stderr/stdout) so when
REM something fails users / `winpodx pod apply-fixes` have something to
REM root-cause from. The marker fits a single grep, the log is the deep dive.
set "RDPRRAP_STATUS=C:\winpodx\rdprrap\.activation_status"
set "RDPRRAP_LOG=C:\winpodx\rdprrap\install.log"
(echo === rdprrap install log === %DATE% %TIME%) > "%RDPRRAP_LOG%"
(echo version=%RDPRRAP_VERSION%)>>"%RDPRRAP_LOG%"
(echo bundle=%RDPRRAP_ZIP_SRC%)>>"%RDPRRAP_LOG%"

REM --- Extract with retries + per-attempt log ------------------------------
REM Expand-Archive occasionally fails on first-boot Sysprep with file-lock /
REM antivirus interference. Retry up to 3 times. Capture stderr each
REM attempt so a final extract-failed has actionable diagnostics.
REM v0.4.0 (post-rc1): tar -xf instead of PS Expand-Archive.
REM
REM Background: PS Expand-Archive deadlocked on every fresh install
REM 2026-05-02 through 2026-05-04. install.log ended at "extract attempt
REM 1" with no follow-up - neither "extract OK" nor "extract failed" -
REM meaning the PowerShell call never returned. The 3 disconnected
REM User sessions in qwinsta on every attempt were the eventual kicks
REM (host-side migrate's password probe at 12:45 UTC) that finally
REM killed the hanging install.bat. PS Expand-Archive was hanging on
REM Defender's real-time scan of C:\OEM\ + the rdprrap zip extraction
REM target - one of the staged PS / EXE files (rdprrap-installer.exe
REM is naturally PUP-flagged because it patches termsrv.dll) was
REM getting locked, and Expand-Archive blocked waiting on the lock.
REM
REM tar (Windows 10 1803+, ships in System32\tar.exe) bypasses the
REM PowerShell engine entirely - no module load, no .NET assembly
REM resolution, no AMSI hookpoints. It's a syscall-direct extraction
REM that Defender's PS-script analysis layer can't intercept. The
REM Defender exclusions added at the top of install.bat (Add-MpPreference
REM -ExclusionPath C:\OEM,C:\winpodx) are also in effect by this point,
REM so even the EXE path is exempt.
REM
REM Output flatten: tar's -C strips the archive's leading directory by
REM default for our zip layout, so the inner rdprrap-<version>/ folder
REM that Expand-Archive used to leave behind isn't present after tar
REM extraction. We still defensively check + flatten via cmd's MOVE /Y
REM in case the archive layout changes.
echo [WinPodX] Extracting rdprrap %RDPRRAP_VERSION%...
set "RDPRRAP_EXTRACTED="
for %%T in (1 2 3) do (
    if not defined RDPRRAP_EXTRACTED (
        (echo --- extract attempt %%T %DATE% %TIME%)>>"%RDPRRAP_LOG%"
        "%SystemRoot%\System32\tar.exe" -xf "%RDPRRAP_ZIP_SRC%" -C "%RDPRRAP_DIR%" >>"%RDPRRAP_LOG%" 2>&1
        if not errorlevel 1 set "RDPRRAP_EXTRACTED=1"
        if not defined RDPRRAP_EXTRACTED (
            (echo extract attempt %%T failed; sleeping 2s)>>"%RDPRRAP_LOG%"
            echo [WinPodX]   extract attempt %%T failed, retrying after 2s...
            REM Plain timeout instead of `powershell Start-Sleep` - PS
            REM call here would re-introduce the very engine load that
            REM tar is bypassing.
            timeout /t 2 /nobreak >nul 2>&1
        )
    )
)
if not defined RDPRRAP_EXTRACTED (
    (echo FINAL: extract-failed)>>"%RDPRRAP_LOG%"
    echo [WinPodX] WARNING: rdprrap extraction failed after 3 attempts; staying single-session.
    echo [WinPodX]   diagnostic log: %RDPRRAP_LOG%
    (echo extract-failed)>"%RDPRRAP_STATUS%"
    goto :rdprrap_done
)
(echo extract OK via tar)>>"%RDPRRAP_LOG%"

REM Defensive flatten: if tar left an inner rdprrap-<version>/ folder
REM (depends on the zip's layout convention), move its contents up to
REM RDPRRAP_DIR. Single shot, no PS - pure cmd. Idempotent: the for
REM loop simply finds no match if there's no inner dir.
for /d %%D in ("%RDPRRAP_DIR%\rdprrap-*") do (
    (echo flattening inner dir: %%~nxD)>>"%RDPRRAP_LOG%"
    xcopy /E /Y /Q "%%D\*" "%RDPRRAP_DIR%\" >>"%RDPRRAP_LOG%" 2>&1
    rmdir /S /Q "%%D" >>"%RDPRRAP_LOG%" 2>&1
)

set "RDPRRAP_EXE=%RDPRRAP_DIR%\rdprrap-installer.exe"
if not exist "%RDPRRAP_EXE%" (
    (echo FINAL: extract-failed - rdprrap-installer.exe missing after extract)>>"%RDPRRAP_LOG%"
    echo [WinPodX] WARNING: rdprrap-installer.exe missing after extract; staying single-session.
    echo [WinPodX]   diagnostic log: %RDPRRAP_LOG%
    (echo extract-failed)>"%RDPRRAP_STATUS%"
    goto :rdprrap_done
)

REM --- Delegate install / TermService cycle / verify to rdprrap-activate.ps1
REM Single source of truth - same script `winpodx pod multi-session on`
REM uses for runtime activation. install.bat used to inline ~80 lines of
REM installer-retry / TermService-cycle / ServiceDll-verify / marker logic;
REM that code now lives in rdprrap-activate.ps1 so a fix to the activation
REM flow benefits both OEM-time and runtime paths without drift.
REM
REM Safety at OEM time: install.bat runs from FirstLogonCommands in the
REM local console session - TermService manages RDP sessions only, so the
REM cycle inside the script doesn't tear down our cmd.exe parent.
REM
REM The script writes the same .activation_status / install.log markers
REM install.bat used to write directly. Idempotency marker
REM (.installed_version) stays install.bat's responsibility - only OEM
REM time has the SHA-pinned bundle context to safely stamp it.
echo [WinPodX] Activating rdprrap (delegating to rdprrap-activate.ps1)...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0rdprrap-activate.ps1" >>"%RDPRRAP_LOG%" 2>&1
set "RDPRRAP_RC=%ERRORLEVEL%"
(echo rdprrap-activate.ps1 exit=%RDPRRAP_RC%)>>"%RDPRRAP_LOG%"
if not "%RDPRRAP_RC%"=="0" (
    echo [WinPodX] WARNING: rdprrap-activate.ps1 exited with code %RDPRRAP_RC%; staying single-session.
    echo [WinPodX]   diagnostic log: %RDPRRAP_LOG%
    goto :rdprrap_done
)

(echo %RDPRRAP_VERSION%)>"%RDPRRAP_INSTALLED%"
echo [WinPodX] rdprrap %RDPRRAP_VERSION% installed and activated.
goto :rdprrap_done

:rdprrap_skip
echo [WinPodX] rdprrap_version.txt not found or incomplete; staying single-session.
:rdprrap_done

REM ---------------------------------------------------------------------
REM v0.2.2-rev1: winpodx guest HTTP agent
REM ---------------------------------------------------------------------
echo [WinPodX] Installing WinPodX guest agent...
copy /Y "%~dp0agent\agent.ps1" "C:\OEM\agent.ps1" 2>nul
mkdir C:\OEM\agent-runs 2>nul

REM Hidden launchers go under C:\Users\Public\winpodx\launchers\ rather than
REM C:\OEM\. Public is universally writable for Authenticated Users, so the
REM agent (which runs as User, non-admin) can later overwrite these files
REM during a `winpodx pod apply-fixes` migration without needing UAC. C:\OEM\
REM is SYSTEM-owned and rejects User writes by default.
REM
REM Diagnostic log (`C:\winpodx\setup.log`) records each copy + verify
REM step. Pre-OEM-v20 the copies were silent (`2>nul`), so when a
REM Sysprep-time failure (network share blip, AV interference) skipped a
REM file, the missing-file symptom only surfaced later as a "Cannot find
REM script file" wscript dialog at HKCU\Run firing time -- with no
REM breadcrumbs. With this log + per-file existence check below, a
REM staging failure now writes a single line we can grep from the host
REM via the agent.
mkdir C:\winpodx 2>nul
set "SETUP_LOG=C:\winpodx\setup.log"
(echo === setup log === %DATE% %TIME%) > "%SETUP_LOG%"
(echo OEM_VERSION=%WINPODX_OEM_VERSION%)>>"%SETUP_LOG%"
(echo install.bat dir=%~dp0)>>"%SETUP_LOG%"

mkdir "C:\Users\Public\winpodx" 2>nul
mkdir "C:\Users\Public\winpodx\launchers" 2>nul

REM Each launcher: copy, verify the file exists at its destination, log
REM the outcome. Continuing on failure (so a single bad copy doesn't
REM abort the rest of install.bat) but recording it for diagnostics.
REM `LAUNCHERS_OK=1` after the loop means hidden-launcher.vbs (the
REM critical wrapper) made it to its destination -- gates the wscript
REM reg add below.
set "LAUNCHERS_OK="
for %%F in (
    "hidden-launcher.vbs"
    "launch_uwp.vbs"
    "launch_uwp.ps1"
    "agent-respawn.ps1"
    "agent-keepalive.ps1"
    "rdprrap-activate.ps1"
) do (
    copy /Y "%~dp0%%~F" "C:\Users\Public\winpodx\launchers\%%~F" >nul 2>>"%SETUP_LOG%"
    if exist "C:\Users\Public\winpodx\launchers\%%~F" (
        (echo launcher OK: %%~F)>>"%SETUP_LOG%"
    ) else (
        (echo launcher FAILED: %%~F src=%~dp0%%~F not staged or copy denied)>>"%SETUP_LOG%"
    )
)
if exist "C:\Users\Public\winpodx\launchers\hidden-launcher.vbs" set "LAUNCHERS_OK=1"
(echo launchers gate: LAUNCHERS_OK=%LAUNCHERS_OK%)>>"%SETUP_LOG%"

REM Pre-register the URL ACL for agent.ps1's HttpListener prefix.
REM
REM agent.ps1 binds ``http://+:8765/`` (all interfaces inside the
REM Windows VM) - NOT 127.0.0.1. dockur's user-mode QEMU NAT forwards
REM from container:8765 to the VM's slirp interface (10.0.2.15:8765,
REM NOT 127.0.0.1:8765); a 127.0.0.1-only listener would mean slirp's
REM forwarded packets hit a closed port (kernalix7 saw "Connection
REM reset by peer" on 2026-04-30 from exactly this). Binding to + is
REM safe because the agent stays externally unreachable: compose's
REM 127.0.0.1:8765:8765/tcp mapping is loopback on the host, and the
REM QEMU slirp net is private to the container.
REM
REM HttpListener.Start() needs a urlacl entry to bind ``+``; without
REM it the bind fails with "conflicts with an existing registration on
REM the machine" because a non-admin process (the autologon User the
REM agent runs as) cannot reserve a strong-wildcard prefix without a
REM pre-existing reservation it is permitted to use.
REM
REM #269 (ismikes, Kubuntu 26.04): agent.log showed the bind failing
REM with exactly that conflict on every boot even though install.bat
REM ran to completion. Root cause: the prior `user=Everyone` form +
REM hidden `>nul 2>&1` masked a failed / locale-dependent reservation.
REM Hardened below:
REM   - sddl=D:(A;;GX;;;WD) reserves for the World (Everyone) SID
REM     S-1-1-0 directly, locale-independent (the literal string
REM     "Everyone" is localized on non-English Windows and the netsh
REM     parse silently no-ops there). GX = GENERIC_EXECUTE = the
REM     register/listen right HttpListener needs.
REM   - delete every overlapping 8765 reservation (strong +, weak *,
REM     loopback) first so a stale differently-owned entry can't win.
REM   - results logged to setup.log (not >nul) so a failure is visible.
REM   - show urlacl after, so the actual post-state is in the log.
echo [agent-install] step=urlacl status=enter>>"%SETUP_LOG%"
netsh http delete urlacl url=http://127.0.0.1:8765/ >>"%SETUP_LOG%" 2>&1
netsh http delete urlacl url=http://*:8765/ >>"%SETUP_LOG%" 2>&1
netsh http delete urlacl url=http://+:8765/ >>"%SETUP_LOG%" 2>&1
netsh http add urlacl url=http://+:8765/ sddl="D:(A;;GX;;;WD)" >>"%SETUP_LOG%" 2>&1
echo [agent-install] urlacl add rc=%ERRORLEVEL%>>"%SETUP_LOG%"
netsh http show urlacl url=http://+:8765/ >>"%SETUP_LOG%" 2>&1
echo [agent-install] step=urlacl status=exit>>"%SETUP_LOG%"

REM Register HKCU\Run via PowerShell instead of `reg add`. Three reasons
REM the cmd-quoting path bit users pre-OEM-v20:
REM   1. `reg add /d "...\\\"path\\\"..."` survives cmd parsing but
REM      reg.exe stores literal backslash-quote pairs in the registry,
REM      and CommandLineToArgvW at logon evaluates them as escaped
REM      quotes -- not always argv-equivalent to the intended quoted
REM      argument. PS's Set-ItemProperty takes a real .NET string and
REM      stores it byte-exact.
REM   2. PS can verify hidden-launcher.vbs exists *before* registering
REM      the wscript-wrapped value -- if the launcher staging copy
REM      failed (logged via setup.log above, LAUNCHERS_OK unset), we
REM      fall back to direct `powershell.exe -WindowStyle Hidden` for
REM      WinpodxAgent. That fallback flashes briefly but at least
REM      starts the agent; without it, HKCU\Run fired wscript on a
REM      missing file -> "Cannot find script file" dialog blocking
REM      the user session indefinitely (kernalix7 saw this on a fresh
REM      install 2026-05-02 18:48).
REM   3. Single PS round-trip writes both WinpodxAgent and WinpodxMedia,
REM      replacing the two separate reg-add lines + the WinpodxMedia
REM      special case below.
echo [WinPodX] Registering HKCU\Run entries...
echo [agent-install] step=hkcu-run-register status=enter>>"%SETUP_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wrap = 'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs';" ^
  "$haveWrap = Test-Path -LiteralPath $wrap;" ^
    "Write-Output (\"reg-add: haveWrap=$haveWrap\");" ^
  "$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run';" ^
  "if ($haveWrap) {" ^
  "  $agent = 'wscript.exe \"' + $wrap + '\" \"powershell.exe\" \"-NoProfile\" \"-ExecutionPolicy\" \"Bypass\" \"-File\" \"C:\OEM\agent.ps1\"';" ^
  "  $media = 'wscript.exe \"' + $wrap + '\" \"powershell.exe\" \"-NoProfile\" \"-ExecutionPolicy\" \"Bypass\" \"-File\" \"C:\winpodx\media_monitor.ps1\"';" ^
  "} else {" ^
    "  Write-Output 'reg-add: hidden-launcher.vbs missing -> fallback to direct powershell (brief flash)';" ^
  "  $agent = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\OEM\agent.ps1';" ^
  "  $media = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\winpodx\media_monitor.ps1';" ^
  "}" ^
  "try {" ^
  "  Set-ItemProperty -Path $key -Name 'WinpodxAgent' -Value $agent -Force;" ^
  "  Set-ItemProperty -Path $key -Name 'WinpodxMedia' -Value $media -Force;" ^
    "  Write-Output ('reg-add: WinpodxAgent=' + $agent);" ^
    "  Write-Output ('reg-add: WinpodxMedia=' + $media);" ^
  "} catch {" ^
    "  Write-Output ('reg-add: ERROR ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message);" ^
  "}" >>"%SETUP_LOG%" 2>&1
echo [agent-install] step=hkcu-run-register status=exit rc=%ERRORLEVEL%>>"%SETUP_LOG%"

REM Start the agent NOW (install.bat-time spawn) -- not just register it
REM in HKCU\Run for future sessions. Reasoning: HKCU\Run fires once per
REM user logon, and the autologon User session has *already* logged in
REM by the time install.bat (FirstLogonCommands) executes. Registering
REM HKCU\Run here only takes effect on the NEXT session, so without
REM this explicit spawn the agent sits idle until the user (or a host
REM RDP probe) opens a new session. install.sh's wait-ready phase 3
REM was timing out at /health waiting for an agent that wasn't going
REM to start until much later. spawn here -> agent /health up before
REM install.bat exits, phase 3 succeeds cleanly, migrate's apply chain
REM runs against a healthy agent (no FreeRDP-fallback cascades).
REM
REM Same wscript+hidden-launcher.vbs wrapper / direct-PS fallback
REM split as the HKCU\Run registration above. Spawned via
REM Start-Process detached so install.bat doesn't block on the agent's
REM event loop.
echo [WinPodX] Starting guest agent...
echo [agent-install] step=spawn status=enter>>"%SETUP_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wrap = 'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs';" ^
  "$agentScript = 'C:\OEM\agent.ps1';" ^
  "Write-Output ('agent-spawn: wrap=' + $wrap + ' wrapExists=' + (Test-Path -LiteralPath $wrap));" ^
  "Write-Output ('agent-spawn: agentScript=' + $agentScript + ' scriptExists=' + (Test-Path -LiteralPath $agentScript));" ^
  "try {" ^
  "  if (Test-Path -LiteralPath $wrap) {" ^
  "    $startArgs = @($wrap, 'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $agentScript);" ^
  "    $p = Start-Process wscript.exe -ArgumentList $startArgs -WindowStyle Hidden -PassThru;" ^
    "    Write-Output ('agent-spawn: wscript+hidden-launcher.vbs pid=' + $p.Id);" ^
  "  } else {" ^
  "    $p = Start-Process powershell.exe -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $agentScript) -WindowStyle Hidden -PassThru;" ^
    "    Write-Output ('agent-spawn: direct-powershell-fallback pid=' + $p.Id);" ^
  "  }" ^
  "} catch {" ^
    "  Write-Output ('agent-spawn: ERROR ' + $_.Exception.GetType().FullName + ': ' + $_.Exception.Message);" ^
    "}" >>"%SETUP_LOG%" 2>&1
echo [agent-install] step=spawn status=exit rc=%ERRORLEVEL%>>"%SETUP_LOG%"

REM Quick post-spawn health probe -- give the agent 5s to bind 8765,
REM then log whether the listener is up. If it's not, we know agent.ps1
REM either failed to start or crashed before HttpListener.Start(), and
REM the user / next debugger has a clear breadcrumb without needing
REM to chase Start-Process exit codes.
echo [agent-install] step=post-spawn-probe status=enter>>"%SETUP_LOG%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Sleep -Seconds 5;" ^
  "$listener = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue;" ^
  "if ($listener) {" ^
  "  Write-Output ('post-spawn-probe: 8765 listener up (PID ' + ($listener | Select-Object -First 1 -ExpandProperty OwningProcess) + ')');" ^
  "} else {" ^
    "  Write-Output 'post-spawn-probe: 8765 NOT listening 5s after spawn';" ^
    "  Get-ChildItem C:\OEM\agent.log -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ('post-spawn-probe: agent.log size=' + $_.Length) };" ^
    "  Get-Content C:\OEM\agent.log -Tail 20 -ErrorAction SilentlyContinue | ForEach-Object { Write-Output ('agent.log: ' + $_) };" ^
    "}" >>"%SETUP_LOG%" 2>&1
echo [agent-install] step=post-spawn-probe status=exit rc=%ERRORLEVEL%>>"%SETUP_LOG%"

REM ---------------------------------------------------------------------
REM Agent keep-alive scheduled task (WinpodxAgentKeepAlive).
REM
REM HKCU\Run fires the agent exactly once per interactive logon. When the
REM autologon session is torn down (RDP single-session enforcement kicks
REM it when a FreeRDP connection arrives before rdprrap multi-session is
REM active, or a TermService cycle during rdprrap (re)activation), the
REM agent process dies with the session and HKCU\Run does NOT re-fire --
REM the agent stays dead until the pod reboots. This task is the
REM persistent watchdog HKCU\Run never was.
REM
REM Principal: the INTERACTIVE autologon user, NOT SYSTEM / S4U. The
REM agent's /exec runs PowerShell in the user context (Start Menu / per-
REM user app discovery, per-user reverse-open HKCU registration); a
REM SYSTEM principal would change HKCU + Start Menu out from under those
REM callers. A user-context task only runs while a session exists, which
REM covers crash-but-alive (1-min repetition) and re-logon (AtLogOn). The
REM session-kick-with-no-relogon case is handled by keeping rdprrap
REM activation idempotent so the kick does not happen (see
REM _apply_multi_session / rdprrap-activate.ps1).
REM
REM agent-keepalive.ps1 is staged to C:\winpodx (survives the C:\OEM wipe
REM on classic VMs, same as power-monitor.ps1) and runs through the
REM wscript hidden-launcher wrapper so the 1-min wakeups never flash a
REM console. Registered via the ScheduledTasks cmdlets so we can attach
REM BOTH an AtLogOn trigger AND a 1-minute repetition (schtasks.exe can
REM only set one schedule per task).
echo [WinPodX] Registering agent keep-alive scheduled task...
echo [agent-install] step=keepalive-task status=enter>>"%SETUP_LOG%"
if exist "C:\Users\Public\winpodx\launchers\agent-keepalive.ps1" (
    if not exist C:\winpodx mkdir C:\winpodx
    copy /Y "C:\Users\Public\winpodx\launchers\agent-keepalive.ps1" C:\winpodx\agent-keepalive.ps1 >nul 2>&1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$wrap = 'C:\Users\Public\winpodx\launchers\hidden-launcher.vbs';" ^
  "$ka = 'C:\winpodx\agent-keepalive.ps1';" ^
  "if (-not (Test-Path -LiteralPath $ka)) { Write-Output 'keepalive: agent-keepalive.ps1 not staged; skipping'; exit 0 };" ^
  "if (Test-Path -LiteralPath $wrap) {" ^
  "  $exe = 'wscript.exe';" ^
  "  $arg = '\"' + $wrap + '\" \"powershell.exe\" \"-NoProfile\" \"-ExecutionPolicy\" \"Bypass\" \"-File\" \"' + $ka + '\"';" ^
  "} else {" ^
  "  $exe = 'powershell.exe';" ^
  "  $arg = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $ka + '\"';" ^
  "}" ^
  "$act = New-ScheduledTaskAction -Execute $exe -Argument $arg;" ^
  "$tLogon = New-ScheduledTaskTrigger -AtLogOn;" ^
  "$tRepeat = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 1);" ^
  "$me = \"$env:USERDOMAIN\$env:USERNAME\";" ^
  "$prin = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Limited;" ^
  "$set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2);" ^
  "try {" ^
  "  Register-ScheduledTask -TaskName 'WinpodxAgentKeepAlive' -Action $act -Trigger @($tLogon,$tRepeat) -Principal $prin -Settings $set -Force | Out-Null;" ^
  "  Write-Output ('keepalive: registered for ' + $me);" ^
  "} catch {" ^
  "  Write-Output ('keepalive: ERROR ' + $_.Exception.Message);" ^
  "}" >>"%SETUP_LOG%" 2>&1
echo [agent-install] step=keepalive-task status=exit rc=%ERRORLEVEL%>>"%SETUP_LOG%"

REM Token is delivered via the OEM bind mount - no \\tsclient\home copy
REM needed. Setup stages it to {oem_dir}/agent_token.txt before container
REM creation; dockur lays the OEM directory contents into C:\OEM\.
echo [WinPodX] Guest agent installed.

REM Stage power-monitor.ps1 under C:\winpodx (C:\OEM is wiped after
REM first boot, so anything we want to keep needs a copy outside it).
REM Then register a SYSTEM-level scheduled task that runs the monitor
REM at boot; the script subscribes to Win32_PowerManagementEvent and
REM cycles TermService when the host suspends/resumes (which the guest
REM only sees as a wall-clock jump, leaving the RDP TCP listener
REM stale -- kernalix7's recurring "GUI stuck on starting after host
REM wake" symptom).
if exist C:\OEM\power-monitor.ps1 (
    if not exist C:\winpodx mkdir C:\winpodx
    copy /Y C:\OEM\power-monitor.ps1 C:\winpodx\power-monitor.ps1 >nul 2>&1
    schtasks /create /tn "winpodx-power-monitor" ^
        /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\winpodx\power-monitor.ps1" ^
        /sc onstart /ru SYSTEM /rl HIGHEST /f >nul 2>&1
    REM Kick the task immediately so the WMI subscription is live for
    REM this session too -- otherwise the very first host suspend
    REM after install (before the next guest reboot) goes unhandled.
    schtasks /run /tn "winpodx-power-monitor" >nul 2>&1
    echo [WinPodX] Guest power monitor scheduled.
)

REM Sentinel lives under C:\winpodx so it survives past the one-shot C:\OEM stage.
(echo done)>C:\winpodx\setup_done.txt

echo [WinPodX] Post-install configuration complete (version %WINPODX_OEM_VERSION%)!
echo [WinPodX] RDP is now enabled. You can connect with FreeRDP.

REM ---------------------------------------------------------------------------
REM TermService cycle -- ABSOLUTELY LAST STEP.
REM
REM This restarts TermService so the running process picks up the
REM termwrap.dll patched into HKLM:\...\TermService\Parameters\ServiceDll
REM by rdprrap-activate.ps1 (synchronous OEM-mode call earlier in this
REM script). rdprrap-activate.ps1 deliberately skips the cycle in OEM
REM mode (marker = patched-pending-cycle) BECAUSE this cycle is what
REM has been killing install.bat mid-script -- in dockur's setup the
REM autologon User session is itself managed through TermService, so
REM `net stop TermService /y` takes our cmd.exe down with it.
REM
REM By doing the cycle as the very last action, we have already
REM committed:
REM   - launcher staging into C:\Users\Public\winpodx\launchers\
REM   - HKCU\Run\WinpodxAgent (registered via PowerShell)
REM   - inline agent spawn (so it ran with the now-stale TermService;
REM     it'll die with this cycle, then HKCU\Run brings it back in
REM     the autologon-retry session)
REM   - C:\winpodx\setup_done.txt sentinel
REM   - rdprrap-installer registry patch (done by rdprrap-activate.ps1)
REM
REM So even when this cycle kills install.bat's session, the autologon
REM retry creates a fresh user session with multi-session active and
REM HKCU\Run firing -- that brings the agent up cleanly.
REM
REM We still try to update the .activation_status marker after the
REM cycle so apply-fixes doesn't have to re-trigger activation. If
REM cmd.exe dies before reaching that, the marker stays at
REM `patched-pending-cycle` and the host's `_apply_multi_session`
REM ServiceDll cross-check (PR #85) will reconcile it to `enabled` on
REM next apply-fixes.
echo [WinPodX] Cycling TermService to load termwrap.dll (final step)...
net stop TermService /y >nul 2>&1
net start TermService >nul 2>&1

REM Best-effort marker update. May not run if the cycle killed our
REM session; that's fine -- apply-fixes reconciles via ServiceDll check.
(echo enabled)>C:\winpodx\rdprrap\.activation_status 2>nul

REM ---------------------------------------------------------------------------
REM Scheduled reboot -- ABSOLUTELY FINAL.
REM
REM Several registry edits set above (PlatformAoAcOverride for Modern
REM Standby, NIC binding, etc.) only take effect on the next Windows
REM boot. Without this reboot, a fresh-install guest is technically
REM running with the *old* Modern Standby state for the first session;
REM the host then sees the long-idle stall the powercfg /change
REM timeouts can't prevent on their own. The TermService cycle above
REM picks up termwrap.dll; the reboot below picks up everything that
REM needs a clean boot.
REM
REM Sequence:
REM   1. install.bat writes ``C:\winpodx\oem_reboot_pending.txt``.
REM   2. ``shutdown /r /t 15`` queues the reboot with a 15s grace
REM      window so cmd.exe can finish + the autologon User session
REM      doesn't fight the cycle.
REM   3. On the next Windows boot, the RunOnce key below fires
REM      ``del oem_reboot_pending.txt`` so the host can poll the
REM      marker's absence as the "reboot pass complete" signal.
REM   4. ``winpodx pod wait-ready`` adds a [4/4] step that polls
REM      the marker via the agent transport (or `\\tsclient\home`
REM      fallback) until it's gone.
REM
REM If shutdown fails for any reason, the marker is left behind --
REM apply-fixes treats this as "still need second-pass reboot" and
REM offers to retry. Failure mode is detectable, not silent.

REM -- winpodx bare-metal disguise: prune unused virtio driver service keys --
REM al-khaser flags Services\{viostor,vioscsi,BalloonService} as VM tells. The
REM virtio-win bundle installs them even with no matching device. The helper
REM removes only the keys whose device is absent (guarded so a virtio-boot or
REM ballooned guest keeps its driver). Runs before the reboot so the change is
REM applied on the clean boot below. No-op when the helper isn't shipped.
if exist "%~dp0disguise-cleanup.ps1" (
    echo [WinPodX] Pruning unused virtio driver service keys...
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0disguise-cleanup.ps1" >nul 2>&1
)

echo [WinPodX] Scheduling reboot to apply registry / power settings...
(echo pending)>C:\winpodx\oem_reboot_pending.txt 2>nul
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce" ^
    /v WinpodxClearOemRebootMarker /t REG_SZ ^
    /d "cmd.exe /c del /q C:\winpodx\oem_reboot_pending.txt" /f >nul
shutdown /r /t 15 /c "WinPodX: applying registry / power settings"
