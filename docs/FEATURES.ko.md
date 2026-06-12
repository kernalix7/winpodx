# 기능

[English](FEATURES.md) | **한국어**

전체 기능 셋: 주변기기 & 공유, 멀티세션 RDP, 앱 프로필, reverse-open (Linux 앱이 Windows "Open with…" 메뉴에 등장).

## Reverse-open (Linux 앱이 Windows "Open with…" 에)

v0.5.0 에 ship, 이후 기본 활성. Windows 게스트 안에서 아무 파일이나 우클릭하면 Linux 측 핸들러가 "Open with…" 메뉴에 등장 — `.txt` 는 Kate, `.png` 는 gwenview, `.html` 은 Firefox 등. 하나 선택하면 파일 열기가 호스트의 `xdg-open` 으로 round-trip 해서 실제로 Linux 에서 설정한 앱에 도착.

동작 방식:

```
Windows Explorer 우클릭        ─┐
                               │  슬러그별 winpodx-<slug>.exe
                               │  (Rust shim, rcedit 으로 아이콘 embed)
                               ▼
   \\tsclient\home\.local\share\winpodx\reverse-open\incoming\<uuid>.json 에 atomic JSON 쓰기
                               │
                               ▼
   host listener daemon 이 픽업, safe_open_unc 가 경로 검증
                               │
                               ▼
   subprocess: <app.exec_argv> 와 실제 호스트 파일 경로
```

기능은 **기본 활성** (`cfg.reverse_open.enabled = true`). 사용자가 Linux 기본 핸들러로 설정한 각 Linux 앱 — `xdg-mime default` 또는 DE 의 "기본 앱" 설정 통해 — 이 Windows 측에 매칭되는 확장자와 함께 등록. Discovery 가 `$XDG_DATA_HOME/applications` + `$XDG_DATA_DIRS` 와 freedesktop 검색 경로의 모든 `mimeapps.list` 를 walk.

CLI 로 관리:

```bash
winpodx host-open status        # listener + manifest 상태
winpodx host-open list          # push 될 앱들
winpodx host-open refresh       # 재스캔 + 게스트로 push
winpodx host-open add <slug>    # allowlist
winpodx host-open remove <slug> # 제거 (또는 --deny)
winpodx host-open disable       # 기능 전체 끄기
```

또는 GUI Settings 페이지 → reverse-open 패널 (같은 컨트롤).

슬러그별 아이콘이 짧은 Open With 메뉴 + 긴 "다른 앱 선택" 다이얼로그 양쪽에 렌더링되는 이유: 각 `winpodx-<slug>.exe` 가 Rust shim 의 독립 사본이고 매칭되는 `.ico` 가 PE resource section 에 embed 되어있기 때문 (vendored `rcedit.exe`, electron/rcedit v2.0.0, MIT). Chooser 아이콘 트레이드오프: 슬러그별 `.exe` 사본이 디스크에 ~500 KB × N 앱 차지 (hard-link inode 공유 없음) — Win10/Win11 에서 reliably 동작하는 유일한 chooser 아이콘 경로라서.

## 매끄러운 앱 윈도

- RemoteApp (RAIL) 이 각 앱을 네이티브 Linux 윈도로 렌더링 — 전체 데스크톱 아님
- `WM_CLASS` 매칭 통한 앱별 taskbar 아이콘 (`/wm-class:<stem>` + `StartupWMClass`)
- 파일 연결: Linux 파일 관리자에서 `.docx` 더블클릭 → Word 가 열림
- 멀티세션 RDP: bundled rdprrap 이 최대 10개 독립 세션 자동 활성화
- 실행 중인 세션을 GUI Dashboard (Running-sessions 스트립) 또는 시스템 트레이 메뉴에서 종료
- RAIL 전제조건 (`fDisabledAllowList=1` + `fInheritInitialProgram=1` + `MaxInstanceCount=10`) 이 unattended 설치 중 자동 설정
- 멀티모니터 RAIL 기본 활성 (`cfg.rdp.multimon = "span"`): 리모트 앱 윈도를 두 번째 모니터로 드래그해도 입력이 계속 동작
- UWP/Store 앱도 다른 앱처럼 Linux taskbar 에 등장

## 제로 설정 실행

