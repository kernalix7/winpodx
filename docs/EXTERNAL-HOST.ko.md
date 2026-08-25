# 외부 Windows 호스트

[English](EXTERNAL-HOST.md) | **한국어**

WinPodX는 dockur가 관리하는 Windows 게스트에서 가장 잘 동작합니다. 이 경로가 권장되는
first-class 경로이며 자동 프로비저닝, 마이그레이션, 복구, 멀티세션 설정, 게스트 에이전트
업데이트, reverse-open 유지보수도 이 경로를 기준으로 합니다.

`manual` 백엔드는 기존 Windows 10 또는 Windows 11 Pro PC/VM에 대신 연결할 수 있습니다.
이 경로는 best-effort로 지원됩니다.

- 관리 게스트의 loopback 연결과 달리 LAN 또는 VPN을 통해 RDP에 연결하므로 네트워크
  지연과 처리량에 따라 앱 반응성이 떨어질 수 있습니다.
- Windows 가용성, 업데이트, RDP 정책, 방화벽 규칙, 게스트 에이전트, 선택적 멀티세션,
  선택적 reverse-open 등록은 사용자가 직접 관리해야 합니다.
- WinPodX는 이러한 게스트 구성요소를 프로비저닝, 시작, 중지, 마이그레이션 또는
  self-heal하지 않습니다.

> **서로 다른 두 가지 "manual":** `[pod] backend = "manual"`은 사용자가 보유한
> Windows에 연결한다는 뜻입니다. `install.sh --manual`은 WinPodX만 설치하고 최초
> 설정/프로비저닝 실행을 건너뛰며, 나중에 설정을 실행하면 일반 dockur 흐름을 사용합니다.
> 두 기능은 서로 관련이 없습니다.

## 시작하기 전에

Remote Desktop 호스트 기능을 제공하는 지원 Windows 에디션, 일상적인 RDP 사용을 위한
전용 비관리자 Windows 계정, Linux와 Windows 사이의 격리된 host-only 네트워크 또는 인증된
VPN을 준비하십시오. 현재 manual 백엔드는 NLA(Network Level Authentication) 없이 TLS
보안을 사용해야 하므로 사전 인증 보호 수준이 낮습니다. 정책상 NLA가 필수라면 이 경로를
사용하지 마십시오. RDP나 WinPodX 에이전트를 공용 인터넷에 직접 노출하지 마십시오.

아래 명령은 외부 호스트에 필요한 부분만 의도적으로 재현합니다.
**`config/oem/install.bat` 전체를 실행하지 마십시오.** 이 스크립트는 dockur 최초 부팅용
오케스트레이션이며 DNS, Defender 제외, Windows Update, 서비스, 전원 정책, 예약 작업,
멀티세션 상태, 재부팅 동작까지 변경합니다.

## 1. manual 백엔드 설정

`winpodx setup --customize`를 실행해 manual 백엔드를 선택하거나
설정 파일을 직접 편집합니다. 기본 경로는 `~/.config/winpodx/winpodx.toml`이며 custom
`XDG_CONFIG_HOME`을 사용하면 `~/.config` 부분이 바뀝니다. Wizard는 호스트 주소와 자격
증명을 저장하지만 외부 RDP 포트는 묻지 않아 관리 게스트 기본값 `3390`을 유지하고, 전체
홈 디렉터리 drive redirection도 그대로 둡니다. Prompt에는 기존 Windows 계정 password를
입력하십시오. Enter를 누르면 외부 호스트가 알지 못하는 관리 게스트용 random password가
생성됩니다.

Wizard 실행 후 Windows 기본 RDP 포트를 설정하고 게스트에 공개할 디렉터리를 제한합니다.

```bash
install -d -m 700 "$HOME/winpodx-share"
winpodx config set rdp.port 3389
winpodx config set pod.home_share "$HOME/winpodx-share"
```

직접 편집할 때는 같은 값을 사용하고 setup 완료 상태를 표시합니다. 아래 홈 경로는 실제
Linux 계정의 absolute path로 바꾸고 첫 연결 전에 위 `install` 명령으로 생성하십시오.

```toml
[pod]
backend = "manual"
initialized = true
home_share = "/home/you/winpodx-share"

[rdp]
ip = "192.0.2.20"       # Windows 호스트의 사설/VPN 주소로 바꾸십시오.
port = 3389
user = "winpodx"
password = "CHANGE_ME"
```

설정 파일에는 RDP 자격 증명이 들어 있으므로 권한을 제한합니다.

```bash
WINPODX_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/winpodx"
chmod 600 "$WINPODX_CONFIG_DIR/winpodx.toml"
```

