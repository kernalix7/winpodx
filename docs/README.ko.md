<div align="center">

<img src="../CI.svg" alt="winpodx" width="320">

### 앱 클릭하면 Word 가 뜬다. 끝.

<p>Windows 앱마다 네이티브 Linux 윈도 — 진짜 아이콘, 진짜 <code>WM_CLASS</code>,<br>
태스크바 핀 가능. FreeRDP RemoteApp + dockur/windows. Zero config.</p>

<pre><code>curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash</code></pre>

[![Beta](https://img.shields.io/badge/status-beta-orange?style=for-the-badge)](#상태-베타)
[![Latest](https://img.shields.io/github/v/release/kernalix7/winpodx?include_prereleases&style=for-the-badge&label=latest&color=2962FF)](https://github.com/kernalix7/winpodx/releases)

[![license](https://img.shields.io/github/license/kernalix7/winpodx?style=flat-square&color=blue)](../LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![tests](https://img.shields.io/badge/tests-411-2EA44F?style=flat-square)](#테스트)
[![CI](https://img.shields.io/github/actions/workflow/status/kernalix7/winpodx/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/kernalix7/winpodx/actions/workflows/ci.yml)
[![stars](https://img.shields.io/github/stars/kernalix7/winpodx?style=flat-square&color=FFD93D&logo=github&logoColor=white)](https://github.com/kernalix7/winpodx/stargazers)
[![downloads](https://img.shields.io/github/downloads/kernalix7/winpodx/total?style=flat-square&color=2EA44F)](https://github.com/kernalix7/winpodx/releases)

###### Works on

[![openSUSE](https://img.shields.io/badge/openSUSE-73BA25?style=flat-square&logo=opensuse&logoColor=white)](https://www.opensuse.org/)
[![Fedora](https://img.shields.io/badge/Fedora-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/)
[![Debian](https://img.shields.io/badge/Debian-A81D33?style=flat-square&logo=debian&logoColor=white)](https://www.debian.org/)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=flat-square&logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![RHEL family](https://img.shields.io/badge/RHEL%20%2F%20Alma%20%2F%20Rocky-EE0000?style=flat-square&logo=redhat&logoColor=white)](https://www.redhat.com/)
[![Arch (AUR pending)](https://img.shields.io/badge/Arch%20(AUR)-pending-lightgrey?style=flat-square&logo=archlinux&logoColor=white)](../packaging/aur/README.md)

<sub>[English](../README.md) &nbsp;·&nbsp; **한국어** &nbsp;·&nbsp; [빠른 시작](#빠른-시작) &nbsp;·&nbsp; [기능](#주요-기능) &nbsp;·&nbsp; [CLI](#cli-레퍼런스) &nbsp;·&nbsp; [멀티세션](#multi-session-rdp)</sub>

</div>

---

> ### 상태: 베타
> winpodx는 활발히 개발 중입니다 (v0.2.0.x). 설치 경로, FreeRDP RemoteApp 통합, Windows-side runtime apply, discovery 흐름이 v0.1.9 → v0.2.0.x 동안 많이 개선됐지만, 여전히 거친 부분이 남아있을 수 있습니다 — 특히 첫 설치 시 (Windows VM 첫 부팅에 5~10분 소요; 진행 상황은 `winpodx pod wait-ready --logs` 로 확인). 문제 발생 시 <https://github.com/kernalix7/winpodx/issues> 에 이슈 등록해주세요.

**Full-screen RDP 아님.** Windows 앱이 각각 네이티브 Linux 윈도 — pin 가능, alt-tab 됨, 파일 연결 동작. 진짜 Windows 데스크톱 필요할 때만 `winpodx app run desktop`.

winpodx는 백그라운드에서 Windows 컨테이너([dockur/windows](https://github.com/dockur/windows))를 실행하고, FreeRDP RemoteApp으로 Windows 앱을 네이티브 Linux 앱처럼 표시합니다. VM 수동 설정 불필요, ISO 다운로드 불필요, 레지스트리 편집 불필요. **외부 Python 의존성 거의 없음** (Python 3.11+ 는 표준 라이브러리만; 3.9/3.10 은 순수 파이썬 `tomli` 폴백 1개).

## 왜 winpodx인가?

Linux에서 Windows 앱을 실행하는 기존 도구들은 각각 한계가 있습니다:

| | winapps | LinOffice | winboat | winpodx |
|---|---|---|---|---|
| 핵심 기술 | dockur + FreeRDP | dockur + FreeRDP | dockur + FreeRDP | dockur + FreeRDP |
| 설정 | 수동 (셸 + 설정 파일 + RDP 테스트) | 원라인 스크립트 | 원클릭 GUI 설치 | **제로 설정** (첫 실행 시 자동) |
| 인터페이스 | CLI 만 | CLI 만 | Electron GUI | **Qt6 GUI + CLI + 트레이** |
| 앱 범위 | 모든 Windows 앱 | Office 전용 | 모든 Windows 앱 | 모든 Windows 앱 |
| 언어 | Shell (86%) | Shell + Python | TypeScript / Vue / Go | **Python (100%)** |
| 런타임 의존성 | curl, dialog, git, netcat | Podman, FreeRDP | Electron, Docker/Podman, FreeRDP | **Python 3.9+, FreeRDP, Podman** |
| 자동 일시정지 / 재개 | 없음 | 없음 | 명시 안됨 | **있음 (idle timeout)** |
| 비밀번호 회전 | 없음 | 없음 | 명시 안됨 | **있음 (7일, 원자적)** |
| HiDPI 자동 감지 | 없음 | 없음 | 명시 안됨 | **GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb** |
| 사운드 기본 | 없음 | 없음 | 있음 (FreeRDP) | 있음 (FreeRDP) |
| 프린터 리다이렉션 기본 | 없음 | 없음 | 명시 안됨 | 있음 (FreeRDP) |
| USB 드라이브 자동 매핑 | 없음 | 없음 | 스마트카드 패스스루 | **드라이브 서브폴더 → 드라이브 문자 (FileSystemWatcher)** |
| 디스커버리 (설치된 앱 자동 스캔) | 없음 | 없음 | 있음 | **있음 (Registry + Start Menu + UWP + choco/scoop)** |
| 멀티 세션 RDP | 없음 | 없음 | 명시 안됨 | **있음 (번들 rdprrap, 최대 10)** |
| 오프라인 / 에어갭 설치 | 없음 | 없음 | 없음 | **있음 (`--source` + `--image-tar`)** |
| 라이선스 | MIT | AGPL-3.0 | MIT | MIT |

> winboat 가 가장 가까운 동급 프로젝트이고 영감 중 하나입니다. winpodx 는 다른 조합을 추구합니다 — Electron 대신 stdlib 중심 Python + Qt6, 더 깊은 자동 설정 (auto suspend, 7일 비밀번호 회전, 다중 DE HiDPI), 명시적 에어갭 설치 경로. 두 프로젝트 모두 dockur/windows 위에 빌드되어 있고, 그 생태계는 어떤 한 앱보다도 큽니다.

## winpodx vs Wine

**winpodx 는 Wine 대체재가 아닙니다.** Wine 은 Windows API 호출을 Linux 로 번역하고, winpodx 는 실제 Windows OS 를 컨테이너에서 돌립니다. 두 도구는 다른 문제를 풉니다 — 많은 사용자가 둘 다 설치합니다.

| 필요한 게... | 사용 |
|---|---|
| 오래된 Win32 앱, 인디 게임, 가벼운 유틸 | **Wine / Bottles / Lutris** |
| GPU 가속 게임 / 3D 앱 (DirectX 9 ~ 12) | **Wine** — DXVK / VKD3D 가 거의 네이티브 프레임 레이트. winpodx 는 GPU 패스스루 기본 미지원, QEMU CPU 렌더링은 훨씬 느림. (VFIO 기반 GPU 패스스루는 가능하지만 별도 수동 설정 필요, 패키징 안 됨.) |
| Outlook + Teams + OneDrive 풀 통합된 Microsoft 365 | **winpodx** |
| Adobe Creative Suite (Photoshop, Illustrator, Premiere, Lightroom) | winpodx — 단 무거운 GPU 효과는 CPU bound (위 GPU 행 참조) |
| 안티치트 게임 (Valorant, EAC, BattlEye 계열) | **TBD** — 안티치트마다 VM 감지 정책 다름 (Vanguard 는 TPM 2.0 + 하이퍼바이저 없음 요구, EAC 는 대부분 VM 차단, VAC 는 관대). 본격 사용 전 테스트 필요. |
| DRM 무거운 소프트웨어 / 하드웨어 동글 앱 | **winpodx** |
| 커널 모드 드라이버 동반 앱 (일부 VPN, 보안 소프트웨어) | **winpodx** |
| 지역 인증서 필요한 은행 / 세무 / 공공기관 도구 | **winpodx** |
| Visual Studio, WinUI 3 / WinRT, Wine 이 못 따라가는 .NET 기능 | **winpodx** |
| IE 전용 레거시 사내 웹앱 | **winpodx** |
| "대충 됨" 이 허용 안 되는 모든 경우 | **winpodx** |

Wine 은 속도와 GPU (DXVK/VKD3D 가 깔끔히 번역해줄 때) 에서 이깁니다. winpodx 는 **그 외 모든 경우에서 100% Windows 기능 호환성** 으로 이깁니다 — 진짜 Windows 커널 위에서 앱이 돌고, FreeRDP RemoteApp 으로 Linux 데스크톱에 네이티브 윈도로 렌더링될 뿐입니다.

## 주요 기능

<table>
<tr><td width="50%">

**심리스 앱 창**
- RemoteApp (RAIL)으로 각 앱을 네이티브 Linux 창으로 렌더링 (전체 데스크톱 없음)
- 앱별 독립 작업 표시줄 아이콘 (`/wm-class:<stem>` + `StartupWMClass` 매칭)
- 파일 연결: 파일 관리자에서 `.docx` 더블클릭 → Word 실행
- 멀티세션 RDP: 번들된 rdprrap 가 최대 10개 독립 세션을 자동 활성화
- RAIL 전제 레지스트리 (`fDisabledAllowList=1` + `fInheritInitialProgram=1` + `MaxInstanceCount=10`) 를 무인 설치 중 자동 설정

</td><td width="50%">

**제로 설정 실행**
- 첫 앱 클릭 시 모든 것을 자동 프로비저닝: 설정, 컨테이너, 데스크톱 엔트리
- **첫 부팅 시 자동 발견** — winpodx 가 실행 중인 Windows 게스트를 스캔해 설치된 모든 앱 (Registry App Paths, Start Menu, UWP/MSIX, Chocolatey, Scoop) 을 등록하고 실제 바이너리에서 추출한 아이콘을 사용
- `winpodx app refresh` 또는 GUI Refresh 버튼으로 언제든 재스캔 가능
- 고급 설정을 위한 대화형 설정 위자드

</td></tr>
<tr><td width="50%">

**주변기기 및 공유**
- **클립보드**: 양방향 복사-붙여넣기 (텍스트 + 이미지) 기본 활성화
- **사운드**: RDP 오디오 스트리밍 (`/sound:sys:alsa`) 기본 활성화
- **프린터**: Linux 프린터를 Windows 로 RDP 리다이렉션 (기본)
- **USB 드라이브**: Linux 마운트 트리가 `\\tsclient\media` 로 공유; 세션 시작 후 꽂힌 드라이브도 서브폴더로 접근 가능
- **USB 드라이브 자동 매핑**: Windows 측 FileSystemWatcher 스크립트가 `\\tsclient\media\<USB>` 서브폴더를 드라이브 문자 (E:, F:, ...) 로 자동 매핑
- **USB 장치 패스스루**: `/usb:auto` 가 allowlist 에 있지만 **기본 비활성화** — FreeRDP 빌드에 urbdrc 플러그인 있으면 `extra_flags` 로 opt-in
- **홈 디렉토리**: `\\tsclient\home` 으로 공유 (기본)
- **데스크탑 바로가기**: 첫 부팅 시 Windows 바탕화면에 `\\tsclient\home` ("Home"), `\\tsclient\media` ("USB") 바로가기 자동 생성

**GPU 가속:** 아직 미지원. dockur/windows 가 QEMU/KVM + 소프트웨어 그래픽으로 동작 — DirectX 무거운 게임 / 3D 앱은 CPU bound. VFIO 기반 GPU 패스스루는 가능하지만 패키징 안 됨. (GPU 필요한 경우 [winpodx vs Wine](#winpodx-vs-wine) 참조 — Wine + DXVK 가 정답.)

</td><td width="50%">

**자동화 및 보안**
- 자동 일시정지/재개: 유휴 시 컨테이너 일시정지, 다음 실행 시 자동 재개
- 비밀번호 자동 로테이션: 20자 암호학적 비밀번호, 7일 주기, 롤백 지원
- 스마트 DPI 스케일링: GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb 자동 감지
- Qt6 시스템 트레이 + 전체 Qt6 메인 윈도우 (Apps / Settings / Tools / Terminal)
- 멀티 백엔드: Podman (기본), Docker, libvirt/KVM, 수동 RDP
- Windows 빌드 고정: 11 25H2 (`TargetReleaseVersionInfo=25H2`, 365일 기능 업데이트 연기)
- Windows 디블로트: 텔레메트리, 광고, Cortana, 검색 인덱싱 및 서비스 (DiagTrack / dmwappushservice / WSearch / SysMain) 비활성화
- 고성능 전원 관리 + 최대 절전 해제 + tzutil UTC + Cloudflare DNS 기본값
- 시간 동기화: 호스트 sleep/wake 후 Windows 시계 강제 재동기화
- FreeRDP `extra_flags` 허용 목록 (정규식 검증) — 사용자 입력 안전 경계

</td></tr>
</table>

## 동작 방식

```
                     ┌─────────────────────────────┐
  앱 메뉴에서         │     Linux 데스크톱 (KDE,      │
  "Word" 클릭  ───>  │     GNOME, Sway, ...)        │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │         winpodx              │
                     │  ┌─────────────────────┐     │
                     │  │ 자동 프로비저닝:       │     │
                     │  │  설정 → 비밀번호      │     │
                     │  │  → 컨테이너 → RDP    │     │
                     │  │  → 데스크톱 엔트리    │     │
                     │  └─────────────────────┘     │
                     └──────────────┬──────────────┘
                                    │ FreeRDP RemoteApp
                     ┌──────────────▼──────────────┐
                     │   Windows 컨테이너 (Podman)   │
                     │   ┌──────────────────────┐   │
                     │   │  Word  Excel  PPT ... │   │
                     │   │ rdprrap 멀티세션       │   │
                     │   └──────────────────────┘   │
                     │   127.0.0.1:3390 (TLS)       │
                     └─────────────────────────────┘
```

## GUI

`winpodx gui` 명령으로 실행. Qt6 메인 윈도우는 4개 페이지로 구성되어 있습니다:

| 페이지 | 내용 |
|--------|------|
| **Apps** | 설치된 앱 프로필 그리드 / 리스트 뷰, 검색 + 카테고리 필터, 앱별 3초 쿨다운 런치, 앱 프로필 Add / Edit / Delete 다이얼로그 |
| **Settings** | RDP (사용자 / IP / 포트 / 스케일 / DPI / 비밀번호 로테이션) + Container (백엔드 / CPU / RAM / 유휴 타임아웃) 를 한 화면에 |
| **Tools** | Suspend / Resume / Full Desktop 버튼, Clean Locks / Sync Time / Debloat, 그리고 Windows Update **enable / disable** 원클릭 토글 |
| **Terminal** | 명령 허용 목록 (`podman`, `docker`, `virsh`, `winpodx`, `xfreerdp`, `systemctl`, `journalctl`, `ss`, `ip`, `ping`, ...) 으로 제한된 임베디드 셸. 빠른 버튼 제공 (Status / Logs / Inspect / RDP Test / Clear) |

시스템 트레이 (`winpodx tray`) 는 경량 대안입니다 — 팟 제어, 앱 런처 서브메뉴 (상위 20개 + Full Desktop), 유지보수 서브메뉴 (Clean Locks / Sync Time / Suspend), 선택적 유휴 모니터 스레드.

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| 언어 | Python 3.9+ (3.11+ 는 표준 라이브러리만; 3.9/3.10 은 `tomli` 폴백) |
| CLI | argparse (표준 라이브러리) |
| GUI (선택) | PySide6 (Qt6) |
| 설정 | TOML (3.11+ 는 표준 라이브러리 `tomllib` / 3.9/3.10 은 `tomli`; 내장 writer) |
| RDP | FreeRDP 3+ (xfreerdp, RemoteApp/RAIL) |
| 컨테이너 | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |
| CI | GitHub Actions (lint + test on 3.9-3.13 + pip-audit) |

## 빠른 시작

### 설치

**원 라인 설치** (지원하는 모든 Linux 배포판):

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

배포판 자동 감지 → 누락된 시스템 의존성 (Podman, FreeRDP, KVM, Python 3.9+) 을
사용자 확인 후 설치 → winpodx 를 `~/.local/bin/winpodx-app/` 에 배치 → 14개
Windows 앱을 데스크탑 메뉴에 등록. 의존성 설치 단계 외에는 root 권한 불필요.
openSUSE, Fedora, Debian/Ubuntu, RHEL 계열, Arch 지원.

**오프라인 / 에어갭 설치** — 레지스트리 / 패키지 레포 접근이 없는 환경을 위해
3개 플래그 제공:

```bash
# git clone 대신 로컬 클론 경로 사용 (환경변수: WINPODX_SOURCE)
./install.sh --source /media/usb/winpodx

# 첫 부팅 시 registry pull 없이 Windows 이미지 tar 미리 로드 (환경변수: WINPODX_IMAGE_TAR)
./install.sh --image-tar /media/usb/windows-image.tar

# 배포판 의존성 설치 단계 스킵 (환경변수: WINPODX_SKIP_DEPS=1) — 필수 도구 부재 시 즉시 실패
./install.sh --skip-deps

# 셋 다 조합:
./install.sh --source /media/usb/winpodx --image-tar /media/usb/windows-image.tar --skip-deps
```

환경변수는 `curl | bash` 에서도 동작하므로
`WINPODX_SKIP_DEPS=1 curl ... | bash` 형태 사용 가능.

**원 라인 삭제** — 파이프 실행에서는 `--confirm` 또는 `--purge` 플래그가 필수입니다
(bash 가 curl 의 stdin 을 소비 중이라 대화형 프롬프트가 터미널을 읽을 수 없음):

```bash
# winpodx 파일만 삭제, Windows 컨테이너/데이터는 보존
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm

# 완전 제거: 컨테이너, 볼륨, 설정, 런처까지 전부
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
```

배포판 패키지 매니저로 설치하려면? 모든
[GitHub Release](https://github.com/kernalix7/winpodx/releases/latest)
에 RPM / `.deb` / AUR 패키지가 자동 첨부됩니다. openSUSE/Fedora RPM 은
[openSUSE Build Service (`home:Kernalix7/winpodx`)](https://build.opensuse.org/package/show/home:Kernalix7/winpodx)
에서, 나머지는 GitHub Actions 에서 빌드/발행:

**openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll**

```bash
sudo zypper addrepo \
  https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper refresh
sudo zypper install winpodx
```

`openSUSE_Tumbleweed` 부분은 `openSUSE_Leap_16.0`, `openSUSE_Leap_15.6`,
`openSUSE_Slowroll` 등으로 교체 가능.

**Fedora 42 / 43**

```bash
sudo dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx
```

**Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10**

[최신 Release](https://github.com/Kernalix7/winpodx/releases/latest) 에서
본인 배포판에 맞는 `.deb` 를 다운받아 설치:

```bash
sudo apt install ./winpodx_<version>_all_debian13.deb   # 배포판에 맞게 선택
```

**AlmaLinux / Rocky / RHEL 9 & 10**

el9 에서는 `python3-tomli` 때문에 EPEL 이 필요합니다.
[최신 Release](https://github.com/Kernalix7/winpodx/releases/latest) 에서
`.rpm` 을 받아 설치:

```bash
sudo dnf install epel-release                     # el9 만 필요
sudo dnf install ./winpodx-<version>-1.noarch.el9.rpm   # 또는 .el10.rpm
```

**Arch Linux (AUR)**

> 참고: AUR 자동 발행은 인프라만 준비되어 있고 메인테이너 1회 세팅이 완료되기
> 전까지는 비활성 상태입니다 (자세한 절차는
> [`packaging/aur/README.md`](../packaging/aur/README.md)). 활성화 이후에는
> 태그 푸시마다 자동 발행됩니다.

```bash
yay -S winpodx        # 또는:
paru -S winpodx
```

**소스에서 (개발용)**

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

소스 설치 스크립트가 자동으로:
1. 배포판 감지 (openSUSE, Fedora, Ubuntu, Arch, ...)
2. 없는 의존성 설치 (Podman, FreeRDP, KVM), 설치 전 확인
3. winpodx를 `~/.local/bin/winpodx-app/`에 복사
4. 설정 및 compose.yaml 생성
5. 첫 포드 부팅 시 자동 발견 (`winpodx app refresh`) 이 메뉴를 채움

### 실행

```bash
winpodx app run word              # Word 실행
winpodx app run word ~/문서.docx   # 파일과 함께 실행
winpodx app run desktop           # 전체 Windows 데스크톱
```

또는 앱 메뉴에서 아이콘을 클릭하세요.

### 직접 실행 (설치 없이)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

---

## CLI 참조

<details>
<summary><b>전체 CLI 참조 펼치기</b></summary>

```bash
# 앱
winpodx app list                  # 사용 가능한 앱 목록
winpodx app run word              # Word 실행 (첫 실행 시 자동 프로비저닝)
winpodx app run word ~/doc.docx   # 파일과 함께 실행
winpodx app run desktop           # 전체 Windows 데스크톱 세션
winpodx app install-all           # 전체 앱 데스크톱 메뉴 등록
winpodx app sessions              # 활성 세션 확인
winpodx app kill word             # 세션 종료

# 팟 관리
winpodx pod start --wait          # 시작 + RDP 준비 대기
winpodx pod stop                  # 정지 (활성 세션 경고)
winpodx pod status                # 세션 수 포함 상태
winpodx pod restart
winpodx pod apply-fixes           # Windows 런타임 수정 재적용 (멱등)
winpodx pod sync-password         # 비밀번호 drift 복구 (cfg ↔ Windows)
winpodx pod multi-session on      # 번들 rdprrap 다중 세션 RDP 토글
winpodx pod multi-session status
winpodx pod wait-ready --logs     # Windows 첫 부팅 대기 (진행 + 컨테이너 로그)

# 전원 관리
winpodx power --suspend           # 일시정지 (CPU 해제, 메모리 유지)
winpodx power --resume            # 재개

# 보안
winpodx rotate-password           # Windows RDP 비밀번호 수동 변경

# 유지보수
winpodx cleanup                   # Office 잠금 파일 제거 (~$*.*)
winpodx timesync                  # Windows 시간 강제 동기화
winpodx debloat                   # 텔레메트리, 광고, 블로트 비활성화
winpodx uninstall                 # winpodx 파일 제거 (컨테이너 유지)
winpodx uninstall --purge         # 설정 포함 전체 제거

# 시스템
winpodx setup                     # 대화형 설정 위자드
winpodx info                      # 디스플레이, 의존성, 설정 진단
winpodx gui                       # Qt6 메인 윈도우 실행 (Apps / Settings / Tools / Terminal)
winpodx tray                      # Qt 시스템 트레이 아이콘
winpodx config show               # 현재 설정 확인
winpodx config set rdp.scale 140  # 설정 값 변경
winpodx config import             # 기존 winapps.conf 가져오기
```

</details>

## 주변기기 및 공유

| 기능 | 동작 방식 | 기본값 |
|------|----------|--------|
| **클립보드** | RDP를 통한 양방향 복사-붙여넣기 (`+clipboard`) | 활성화 |
| **사운드** | ALSA를 통한 오디오 스트리밍 (`/sound:sys:alsa`) | 활성화 |
| **프린터** | Linux 프린터를 Windows에 공유 (`/printer`) | 활성화 |
| **홈 디렉토리** | `\\tsclient\home`으로 공유 (`+home-drive`) | 활성화 |
| **USB 드라이브** | 미디어 폴더를 `\\tsclient\media`로 공유 (`/drive:media`); 세션 시작 후 꽂은 USB도 하위 폴더로 접근 가능 | 활성화 |
| **USB 장치 패스스루** | 네이티브 USB 리다이렉션 (`/usb:auto`) — FreeRDP urbdrc 플러그인 필요 | **opt-in** (`extra_flags` 로 활성화) |
| **USB 드라이브 매핑** | Windows 측 스크립트가 USB 하위 폴더를 드라이브 문자(E:, F:, ...)로 자동 매핑 (FileSystemWatcher) | 활성화 |

### USB 드라이브 흐름

```
Linux에서 USB 꽂기
    │
    ▼
Linux가 /run/media/$USER/USBNAME에 마운트
    │
    ▼
FreeRDP가 \\tsclient\media\USBNAME으로 공유
    │
    ▼
media_monitor.ps1이 감지 → net use E: \\tsclient\media\USBNAME
    │
    ▼
Windows 탐색기에 E: 드라이브 표시
```

## 설정

설정 파일: `~/.config/winpodx/winpodx.toml` (자동 생성, 0600 권한)

```toml
[rdp]
user = "User"
password = ""                # 자동 생성 랜덤 비밀번호
password_updated = ""        # ISO 8601 타임스탬프
password_max_age = 7         # 자동 변경 주기 (일, 0 = 비활성화)
ip = "127.0.0.1"
port = 3390
scale = 100                  # DE에서 자동 감지
dpi = 0                      # Windows DPI % (0 = 자동)
extra_flags = ""             # 추가 FreeRDP 플래그 (허용 목록)

[pod]
backend = "podman"
win_version = "11"                               # 11 | 10 | ltsc10 | tiny11 | tiny10
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = true                                # 앱 실행 시 자동 팟 시작
idle_timeout = 0                                 # 자동 일시정지 (초, 0 = 비활성화)
boot_timeout = 300                               # 최초 부팅 무인 설치 대기 시간 (초)
image = "ghcr.io/dockur/windows:latest"          # 컨테이너 이미지 (에어갭 미러 지정 가능)
disk_size = "64G"                                # dockur 에 전달되는 가상 디스크 크기
```

## 앱 프로필

앱 프로필은 **메타데이터 전용**입니다. winpodx 가 FreeRDP RemoteApp 으로 실행할 수 있도록 Windows 앱의 위치를 알려줄 뿐, 앱 자체가 아닙니다. 실제 Windows 앱은 Windows 컨테이너 안에 설치해야 합니다.

### 자동 발견 (기본)

v0.1.9 부터 winpodx 는 **번들 프로필을 더 이상 제공하지 않습니다**. Windows 포드 첫 부팅 직후 provisioner 가 `winpodx app refresh` 를 자동 실행하면 게스트를 스캔합니다:

- Registry `App Paths` (`HKLM` + `HKCU`)
- Start Menu `.lnk` 재귀 (depth 캡)
- UWP / MSIX (`Get-AppxPackage` + `AppxManifest.xml`)
- Chocolatey + Scoop shim

각 결과에서 바이너리 (또는 UWP 패키지의 로고 자산) 직접 아이콘을 추출해 `~/.local/share/winpodx/discovered/<slug>/` 에 기록합니다. 언제든 재실행:

```bash
winpodx app refresh        # CLI
# 또는 GUI Apps 페이지의 "Refresh Apps" 버튼 클릭
```

<details>
<summary><b>커스텀 앱 프로필 수동 추가</b></summary>

사용자 작성 프로필은 `~/.local/share/winpodx/apps/` 에 두면 같은 `name` 의 발견 결과를 덮어씁니다:

```bash
mkdir -p ~/.local/share/winpodx/apps/myapp
cat > ~/.local/share/winpodx/apps/myapp/app.toml << 'EOF'
name = "myapp"
full_name = "My Application"
executable = "C:\\Program Files\\MyApp\\myapp.exe"
categories = ["Utility"]
mime_types = []
EOF

winpodx app install myapp   # 데스크톱 메뉴에 등록
```

</details>

## 멀티세션 RDP

기본 Windows Desktop 에디션은 사용자당 RDP 세션 1개로 제한되며, 두 번째 앱을
열면 기존 세션을 빼앗아 재연결됩니다. winpodx 는
[rdprrap](https://github.com/kernalix7/rdprrap) — RDPWrap 의 Rust 재구현 —
을 패키지 자체에 번들로 포함하며, Windows 무인 설치 단계에서 자동 적용해 각
RemoteApp 창이 독립된 세션을 갖도록 만듭니다.

**RAIL 전제 조건.** RemoteApp 자체가 동작하려면 3개의 레지스트리 값이
필요하며, winpodx 는 무인 설치 중 이를 자동 적용합니다:
`fDisabledAllowList=1` (RemoteApp 게시 활성화), `fInheritInitialProgram=1`
(`/app:program:...` 가 셸 대신 지정한 실행 파일을 실행하도록),
`MaxInstanceCount=10` + `fSingleSessionPerUser=0` (단일 세션 제한 해제,
최대 10개 동시 RemoteApp 창). 이 키들은 rdprrap 설치 성공 여부와 무관하게
적용됩니다 — rdprrap 은 세션을 *독립적*으로 만들어 주는 것이고, 레지스트리
키는 RemoteApp 자체가 켜지게 만드는 부분입니다. rdprrap 설치 후 wrapper DLL
활성화를 위해 `TermService` 를 재기동합니다 (재부팅 불필요).

**인증 채널.** `podman unshare --rootless-netns` 내부에서 FreeRDP 가 무인
인증을 수행할 수 있도록 NLA 를 비활성화 (`UserAuthentication=0`) 하지만,
`SecurityLayer=2` 로 RDP 채널 자체는 TLS 로 암호화됩니다. 즉
`127.0.0.1` 상대로 `/sec:tls /cert:ignore` 를 쓰는 구성이 "인증 + 암호화"
완전 경로이며, NLA 가 꺼져 있더라도 평문이 와이어에 노출되지 않습니다.

**완전 오프라인 동작.** rdprrap zip 은 winpodx 데이터 디렉토리
(`config/oem/`) 안에 함께 배포되며, 게스트 최초 부팅 시 `C:\OEM\` 로
스테이징됩니다. 핀 파일과 sha256 이 일치하는지 확인한 뒤에만 압축을 풉니다.
설치 시점에 네트워크 접근은 필요하지 않습니다.

설치는 1회성입니다. dockur 의 무인 설치 단계에서 패치가 적용되며, 그 단계에서
문제가 생기더라도(해시 불일치, 압축 해제 실패, 설치기 오류) winpodx 는 경고만
남기고 단일 세션 상태를 유지합니다. 앱 실행은 이 단계에서 블록되지 않습니다.
게스트 측 관리 채널(설치 후 enable/disable/status)은 향후 릴리즈로 예정되어
있습니다.

## 설치 / 삭제

```bash
# 클론한 repo 에서:
./install.sh                # 설치 (배포판 감지, 의존성 설치, 앱 등록)
./uninstall.sh              # 삭제 (대화형, 단계별 확인)
./uninstall.sh --confirm    # 삭제 (자동, 설정 보존)
./uninstall.sh --purge      # 삭제 (설정 포함 전체 제거)

# 또는 원 라인 (클론 불필요):
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh   | bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
```

**삭제 시 winpodx 파일만 제거합니다.** 절대 건드리지 않는 것:
- Podman 컨테이너/볼륨 (Windows VM 데이터)
- 시스템 패키지 (podman, freerdp, python3)
- 홈 디렉토리 파일

## 프로젝트 구조

```
winpodx/
├── install.sh             # 원라인 설치 (pip 불필요)
├── uninstall.sh           # 깔끔한 삭제
├── src/winpodx/
│   ├── cli/               # argparse 명령 (app, pod, config, setup, ...)
│   ├── core/              # 설정, RDP, 팟 생명주기, 프로비저너, 데몬
│   ├── backend/           # Podman, Docker, libvirt, 수동
│   ├── desktop/           # .desktop 엔트리, 아이콘, MIME, 트레이, 알림
│   ├── display/           # X11/Wayland 감지, DPI 스케일링
│   ├── gui/               # Qt6 메인 윈도우, 앱 다이얼로그, 테마
│   └── utils/             # XDG 경로, 의존성, TOML writer, winapps 호환
├── data/                  # winpodx GUI 데스크톱 엔트리 + 아이콘 + 설정 예시
├── config/oem/            # Windows OEM 스크립트 (포스트인스톨)
├── scripts/windows/       # PowerShell 스크립트 (디블로트, 시간 동기화, USB 매핑, 앱 발견)
├── .github/workflows/     # CI: lint + test on 3.9-3.13 + pip-audit
└── tests/                 # pytest 테스트 스위트 (411개 테스트)
```

## 지원 배포판

| 배포판 | 패키지 매니저 | 상태 |
|--------|-------------|------|
| openSUSE Tumbleweed/Leap | zypper | 테스트됨 |
| Fedora / RHEL / CentOS | dnf | 지원 |
| Ubuntu / Debian / Mint | apt | 지원 |
| Arch / Manjaro | pacman | 지원 |

## 테스트

```bash
# 저장소 루트에서 (설치 불필요)
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/ -v    # 411개 테스트
ruff check src/ tests/         # 린트
```

## 기여

개발 설정 및 워크플로우는 [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md)를 참조하세요.

## 릴리즈 및 패키징

태그 (`v*.*.*`) 푸시 시 모든 지원 채널로 자동 배포됩니다:

| 채널 | 배포판 |
|------|--------|
| RPM (openSUSE / Fedora / Slowroll) | Tumbleweed, Leap 15.6, Leap 16.0, Slowroll, Fedora 42/43 |
| RPM (RHEL 계열) | AlmaLinux 9 / 10 (RHEL, Rocky, Oracle Linux 9/10 에도 설치 가능) |
| `.deb` | Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10 |
| AUR | Arch Linux (활성화 이후 — [`packaging/aur/README.md`](../packaging/aur/README.md) 참조) |
| `sdist` + `wheel` | PyPI 호환 소스/바이너리 배포판 |

각 채널별 메인테이너 설정은 [`packaging/`](../packaging/) 아래에 있습니다:
- [`packaging/obs/README.md`](../packaging/obs/README.md) — openSUSE Build Service (RPM 계열).
- [`packaging/aur/README.md`](../packaging/aur/README.md) — Arch User Repository.
- Debian/Ubuntu 및 AlmaLinux 빌드는 각자의 GitHub Actions 워크플로우에서 자체 완결되어 별도 설정 불필요.

## 보안

보안 이슈는 [SECURITY.ko.md](SECURITY.ko.md)의 절차를 따라 주세요.

## 라이선스

[MIT](LICENSE) - Kim DaeHyun (kernalix7@kodenet.io)