- 첫 앱 클릭이 모든 것 자동 프로비저닝: config, 컨테이너, desktop 엔트리
- 첫 부팅 시 자동 discovery 가 실행 중인 Windows 게스트 스캔, 설치된 모든 앱 (Registry App Paths, Start Menu, UWP/MSIX, Chocolatey, Scoop) 을 실제 바이너리 아이콘과 함께 등록
- `winpodx app refresh` 또는 GUI Refresh 버튼으로 언제든 수동 재스캔
- 고급 설정용 대화형 setup 마법사
- 로그인 시 pod 자동 시작 옵션 (opt-in, 기본 꺼짐): `winpodx autostart on|off|status` 또는 GUI 체크박스가 XDG autostart `.desktop` 엔트리(`~/.config/autostart/winpodx-tray.desktop`)를 설치 — 로그인 시 트레이가 떠 첫 앱 클릭 전에 Windows pod 이 미리 준비됨

## 다국어 UI

트레이, GUI, CLI 가 7개 언어로 완전 번역: 영어, 한국어, 중국어 (中文), 일본어 (日本語), 독일어 (Deutsch), 프랑스어 (Français), 이탈리아어 (Italiano).

- 첫 실행 시 시스템 로케일에서 자동 감지; 미지원 로케일은 영어로 fallback
- `winpodx language <code>` (예: `winpodx language ja`) 또는 GUI 언어 드롭다운으로 언제든 전환
- config `[ui] language` 에 저장

## Start-menu GUI & 대시보드

데스크톱 GUI 는 Start-menu 스타일 레이아웃으로 구성: 왼쪽 세로 내비게이션 사이드바 (페이지당 한 행) + 처음 진입하는 **Dashboard** 홈.

- **Dashboard** 는 실시간 Pod / RAM / CPU 링 게이지와 디스크 사용량, 자동 복구 상태 카드, 고정/최근 워크스페이스 타일, reverse-open 토글을 보여줌.
- 앱 런처는 이제 **All apps** 페이지.
- **Devices** 페이지는 USB / PCI 패스스루용 두 컬럼 host ↔ guest 디바이스 mover 를 제공.
- 통합 디자인 시스템 + 자체 SVG 아이콘 셋 (더 이상 유니코드 글리프 아이콘 아님), 좁거나 분수 배율 윈도에서 reflow 하는 반응형 레이아웃, fit-to-screen 사이징.
- 상단의 hero 검색이 커맨드 바 역할도 겸함.

## 주변기기 & 공유

| 기능 | 동작 방식 | 기본값 |
|---------|-------------|---------|
| **클립보드** | RDP 통한 양방향 복사-붙여넣기 (`+clipboard`) | 활성 |
| **사운드** | ALSA 통한 오디오 스트리밍 (`/sound:sys:alsa`) | 활성 |
| **프린터** | Linux 프린터가 Windows 에 공유 (`/printer`) | 활성 |
| **홈 디렉토리** | `\\tsclient\home` 으로 공유 (`+home-drive`) | 활성 |
| **USB 드라이브** | media 폴더가 `\\tsclient\media` 로 공유 (`/drive:media`); 세션 시작 후 꽂은 USB 도 서브폴더로 접근 가능. 마운트된 미디어가 없어도 게스트 측 USB 바로가기가 항상 정상 동작 | 활성 |
| **USB 디바이스 패스스루** | 네이티브 USB 리디렉션 (`/usb:auto`) — FreeRDP urbdrc 플러그인 필요 | **Opt-in** (`extra_flags` 에 추가) |
| **호스트 USB / PCI 패스스루** | 호스트 USB 또는 PCI 디바이스를 Windows 게스트로 직접 매핑 (`winpodx device list / attach <id> / detach <id>`, GUI Devices 탭, 트레이 USB 스위처). USB 는 라이브 핫플러그; PCI 는 부팅 시 추가되어 게스트 재시작 + 안전 확인 필요 | USB 라이브 (`cfg.pod.usb_live`, 기본 켜짐) |
| **USB 드라이브 매핑** | Windows 측 스크립트가 FileSystemWatcher 로 USB 서브폴더를 드라이브 레터 (E:, F:, ...) 로 자동 매핑 | 활성 |
| **Reverse 파일 열기** | Linux 앱이 Windows 게스트 우클릭 "Open with…" 메뉴에 등장; 선택 시 호스트 `xdg-open` 으로 round-trip | 활성 |