빈 `pod.home_share`는 SSH 키, 브라우저 프로필, shell 시작 파일, WinPodX 자격 증명을 포함한
Linux `$HOME` 전체를 Windows 세션에 read/write로 공개합니다. 관리되지 않는 외부 호스트에는
위의 제한 모드를 유지하십시오. 또한 WinPodX는 RDP password를 FreeRDP process argument로
전달하므로 신뢰할 수 없는 multi-user Linux 시스템에서 이 workflow를 사용하지 마십시오.

manual 백엔드는 `rdp.ip:rdp.port`를 확인하고 해당 주소에 연결하기만 합니다. 시작과 중지
동작은 no-op이므로 Windows 부팅, 일시 중지, 복구, 종료는 사용자가 관리해야 합니다.

## 2. RDP와 RemoteApp(RAIL) 활성화

Windows에서 **설정 > 시스템 > 원격 데스크톱**을 활성화하고 격리된 link 또는 인증된
VPN을 통하는 Linux 호스트에서만 RDP에 접근하도록 제한합니다. 현재 WinPodX는 FreeRDP를
`/sec:tls`로 실행하므로 호스트가 NLA(Network Level Authentication) 없이 TLS 보안 연결을 허용해야
합니다. 이 TLS-only endpoint를 공용 인터넷에 노출하지 마십시오. 관리자 권한
PowerShell에서 TLS를 유지하고 이 endpoint의 NLA를 끈 뒤 RAIL을 활성화합니다.

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

이 블록을 실행하기 전에 Windows 계정 설정에서 전용 계정을 만드십시오. 도메인 계정이면
`$WinPodXUser`를 `DOMAIN\User`로 설정합니다. well-known group SID를 사용하므로 Windows 표시
언어와 관계없이 **Remote Desktop Users**에 계정이 추가됩니다. 방화벽 설정 실수로 유일한
원격 세션이 잠기지 않도록 이 명령은 Windows에서 로컬로 실행하십시오.
격리된 link 또는 VPN에 대해 `Get-NetConnectionProfile`이 보고하는 interface alias로
`$TrustedInterface`를 설정하십시오. 이 블록은 `DomainAuthenticated` category를 firewall
cmdlet의 `Domain` 값으로 변환합니다. VPN이나 host-only adapter가 `Private`이라고 가정하지
마십시오.

`fDisabledAllowList=1`은 인증된 RDP 클라이언트가 임의의 RemoteApp 초기 프로그램을
요청할 수 있게 합니다. 전용 계정과 네트워크 방화벽 allowlist로 RDP에 접근할 수 있는
대상을 제한하십시오. 새 연결을 테스트하기 전에 Windows를 재부팅하거나 활성 세션이 없을
때 Remote Desktop Services를 다시 시작하십시오.

Windows 11 Pro 기본 상태에서도 이 RAIL 키를 사용하면 **단일 RDP 세션**에서 RemoteApp이
동작하며 별도의 세션 unlock은 필요하지 않습니다. 두 번째 동시 세션을 열면 첫 번째 세션에
재연결하거나 첫 번째 세션을 대체할 수 있습니다.

### 선택적 동시 세션

