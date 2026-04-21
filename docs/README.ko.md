<div align="center">

<img src="../data/winpodx-icon.svg" alt="winpodx" width="128">

# winpodx

**Linux에서 Windows 앱을 심리스하게 실행**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![Backend: Podman](https://img.shields.io/badge/Backend-Podman-purple.svg)](https://podman.io/)
[![Tests: 225 passed](https://img.shields.io/badge/Tests-225%20passed-brightgreen.svg)](#테스트)

[English](../README.md) | **한국어**

*Linux 앱 메뉴에서 아이콘을 클릭합니다. Word가 열립니다. 끝.*

</div>

---

winpodx는 백그라운드에서 Windows 컨테이너([dockur/windows](https://github.com/dockur/windows))를 실행하고, FreeRDP RemoteApp으로 Windows 앱을 네이티브 Linux 앱처럼 표시합니다. VM 수동 설정 불필요, ISO 다운로드 불필요, 레지스트리 편집 불필요. **외부 Python 의존성 없음** (표준 라이브러리만 사용, Python 3.11+).

## 왜 winpodx인가?

Linux에서 Windows 앱을 실행하는 기존 도구들은 각각 한계가 있습니다:

| | winapps | LinOffice | winpodx |
|---|---------|-----------|---------|
| 핵심 기술 | dockur/windows + FreeRDP | dockur/windows + FreeRDP | dockur/windows + FreeRDP |
| 설정 | 수동 (셸 스크립트, 설정 파일, RDP 테스트) | 원라인 스크립트 | **제로 설정** (첫 실행 시 자동) |
| 앱 범위 | 모든 Windows 앱 | Office 전용 | **모든 Windows 앱** |
| 언어 | Shell (86%) | Shell (61%) + Python | **Python (100%)** |
| 의존성 | curl, dialog, git, netcat | Podman, FreeRDP | **Python 3.11+ (표준 라이브러리만)** |
| 자동 일시정지 | 없음 | 없음 | **있음** |
| 비밀번호 로테이션 | 없음 | 없음 | **있음 (7일 주기)** |
| HiDPI | 없음 | 없음 | **자동 감지** |
| 사운드 / 프린터 | 없음 | 없음 | **있음 (기본 활성화)** |
| USB 공유 | 없음 | 없음 | **있음 (자동 드라이브 매핑)** |
| 시스템 트레이 | 없음 | 없음 | **Qt6 트레이** |
| 라이선스 | MIT | AGPL-3.0 | **MIT** |

## 주요 기능

<table>
<tr><td width="50%">

**심리스 앱 창**
- RemoteApp (RAIL)으로 각 앱을 네이티브 Linux 창으로 렌더링 (전체 데스크톱 없음)
- 앱별 독립 작업 표시줄 아이콘 (WM_CLASS 매칭)
- 파일 연결: 파일 관리자에서 `.docx` 더블클릭 → Word 실행
- 멀티세션 지원 (앱별 독립 RDP 세션) 계획 중

</td><td width="50%">

**제로 설정 실행**
- 첫 앱 클릭 시 모든 것을 자동 프로비저닝: 설정, 컨테이너, 데스크톱 엔트리
- 14개 번들 앱 프로필 (Office, VS Code, Windows 기본 도구)
- 간단한 TOML 정의로 모든 Windows 앱 추가 가능
- 고급 설정을 위한 대화형 설정 위자드

</td></tr>
<tr><td width="50%">

**주변기기 및 공유**
- **클립보드**: 양방향 복사-붙여넣기 (텍스트 + 이미지) 기본 활성화
- **사운드**: RDP 오디오 스트리밍 (`/sound:sys:alsa`) 기본 활성화
- **프린터**: Linux 프린터를 RDP 리다이렉션으로 Windows에 공유
- **USB 드라이브**: `/drive:media`로 자동 공유, 세션 시작 후 꽂은 USB도 접근 가능
- **USB 장치**: FreeRDP urbdrc 플러그인 사용 가능 시 네이티브 USB 리다이렉션 (`/usb:auto`)
- **USB 자동 드라이브 매핑**: Windows 측 FileSystemWatcher 스크립트가 USB 폴더를 드라이브 문자(E:, F:, ...)로 자동 매핑
- **홈 디렉토리**: `\\tsclient\home`으로 파일 접근 공유

</td><td width="50%">

**자동화 및 보안**
- 자동 일시정지/재개: 유휴 시 컨테이너 일시정지, 다음 실행 시 자동 재개
- 비밀번호 자동 로테이션: 20자 암호학적 비밀번호, 7일 주기, 롤백 지원
- 스마트 DPI 스케일링: GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb 자동 감지
- Qt6 시스템 트레이: 팟 제어, 앱 런처, 유휴 모니터
- 멀티 백엔드: Podman (기본), Docker, libvirt/KVM, 수동 RDP
- Windows 디블로트: 텔레메트리, 광고, Cortana, 검색 인덱싱 비활성화
- 시간 동기화: 호스트 sleep/wake 후 Windows 시계 강제 재동기화

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
                     │   │  (단일세션 RDP)         │   │
                     │   └──────────────────────┘   │
                     │   127.0.0.1:3390 (TLS)       │
                     └─────────────────────────────┘
```

## 기술 스택

| 레이어 | 기술 |
|--------|------|
| 언어 | Python 3.11+ (표준 라이브러리만, pip 없음) |
| CLI | argparse (표준 라이브러리) |
| GUI (선택) | PySide6 (Qt6) |
| 설정 | TOML (표준 라이브러리 tomllib + 내장 writer) |
| RDP | FreeRDP 3+ (xfreerdp, RemoteApp/RAIL) |
| 컨테이너 | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |
| CI | GitHub Actions (lint + test on 3.11-3.13 + pip-audit) |

## 빠른 시작

### 설치

GitHub Release 에 배포판별 패키지가 자동으로 첨부됩니다. RPM 은
[openSUSE Build Service (`home:Kernalix7/winpodx`)](https://build.opensuse.org/package/show/home:Kernalix7/winpodx)
에서, `.deb` 는 GitHub Actions 에서 빌드됩니다.

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
sudo apt install ./winpodx_0.1.2_all_debian13.deb   # 배포판에 맞게 선택
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
3. winpodx를 `~/.local/bin/winpodx/`에 복사
4. 설정 및 compose.yaml 생성
5. 14개 앱을 데스크톱 메뉴에 등록

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
| **USB 장치** | 네이티브 USB 리다이렉션 (`/usb:auto`); FreeRDP urbdrc 플러그인 필요 | 활성화 (드라이브 공유로 폴백) |
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
win_version = "11"           # 11 | 10 | ltsc10 | tiny11 | tiny10
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = true            # 앱 실행 시 자동 팟 시작
idle_timeout = 0             # 자동 일시정지 (초, 0 = 비활성화)
```

## 앱 프로필

앱 프로필은 **메타데이터 전용**입니다. Windows 앱의 위치를 정의할 뿐, 앱 자체가 아닙니다. 실제 Windows 앱은 Windows 컨테이너 안에 설치해야 합니다.

### 번들 프로필 (14개 앱)

| 프로필 | 설치 필요? |
|--------|-----------|
| Notepad, Explorer, CMD, PowerShell, Paint, Calculator | 아니오 (Windows 기본 내장) |
| Word, Excel, PowerPoint, Outlook, OneNote, Access | 예 (컨테이너에 Office 설치 필요) |
| VS Code | 예 (컨테이너에 VS Code 설치 필요) |
| Teams | 예 (컨테이너에 Teams 설치 필요) |

<details>
<summary><b>커스텀 앱 프로필 추가</b></summary>

```bash
mkdir -p data/apps/myapp
cat > data/apps/myapp/app.toml << 'EOF'
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

> **상태: 계획 중.** 멀티세션 지원은 별도 프로젝트로 개발 중입니다.

현재 Windows Desktop 에디션은 사용자당 RDP 세션을 1개로 제한합니다. 두 번째 앱을 열면 기존 세션이 재연결됩니다. 각 앱은 심리스 RemoteApp (RAIL) 창으로 열리지만, 동시에 하나만 활성화됩니다.

멀티세션 지원 (앱별 독립 RDP 세션)은 별도 프로젝트로 개발 중이며, 완료 시 winpodx에 통합될 예정입니다.

## 설치 / 삭제

```bash
./install.sh                # 설치 (배포판 감지, 의존성 설치, 앱 등록)
./uninstall.sh              # 삭제 (대화형, 단계별 확인)
./uninstall.sh --confirm    # 삭제 (자동, 설정 보존)
./uninstall.sh --purge      # 삭제 (설정 포함 전체 제거)
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
├── data/apps/             # 14개 번들 앱 정의 (TOML)
├── config/oem/            # Windows OEM 스크립트 (포스트인스톨)
├── scripts/windows/       # PowerShell 스크립트 (디블로트, 시간 동기화, USB 매핑)
├── .github/workflows/     # CI: lint + test on 3.11-3.13 + pip-audit
└── tests/                 # pytest 테스트 스위트 (225개 테스트)
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
python3 -m pytest tests/ -v    # 225개 테스트
ruff check src/ tests/         # 린트
```

## 기여

개발 설정 및 워크플로우는 [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md)를 참조하세요.

## 릴리즈 및 패키징

릴리즈 채널 (태그 `v*.*.*` 푸시 시 자동 배포):

| 채널 | 빌드 | 첨부 위치 |
|------|------|----------|
| RPM (openSUSE / Fedora / Slowroll) | [OBS `home:Kernalix7/winpodx`](https://build.opensuse.org/package/show/home:Kernalix7/winpodx) | GitHub Release |
| `.deb` (Debian / Ubuntu) | GitHub Actions `debs-publish.yml` | GitHub Release |
| `sdist` + `wheel` | GitHub Actions `release.yml` | GitHub Release |

OBS 토큰 및 GitHub Secret 설정은
[packaging/obs/README.md](../packaging/obs/README.md#2-github-actions-연동) 참조.
요약:

```bash
# 로컬에서 1회
osc token --create --operation runservice home:Kernalix7 winpodx
```

출력된 토큰 문자열을 복사한 뒤 GitHub 에서
`Settings → Secrets and variables → Actions → New repository secret`:

| 이름 | 값 |
|------|-----|
| `OBS_TOKEN` | `osc token` 으로 발급받은 `runservice` 토큰 |

비밀번호나 다른 비밀값은 필요 없음. `.deb` 워크플로우는 Actions 가
기본으로 제공하는 `GITHUB_TOKEN` 만 사용.

## 보안

보안 이슈는 [SECURITY.ko.md](SECURITY.ko.md)의 절차를 따라 주세요.

## 라이선스

[MIT](LICENSE) - Kim DaeHyun (kernalix7@kodenet.io)