### USB 드라이브 흐름

```
Linux 에서 USB 꽂음
    │
    ▼
Linux 가 /run/media/$USER/USBNAME 으로 마운트
    │
    ▼
FreeRDP 가 \\tsclient\media\USBNAME 으로 공유
    │
    ▼
media_monitor.ps1 감지 → net use E: \\tsclient\media\USBNAME
    │
    ▼
Windows Explorer 에 E: 드라이브 표시
```

### 호스트 USB / PCI 디바이스 패스스루

공유 폴더가 아니라 실제 호스트 주변기기를 Windows 게스트 안으로 통째로 매핑:

```bash
winpodx device list            # 호스트 디바이스 + 현재 게스트 연결 상태
winpodx device attach <id>     # USB 또는 PCI 디바이스를 게스트에 연결
winpodx device detach <id>     # 다시 분리
```

- **USB** 는 라이브 핫플러그 (`cfg.pod.usb_live`, 기본 켜짐) — 게스트 재시작 없이 연결/분리.
- **PCI** 는 부팅 시 추가: 적용하려면 게스트 재시작이 필요하고 안전 확인을 요청 (CLI 는 `--force`, GUI 는 다이얼로그).
- **GUI Devices 탭** 은 두 컬럼 host ↔ guest mover 를 제공하고, **시스템 트레이 USB 스위처** 로 전체 윈도를 열지 않고도 USB 디바이스를 넣고 뺄 수 있음.

**GPU 가속:** 아직 미지원. dockur/windows 가 QEMU/KVM 위에서 소프트웨어 그래픽으로 실행 — DirectX 무거운 게임과 3D 앱은 CPU 바운드. VFIO 통한 GPU 패스스루는 가능하지만 패키징 안 됨. ([COMPARISON.md](COMPARISON.ko.md) → WinPodX vs Wine 참조 — GPU 필요하면 Wine + DXVK 가 맞는 도구.)

## 자동화 & 보안

- 자동 suspend / resume: idle 시 컨테이너 pause, 다음 실행 시 resume
- 비밀번호 자동 회전: 20자 암호학적 비밀번호, 7일 주기 + rollback
- 스마트 DPI 스케일링: GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb 에서 자동 감지
- 멀티 백엔드: Podman (기본), Docker, manual RDP
- Windows 빌드를 11 25H2 에 pin (`TargetReleaseVersionInfo=25H2`, 365일 feature-update 지연)
- Windows debloat: 텔레메트리, 광고, Cortana, 검색 인덱싱, 서비스 (DiagTrack / dmwappushservice / WSearch / SysMain) 비활성화
- 고성능 전원 플랜 + hibernation off + tzutil UTC + Cloudflare DNS
- 시간 동기화: 호스트 sleep/wake 후 Windows 시계 강제 resync
- FreeRDP `extra_flags` allowlist (regex 검증) 가 사용자 input 안전 경계

## 베어메탈 위장 (VM 탐지 회피)

v0.7.0 에 탑재. Opt-in, 기본 꺼짐. 하이퍼바이저 감지 시 실행을 거부하는 소프트웨어 — Nvidia GPU 패스스루 "code 43", launch-gate VM 체크, VM 비적합 설치 프로그램 — 가 QEMU/KVM 대신 실물 베어메탈 머신으로 인식합니다. 레벨 선택:

```bash
winpodx config set pod.disguise_level off      # 기본 — 위장 없음
winpodx config set pod.disguise_level balanced # VM 별 args 만, 재빌드 없음
winpodx config set pod.disguise_level max      # 패치된 QEMU 이미지 (Hardened)
```

또는 GUI Settings 페이지 → "Bare-metal level" 셀렉터 사용 (Hardened 선택 시 이미지 빌드가 자동으로 시작).

### 레벨

**`balanced`** (VM 별, 재빌드 불필요):
- CPUID 하이퍼바이저 present 비트와 KVM 시그니처 제거 (`-cpu -hypervisor,kvm=off,-kvm-pv-*`)
- 호스트의 실제 SMBIOS/DMI (시스템 / 보드 / BIOS 벤더 + 제품, CPU 벤더 + 모델) 를 게스트에 미러링
- 합성 SMBIOS 센서/디스크립터 blob (전압 / 온도 프로브, 냉각 장치, 캐시, 메모리 어레이 + DIMM, 40+ 구조체) 주입하여 `Win32_*` / `CIM_*` WMI 센서 클래스가 실제 하드웨어처럼 보고하도록

