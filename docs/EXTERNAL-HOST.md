# External Windows host

**English** | [한국어](EXTERNAL-HOST.ko.md)

WinPodX works best with its dockur-managed Windows guest. That is the recommended,
first-class path and the one covered by automated provisioning, migration, recovery,
multi-session setup, guest-agent updates, and reverse-open maintenance.

The `manual` backend can instead connect to an existing Windows 10 or Windows 11 Pro
machine or VM. This path is supported on a best-effort basis:

- RDP crosses your LAN or VPN instead of the managed guest's loopback connection, so
  latency and throughput can make apps feel less responsive.
- You own Windows availability, updates, RDP policy, firewall rules, the guest agent,
  optional multi-session support, and optional reverse-open registration.
- WinPodX does not provision, start, stop, migrate, or self-heal these guest components.

> **Two different meanings of "manual":** `[pod] backend = "manual"` means
> bring-your-own Windows. `install.sh --manual` only installs WinPodX while skipping its
> initial setup/provisioning run; it still targets the normal dockur workflow when setup
> is run later. The two features are unrelated.

## Before you begin

Use a supported Windows edition that can host Remote Desktop, a dedicated non-admin
Windows account for routine RDP use, and an isolated host-only network or authenticated
VPN between Linux and Windows. The current manual backend requires TLS security without
Network Level Authentication (NLA), which provides less pre-authentication protection. If
your policy requires NLA, do not use this path. Never publish RDP or the WinPodX agent
directly to the public Internet.

The commands below intentionally reproduce only the pieces needed for an external host.
**Do not run `config/oem/install.bat` wholesale.** That script is dockur first-boot
orchestration: it also changes DNS, Defender exclusions, Windows Update, services, power
policy, scheduled tasks, multi-session state, and reboot behavior.

## 1. Configure the manual backend

Run `winpodx setup --customize` and select the manual backend, or edit the configuration
file directly. Its default path is `~/.config/winpodx/winpodx.toml`; a custom
`XDG_CONFIG_HOME` replaces the `~/.config` prefix. The wizard records the host address and
credentials, but it does not ask for the external RDP port: it leaves the managed-guest
default of `3390`. It also leaves the default whole-home drive redirection in place. Enter
the existing Windows account password when prompted; pressing Enter generates a random
managed-guest password that the external host does not know.

After the wizard, set the stock Windows RDP port and restrict the guest-visible directory:

```bash
install -d -m 700 "$HOME/winpodx-share"
winpodx config set rdp.port 3389
winpodx config set pod.home_share "$HOME/winpodx-share"
```

For a direct edit, use the same values and mark setup complete. Replace the example home
path with your Linux account's actual absolute path, and create it with the `install`
command above before the first connection:

```toml
[pod]
backend = "manual"
initialized = true
home_share = "/home/you/winpodx-share"

[rdp]
ip = "192.0.2.20"       # Replace with the Windows host's private/VPN address.
port = 3389
user = "winpodx"
password = "CHANGE_ME"
```

Protect the configuration because it contains the RDP credential:

```bash
WINPODX_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/winpodx"
chmod 600 "$WINPODX_CONFIG_DIR/winpodx.toml"
```

An empty `pod.home_share` exposes the entire Linux `$HOME` to the Windows session with
read/write access, including SSH keys, browser profiles, shell startup files, and WinPodX
credentials. Keep the restricted mode above for an unmanaged external host. WinPodX also
passes the RDP password to FreeRDP as a process argument, so do not use this workflow on
an untrusted multi-user Linux machine.

The manual backend only checks `rdp.ip:rdp.port` and connects to it. Its start and stop
operations are no-ops; booting, suspending, recovering, and shutting down Windows remain
your responsibility.

## 2. Enable RDP and RemoteApp (RAIL)

On Windows, enable **Settings > System > Remote Desktop** and allow RDP only from the
Linux host over the isolated link or authenticated VPN. WinPodX currently starts FreeRDP
with `/sec:tls`, so the host
must accept TLS security without Network Level Authentication (NLA). Do not expose this
TLS-only endpoint to the public internet. In an elevated PowerShell window, keep TLS
enabled, disable NLA for this endpoint, and enable RAIL:

```powershell
$rail = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList'
$policy = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
$rdpTcp = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
$WinPodXUser = "$env:COMPUTERNAME\winpodx"
$LinuxHost = '192.0.2.10'
$TrustedInterface = 'Ethernet'
$NetworkCategory = (Get-NetConnectionProfile -InterfaceAlias $TrustedInterface).NetworkCategory
$FirewallProfile = if ($NetworkCategory -eq 'DomainAuthenticated') { 'Domain' } else { [string]$NetworkCategory }

$rdpUsersSid = [System.Security.Principal.SecurityIdentifier]'S-1-5-32-555'
Add-LocalGroupMember -SID $rdpUsersSid -Member $WinPodXUser

New-ItemProperty -Path $rdpTcp -Name SecurityLayer -PropertyType DWord -Value 2 -Force | Out-Null
New-ItemProperty -Path $rdpTcp -Name UserAuthentication -PropertyType DWord -Value 0 -Force | Out-Null

New-Item -Path $rail -Force | Out-Null
New-ItemProperty -Path $rail -Name fDisabledAllowList -PropertyType DWord -Value 1 -Force | Out-Null

New-Item -Path $policy -Force | Out-Null
New-ItemProperty -Path $policy -Name fInheritInitialProgram -PropertyType DWord -Value 1 -Force | Out-Null

Get-NetFirewallServiceFilter -Service TermService |
  Get-NetFirewallRule |
  Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } |
  Set-NetFirewallRule -RemoteAddress $LinuxHost -Profile $FirewallProfile
```

Create the dedicated account through Windows account settings before running this block.
For a domain account, set `$WinPodXUser` to `DOMAIN\User`. The well-known group SID adds
the account to **Remote Desktop Users** without depending on the Windows display language.
Run these commands locally so a firewall mistake cannot lock out your only remote session.
Set `$TrustedInterface` to the interface alias reported by `Get-NetConnectionProfile` for
the isolated link or VPN. The block maps its `DomainAuthenticated` category to the
firewall cmdlet's `Domain` value; do not assume a VPN or host-only adapter is `Private`.

`fDisabledAllowList=1` permits an authenticated RDP client to request an arbitrary
RemoteApp initial program. Use a dedicated account and a network-level firewall allowlist
to limit who can reach RDP. Reboot Windows, or restart Remote Desktop Services when no
session is active, before testing a new connection.

Stock Windows 11 Pro supports RemoteApp in a **single RDP session** with these RAIL keys;
no session unlock is needed. Opening a second concurrent session may reconnect or replace
the first one.

### Optional concurrent sessions