서로 독립적인 앱을 여러 RDP 세션에서 동시에 실행해야 할 때만 멀티세션 지원을 설치합니다.
관리 게스트는 [rdprrap](https://github.com/kernalix7/rdprrap)을 사용하지만, 외부 호스트에서는
사용자가 직접 설치, 업데이트, 검증해야 합니다. 사용 전에 라이선스, Windows 빌드 호환성,
보안 영향을 검토하십시오. `config/oem/`의 WinPodX OEM 아카이브나 설치 명령을 복사해
사용하지 마십시오. 해당 흐름은 관리 이미지의 특정 버전에 고정되어 있습니다. wrapper를
활성화하면 `TermService`가 다시 시작되어 활성 RDP 세션이 끊어집니다.

관리 게스트 설계는 [멀티세션 RDP](FEATURES.ko.md#멀티세션-rdp)를 참고하십시오.
자동 설치, `max_sessions` 동기화, 활성화 검사, self-healing은 manual 백엔드에 적용되지
않습니다.

## 3. 게스트 에이전트 설치

에이전트는 host-to-guest 작업에 `/health`와 bearer 인증 `/exec`를 제공합니다. 같은
WinPodX 릴리스의 `config/oem/agent/agent.ps1`을 Windows의 `C:\OEM\agent.ps1`로
복사하십시오. 서로 다른 브랜치나 릴리스에서 스크립트를 가져오지 마십시오.

WinPodX 설정은 일반적으로 WinPodX XDG 설정 디렉터리에 호스트 토큰을 생성합니다. 파일이
없다면 값을 출력하지 않고 32바이트 무작위 토큰을 생성합니다.

```bash
WINPODX_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/winpodx"
install -d -m 700 "$WINPODX_CONFIG_DIR"
umask 077
python3 -c 'import pathlib,secrets,sys; pathlib.Path(sys.argv[1]).write_text(secrets.token_hex(32), encoding="ascii")' "$WINPODX_CONFIG_DIR/agent_token.txt"
chmod 600 "$WINPODX_CONFIG_DIR/agent_token.txt"
```

신뢰할 수 있는 채널을 통해 이 파일을 `C:\OEM\agent_token.txt`로 전송합니다. 두 파일에는
동일한 64자리 16진수 문자가 들어 있어야 합니다. 토큰을 명령 인수, URL, 로그, 스크린샷,
셸 기록에 넣지 마십시오.

에이전트는 대화형 Windows 사용자로 실행되며, 스크립트나 토큰을 수정할 수 없어야 하지만
실행 디렉터리와 로그에는 쓸 수 있어야 합니다. 관리자 권한 PowerShell에서 아래 계정을
WinPodX에 사용하는 계정으로 바꾼 뒤 최소 범위 ACL을 만듭니다.

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

도메인 계정이면 `$WinPodXUser`를 `DOMAIN\User`로 설정하십시오. 대화형 사용자에게
`agent.ps1` 또는 `agent_token.txt` 수정 권한을 부여하지 마십시오. 두 `*S-1-...`
trustee는 로컬 Administrators와 `SYSTEM`의 locale-independent SID입니다.

제공되는 에이전트는 `http://+:8765/`에서 수신합니다. 관리자 권한 PowerShell에서 해당
URL을 예약하고 송신지를 제한한 방화벽 규칙을 만듭니다. 아래 문서용 주소는 Linux 호스트의
고정 사설 또는 VPN 주소로 바꾸십시오.

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

`netsh http add urlacl`이 충돌을 보고하면 기존 예약과 소유자를 확인한 다음 필요한 경우에만
제거하거나 교체하십시오. 다른 서비스의 URL ACL을 확인 없이 삭제하지 마십시오.

에이전트는 평문 HTTP를 사용합니다. `GET /health`에는 의도적으로 인증이 없고,
`POST /exec`에는 bearer 토큰이 필요하며 WinPodX 사용자 권한으로 PowerShell을 실행할 수
있습니다. 송신지 IP 제한만으로는 트래픽이 암호화되거나 인증되지 않습니다. 포트 `8765`에는
격리된 host-only 네트워크 또는 인증된 VPN을 사용하십시오. 일반 shared LAN만으로는
충분하지 않습니다. 포트를 인터넷으로 포워딩하거나 신뢰할 수 없는 전체 subnet에 허용하지
마십시오.

검색과 reverse-open 등록은 해당 계정의 Start 메뉴와 `HKCU`를 사용하므로 에이전트를
`SYSTEM`이 아니라 WinPodX에 사용하는 Windows 계정으로 실행합니다. 해당 사용자로
로그인해 사용자별 시작 항목을 등록합니다.

```powershell
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$command = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\OEM\agent.ps1'
New-ItemProperty -Path $run -Name WinpodxAgent -PropertyType String -Value $command -Force | Out-Null
Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', 'C:\OEM\agent.ps1'
)
```

Linux에서는 비밀 값이 없는 health endpoint만 확인합니다.

```bash
curl --fail --silent --show-error http://192.0.2.20:8765/health
```

## 4. 앱 등록 및 실행

RDP와 에이전트에 접근할 수 있게 되면 개별 RemoteApp보다 먼저 desktop을 테스트합니다.

```bash
winpodx app run desktop
```

Windows 앱 자동 검색은 현재 Podman 또는 Docker 백엔드가 필요합니다.
`winpodx app refresh`는 에이전트에 도달하기 전에 `backend = "manual"`을 거부합니다.
대신 사용자 앱 프로필을 만듭니다. 예:

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

각 앱에 실제 Windows 실행 파일 경로와 영숫자, dash, underscore로만 구성된 안전한 slug를
사용해 반복합니다. `winpodx app list`는 WinPodX가 실행할 수 있는 프로필을 표시하고,
`winpodx app install-all`은 모든 프로필을 Linux desktop 메뉴에 등록합니다. RAIL 동작,
앱 프로필 필드, 게스트 에이전트는 [기능](FEATURES.ko.md)과
[아키텍처](ARCHITECTURE.ko.md)를 참고하십시오. 별도 설명이 없다면 해당 페이지는 관리
게스트를 기준으로 합니다.

## 5. 선택적 reverse-open

Reverse-open은 선택한 Linux 애플리케이션을 Windows의 **연결 프로그램**에 표시하지만
관리되지 않는 외부 호스트에는 권장하지 않습니다. 제공되는 게스트 shim은 요청을 고정 경로
`\\tsclient\home\.local\share\winpodx\reverse-open\incoming`에 기록합니다. 따라서 custom
`XDG_DATA_HOME` 또는 위에서 설정한 제한된 `pod.home_share`와 함께 동작하지 않습니다.

이 기능을 활성화하려면 Windows 호스트에 Linux `$HOME` 전체 read/write 권한을 주는 기본
whole-home redirection으로 되돌려야 합니다. 손상된 게스트는 allowlist에 있는 host 앱을
실행하고 악성 파일이나 URL을 전달할 수도 있습니다. 두 위험을 명시적으로 수용하지 않는다면
reverse-open을 비활성 상태로 유지하십시오. 수용한다면 기본 XDG data path와 최소 explicit
allowlist를 사용하고 새 FreeRDP 세션을 시작한 뒤 다음 항목을 준비합니다.

- 정상 동작하는 게스트 에이전트와 일치하는 토큰
- WinPodX가 시작한 호스트 listener
- 호스트 디렉터리를 `\\tsclient\home`으로 redirect한 활성 FreeRDP 세션
- `winpodx host-open refresh`가 준비한 사용자별 Windows handler

`\\tsclient\home`은 일반 SMB 서버가 아니라 FreeRDP drive redirection입니다. 관련 RDP
세션이 활성 상태일 때만 존재합니다. Linux에서 기능을 활성화하고 확인합니다.

```bash
winpodx config set pod.home_share ""
winpodx host-open enable
winpodx host-open add your-trusted-app-slug
winpodx host-open refresh
winpodx host-open start-listener
winpodx host-open daemon-status
winpodx host-open status
```

`daemon-status`는 listener 프로세스를 확인합니다. 별도의 `status` 명령은 daemon 상태가
아니라 기능 toggle, allowlist, cached manifest를 보고합니다.

데이터 흐름과 보안 모델은
[Reverse-open](FEATURES.ko.md#reverse-open-linux-앱이-windows-open-with-에)을 참고하십시오.
외부 호스트에서는 에이전트, listener, redirection, Windows 등록을 사용자가 동기화 상태로
유지해야 합니다.

## 검증 및 문제 해결

| 검사 | 예상 결과 |
|---|---|
| `nc -vz 192.0.2.20 3389` | 설정한 RDP 포트에 Linux에서 접근할 수 있습니다. |
| `curl --fail http://192.0.2.20:8765/health` | 토큰을 보내지 않아도 JSON에 `"ok": true`가 표시됩니다. |
| `winpodx app run desktop` | 개별 RemoteApp을 테스트하기 전에 전체 데스크톱이 열립니다. |
| `winpodx app list` | 사용자가 작성한 앱 프로필이 표시됩니다. |
| `winpodx host-open daemon-status` | 선택적 호스트 listener가 실행 중입니다. |
| `winpodx host-open status` | 선택 기능의 toggle과 manifest 상태가 올바릅니다. |

`winpodx doctor`는 로컬 dependency와 설정은 계속 검사하지만 manual 백엔드의 원격 RDP 또는
agent endpoint를 probe하지 않습니다. 위 `nc`와 `curl`을 authoritative reachability 검사로
사용하십시오.

Windows에서는 관리자 권한 PowerShell에서 다음 read-only 검사를 사용합니다.

```powershell
Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList' -Name fDisabledAllowList
Get-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' -Name fInheritInitialProgram
Get-NetFirewallServiceFilter -Service TermService | Get-NetFirewallRule | Get-NetFirewallAddressFilter
netsh http show urlacl url=http://+:8765/
Get-NetFirewallRule -DisplayName 'WinPodX agent from Linux host' | Get-NetFirewallAddressFilter
Get-NetTCPConnection -LocalPort 8765 -State Listen
Get-Content C:\OEM\agent.log -Tail 50
```

RDP는 동작하지만 RemoteApp이 동작하지 않으면 Windows를 재부팅한 뒤 다시 테스트하고 두
RAIL 값이 DWORD `1`인지 확인하십시오. `/health`에 접근할 수 없으면 네트워크 접근 범위를
넓히기 전에 listener, 송신지 제한 방화벽 규칙, URL ACL, 토큰 파일을 확인하십시오. 앱
하나는 동작하지만 동시 실행한 앱이 기존 앱을 대체하면 RAIL 실패가 아니라 Windows 기본
단일 세션 동작입니다.

외부 호스트에는 관리 게스트 마이그레이션이 적용되지 않습니다. WinPodX를 업그레이드할
때마다 릴리스 노트를 검토하고, 에이전트 스크립트와 선택적 통합을 교체하기 전에 새 릴리스와
비교하십시오.