**`max` ("Hardened")** (`balanced` 의 모든 것 + 추가):
- `winpodx disguise build-image` 가 QEMU 를 **로컬에서** 컴파일 (~20–40 분), 커맨드 라인으로 override 불가한 VM 식별 문자열을 호스트 실제 값으로 재작성: ACPI OEM ID (`BOCHS`/`BXPC` → 호스트), FADT 하이퍼바이저 벤더 + PM-profile 바이트, 디바이스 `_HID`, `WAET` 테이블 시그니처, 디스크 / 광학 모델 **및 INQUIRY 벤더**; thermal-zone SSDT + WSMT 테이블도 주입
- SATA 시스템 디스크, e1000 NIC, std VGA, `nec-usb-xhci` USB-3 컨트롤러 (USB3 유지하면서 Red Hat `VEN_1B36` 특징 제거); virtio-rng 디바이스 (`VEN_1AF4`) 제거 + 게스트 미사용 virtio 드라이버 서비스 키 정리

바이너리는 절대 shipped 되지 않습니다 — 이미지는 표준 QEMU 소스로 본인 머신에서 빌드됩니다.

### 패치된 이미지 빌드

```bash
winpodx disguise build-image    # 첫 빌드 ~20–40 분; 이후 캐시됨
```

또는 GUI Settings → Bare-metal level 에서 "Hardened" 선택 시 빌드가 자동으로 시작.

### 개인정보

호스트에서 파생된 문자열은 *비식별* (벤더 / 모델 코드 등 디스크 모델) 이며 Docker build-arg 를 거쳐 **로컬 이미지 레이어에만 — git 에 절대 커밋되지 않고 push 도 없음**. 시리얼 / UUID / 자산 태그는 읽지 않습니다. 소스는 제네릭 폴백 (`ALASKA` / `Samsung SSD` / `ATA`) 을 ship.

### 검증

실제 Windows 에서 **al-khaser 0.82** 검증 완료: 디스크, 센서, SMBIOS, ACPI, CPUID, virtio 서비스, 사용자명 탐지 계열 clean. 남은 특징들은 컨테이너-QEMU 구조적 바닥 (RDTSC 타이밍, 실제 하드웨어에서도 비어있는 레거시 `Win32_MemoryDevice` 클래스, RDP 디스플레이 드라이버를 공유하는 Windows 내재 Hyper-V 통합 객체).

## Windows 디스크 자동 확장

Windows `C:` 드라이브가 채워질수록 스스로 커짐 — 거대한 가상 디스크를 미리 잡아둘 필요도, 설치 도중 공간이 떨어질 일도 없음.

- **자동 확장** 은 pod 이 idle 일 때만 동작, `C:` 가 거의 가득 차면 확장, 호스트 여유 공간으로 bounded 되어 기반 스토리지를 절대 overcommit 하지 않음. 디스크 끝에 있는 dockur 의 WinRE 복구 파티션도 올바르게 처리.
- **수동 제어**: `winpodx install grow-disk [SIZE|--extend-only]` 로 공간 추가 (또는 기존 여유 공간으로 파티션만 확장), `winpodx install disk-usage` 로 현재 할당 확인.
- Config 키: `disk_autogrow*` (활성 / 임계값 / 단계) 와 `disk_max_size` (상한).

## 게스트 동기화

실행 중인 Windows 게스트에 재설치 없이 호스트 측 업데이트를 push. WinPodX 가 더 새로운 guest agent, urlacl 예약, rdprrap 빌드, post-install fix 를 ship 하면 게스트가 그 자리에서 받아감.

- **자동**: `guest_autosync` 활성 시 pod 시작마다 — 게스트가 올라올 때마다 현재 호스트 버전으로 reconcile.
- **수동**: `winpodx guest sync [--force]` 로 on-demand reconcile (`--force` 는 버전이 이미 일치해도 재-push).

## 앱 프로필