Install multi-session support only if you need independent apps in concurrent RDP
sessions. The managed guest uses [rdprrap](https://github.com/kernalix7/rdprrap), but an
external host must install, update, and validate it independently. Review its licensing,
Windows-build compatibility, and security implications before use. Do not copy the
WinPodX OEM archive or installer commands out of `config/oem/`; that flow is version-pinned
for the managed image. Activating a wrapper restarts `TermService` and disconnects active
RDP sessions.

See [Multi-Session RDP](FEATURES.md#multi-session-rdp) for the managed-guest design. Its
automatic install, `max_sessions` synchronization, activation checks, and self-healing do
not apply to the manual backend.

## 3. Install the guest agent

The agent supplies `/health` and bearer-authenticated `/exec` for host-to-guest operations.
Copy `config/oem/agent/agent.ps1` from the same WinPodX release to
`C:\OEM\agent.ps1` on Windows. Do not fetch a script from an unrelated branch or release.

WinPodX setup normally creates the host token under the WinPodX XDG configuration
directory. If it is absent, create a 32-byte random token without printing it:

```bash
WINPODX_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/winpodx"
install -d -m 700 "$WINPODX_CONFIG_DIR"
umask 077
python3 -c 'import pathlib,secrets,sys; pathlib.Path(sys.argv[1]).write_text(secrets.token_hex(32), encoding="ascii")' "$WINPODX_CONFIG_DIR/agent_token.txt"
chmod 600 "$WINPODX_CONFIG_DIR/agent_token.txt"
```

Transfer that file through a trusted channel to `C:\OEM\agent_token.txt`. The files must
contain the same 64 hexadecimal characters. Do not put the token in a command argument,
URL, log, screenshot, or shell history.

The agent runs as the interactive Windows user and must be able to write its run directory
and log without being able to modify its script or token. In an elevated PowerShell
window, replace the account below with the account used for WinPodX, then create narrowly
scoped ACLs:

```powershell
$WinPodXUser = "$env:COMPUTERNAME\winpodx"

New-Item -ItemType Directory -Path C:\OEM\agent-runs -Force | Out-Null
if (-not (Test-Path C:\OEM\agent.log)) {
  New-Item -ItemType File -Path C:\OEM\agent.log | Out-Null
}

icacls.exe C:\OEM\agent.ps1 /inheritance:r /grant:r "${WinPodXUser}:(R)" "*S-1-5-32-544:(F)" "*S-1-5-18:(F)"
icacls.exe C:\OEM\agent_token.txt /inheritance:r /grant:r "${WinPodXUser}:(R)" "*S-1-5-32-544:(F)" "*S-1-5-18:(F)"
icacls.exe C:\OEM\agent.log /inheritance:r /grant:r "${WinPodXUser}:(M)" "*S-1-5-32-544:(F)" "*S-1-5-18:(F)"
icacls.exe C:\OEM\agent-runs /inheritance:r /grant:r "${WinPodXUser}:(OI)(CI)(M)" "*S-1-5-32-544:(OI)(CI)(F)" "*S-1-5-18:(OI)(CI)(F)"
```

For a domain account, set `$WinPodXUser` to `DOMAIN\User` instead. Do not grant the
interactive user modify permission on `agent.ps1` or `agent_token.txt`. The two
`*S-1-...` trustees are the locale-independent SIDs for local Administrators and `SYSTEM`.

The shipped agent listens on `http://+:8765/`. In an elevated PowerShell window, reserve
that URL and create a source-restricted firewall rule. Replace the documentation address
below with the Linux host's fixed private or VPN address:

```powershell
$LinuxHost = '192.0.2.10'
$WinPodXUser = "$env:COMPUTERNAME\winpodx"
$TrustedInterface = 'Ethernet'
$NetworkCategory = (Get-NetConnectionProfile -InterfaceAlias $TrustedInterface).NetworkCategory
$FirewallProfile = if ($NetworkCategory -eq 'DomainAuthenticated') { 'Domain' } else { [string]$NetworkCategory }
$UserSid = (New-Object System.Security.Principal.NTAccount($WinPodXUser)).Translate(
  [System.Security.Principal.SecurityIdentifier]
).Value
$UrlSddl = "D:(A;;GX;;;$UserSid)(A;;GX;;;BA)(A;;GX;;;SY)"

netsh http show urlacl url=http://+:8765/
netsh http add urlacl url=http://+:8765/ sddl="$UrlSddl"

New-NetFirewallRule `
  -DisplayName 'WinPodX agent from Linux host' `
  -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 `
  -RemoteAddress $LinuxHost -Profile $FirewallProfile
```

If `netsh http add urlacl` reports a conflict, inspect the existing reservation and remove
or replace it only after identifying its owner. Do not blindly delete another service's
URL ACL.

The agent uses plaintext HTTP. `GET /health` is intentionally unauthenticated, while
`POST /exec` requires the bearer token and can execute PowerShell as the WinPodX user.
Source-IP filtering does not encrypt or authenticate this traffic. Use an isolated
host-only network or authenticated VPN for port `8765`; an ordinary shared LAN is not
sufficient. Never forward the port to the Internet or allow an entire untrusted subnet.

Run the agent as the Windows account used for WinPodX, not as `SYSTEM`, because discovery
and reverse-open registration use that account's Start menu and `HKCU`. Log in as that
user and register a per-user startup entry:

```powershell
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\OEM\agent.ps1'
New-ItemProperty -Path $run -Name WinpodxAgent -PropertyType String -Value $command -Force | Out-Null
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'C:\OEM\agent.ps1'
)
```

From Linux, verify only the non-secret health endpoint:

```bash
curl --fail --silent --show-error http://192.0.2.20:8765/health
```

## 4. Register and launch apps

Once RDP and the agent are reachable, test the desktop before individual RemoteApps:

```bash
winpodx app run desktop
```

Automatic Windows app discovery currently requires a Podman or Docker backend;
`winpodx app refresh` rejects `backend = "manual"` before it reaches the agent. Create a
user app profile instead. For example:

```bash
WINPODX_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/winpodx"
install -d "$WINPODX_DATA_DIR/apps/notepad"
cat > "$WINPODX_DATA_DIR/apps/notepad/app.toml" <<'EOF'
name = "notepad"
full_name = "Notepad"
executable = "C:\\Windows\\System32\\notepad.exe"
categories = ["Utility"]
EOF

winpodx app install notepad
winpodx app run notepad
```

Repeat with the actual Windows executable path and a safe alphanumeric, dash, or underscore
slug for each app. `winpodx app list` shows the profiles WinPodX can launch, and
`winpodx app install-all` registers all of them in the Linux desktop menu. RAIL behavior,
app-profile fields, and the guest agent are described in [Features](FEATURES.md) and
[Architecture](ARCHITECTURE.md); those pages describe the managed guest unless they say
otherwise.

## 5. Optional reverse-open

Reverse-open makes selected Linux applications appear in Windows **Open with**, but it is
not recommended for an unmanaged external host. The shipped guest shim writes requests to
the fixed path `\\tsclient\home\.local\share\winpodx\reverse-open\incoming`. It therefore
does not work with a custom `XDG_DATA_HOME` or the restricted `pod.home_share` configured
above.

Enabling it requires restoring the default whole-home redirection, which gives the Windows
host read/write access to all of Linux `$HOME`. A compromised guest can also launch
allowlisted host applications and pass them hostile files or URLs. Keep reverse-open
disabled unless you explicitly accept both risks. If you do, use the default XDG data path,
set a minimal explicit allowlist, start a new FreeRDP session, and provide:

- the working guest agent and matching token;
- the host listener started by WinPodX;
- an active FreeRDP session with the host directory redirected as
  `\\tsclient\home`; and
- the per-user Windows handlers staged by `winpodx host-open refresh`.

`\\tsclient\home` is FreeRDP drive redirection, not a general SMB server. It exists only
while the relevant RDP session is active. Enable and verify the feature from Linux:

```bash
winpodx config set pod.home_share ""
winpodx host-open enable
winpodx host-open add your-trusted-app-slug
winpodx host-open refresh
winpodx host-open start-listener
winpodx host-open daemon-status
winpodx host-open status
```

`daemon-status` verifies the listener process. The separate `status` command reports the
feature toggle, allowlist, and cached manifest rather than daemon health.

See [Reverse-open](FEATURES.md#reverse-open-linux-apps-in-windows-open-with) for its data
flow and security model. On an external host, you remain responsible for keeping the
agent, listener, redirection, and Windows registrations in sync.

## Verification and troubleshooting

| Check | Expected result |
|---|---|
| `nc -vz 192.0.2.20 3389` | The configured RDP port is reachable from Linux. |
| `curl --fail http://192.0.2.20:8765/health` | JSON reports `"ok": true`; no token is sent. |
| `winpodx app run desktop` | A full desktop opens before testing individual RemoteApps. |
| `winpodx app list` | Manually authored app profiles are listed. |
| `winpodx host-open daemon-status` | The optional host listener is running. |
| `winpodx host-open status` | The optional feature toggle and manifest state are correct. |

`winpodx doctor` still checks local dependencies and configuration, but it does not probe
the remote RDP or agent endpoints for the manual backend. Use the `nc` and `curl` checks
above as the authoritative reachability tests.

On Windows, use these read-only checks from an elevated PowerShell window:

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList' -Name fDisabledAllowList
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' -Name fInheritInitialProgram
Get-NetFirewallServiceFilter -Service TermService | Get-NetFirewallRule | Get-NetFirewallAddressFilter
netsh http show urlacl url=http://+:8765/
Get-NetFirewallRule -DisplayName 'WinPodX agent from Linux host' | Get-NetFirewallAddressFilter
Get-NetTCPConnection -LocalPort 8765 -State Listen
Get-Content C:\OEM\agent.log -Tail 50
```

If RDP works but RemoteApp does not, retest after a Windows reboot and confirm both RAIL
values are DWORD `1`. If `/health` is unreachable, check the listener, source-restricted
firewall rule, URL ACL, and token file before widening network access. If one app works but
a concurrent app replaces it, that is stock single-session behavior rather than a RAIL
failure.

External hosts do not receive managed-guest migrations. After each WinPodX upgrade,
review the release notes and compare your agent script and optional integrations with the
new release before replacing them.