앱 프로필은 **메타데이터만**: Windows 앱이 어디 있는지를 기술해서 WinPodX 가 FreeRDP RemoteApp 통해 실행 가능. 실제 Windows 애플리케이션은 Windows 컨테이너 안에 설치되어 있어야 함.

### 자동 discovery (기본)

v0.1.9 부터 WinPodX 는 **큐레이트된 프로필 리스트 없음**. Windows pod 첫 부팅 시 provisioner 가 `winpodx app refresh` 실행, 실행 중인 게스트를 스캔:

- Registry `App Paths` (`HKLM` + `HKCU`)
- Start Menu `.lnk` 재귀 (depth-cap)
- UWP / MSIX 패키지 — `Get-AppxPackage` + `AppxManifest.xml`
- Chocolatey + Scoop shim

각 결과에 대해 바이너리에서 아이콘 직접 추출 (UWP 는 패키지의 logo 자산) 하고 `~/.local/share/winpodx/discovered/<slug>/` 에 엔트리 작성. 언제든 재실행:

```bash
winpodx app refresh        # CLI
# 또는 GUI All apps 페이지의 "Refresh Apps" 클릭
```

### 사용자 정의 앱 프로필 수동 추가

사용자 작성 프로필은 `~/.local/share/winpodx/apps/` 에 위치, 같은 `name` 의 discovery 결과를 override:

```bash
mkdir -p ~/.local/share/winpodx/apps/myapp
cat > ~/.local/share/winpodx/apps/myapp/app.toml << 'EOF'
name = "myapp"
full_name = "My Application"
executable = "C:\\Program Files\\MyApp\\myapp.exe"
categories = ["Utility"]
mime_types = []
EOF

winpodx app install myapp   # desktop 메뉴에 등록
```

## 멀티세션 RDP

기본 Windows Desktop 에디션은 RDP 를 사용자당 1 세션으로 제한 — 두 번째 앱이 재연결하면서 첫 세션을 빼앗아감. WinPodX 는 [rdprrap](https://github.com/kernalix7/rdprrap) — RDPWrap 의 Rust 재구현 — 을 패키지 내부에 bundle 하고 Windows unattended 설치 중 자동 설치, 그래서 각 RemoteApp 윈도가 독립 세션을 받음.

**RAIL 전제조건.** RemoteApp 자체가 unattended setup 중 WinPodX 가 적용하는 세 개의 레지스트리 설정 필요: `fDisabledAllowList=1` (RemoteApp publishing 활성), `fInheritInitialProgram=1` (`/app:program:...` 가 셸이 아닌 타겟 실행파일을 실행하도록), `MaxInstanceCount=10` + `fSingleSessionPerUser=0` (단일 세션 제한 해제, 최대 10개 동시 RemoteApp 윈도). 이 키들은 rdprrap 설치 성공 여부와 관계없이 설정 — rdprrap 가 세션을 *독립적으로* 만들어주지만, 레지스트리 키들이 RemoteApp 을 일단 동작하게 만드는 것. rdprrap 설치 후 `TermService` 가 cycle 되어 wrapper DLL 이 재부팅 없이 활성화.

**인증 채널.** NLA 비활성 (`UserAuthentication=0`) 으로 FreeRDP 명령줄이 `podman unshare --rootless-netns` 아래에서 unattended 인증 가능, 하지만 `SecurityLayer=2` 가 RDP 채널 자체는 TLS 로 암호화 유지 (그래서 `127.0.0.1` 에 대한 `/sec:tls /cert:ignore` 가 완전 인증 + 암호화 경로 — NLA 가 꺼져있어도 wire 에 평문 없음).

**완전 오프라인 동작.** rdprrap zip 이 WinPodX 의 data 디렉토리 (`config/oem/`) 안에 ship 되고 게스트 첫 부팅 중 `C:\OEM\` 에 stage. 추출 전 pin 파일에 대해 sha256 검증. 설치 시점에 네트워크 접근 불필요.

설치는 일회성: dockur 의 unattended setup 단계 중 패치 적용. 그 단계의 무엇이라도 실패하면 (해시 불일치, 추출, installer 에러), WinPodX 가 경고 로그 + 게스트는 단일 세션 모드 유지 — 앱 실행이 이 단계에서 막히지 않음. guest 측 management 채널 (설치 후 활성/비활성/상태) 은 차후 릴리스 예정.
