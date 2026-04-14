<div align="center">

<img src="../data/winpodx-icon.svg" alt="winpodx" width="128">

# winpodx

**Linux에서 Windows 앱을 심리스하게 실행**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![Backend: Podman](https://img.shields.io/badge/Backend-Podman-purple.svg)](https://podman.io/)

[English](../README.md) | **한국어**

</div>

---

winpodx는 백그라운드에서 Podman을 통해 Windows 컨테이너를 실행하고, FreeRDP를 통해 Windows 앱을 네이티브 Linux 앱처럼 표시합니다. 앱 메뉴에서 아이콘을 클릭하면 바로 동작합니다.

VM 수동 설정 불필요. **외부 Python 의존성 없음** — 표준 라이브러리만 사용 (Python 3.11+).

## 주요 기능

| 기능 | 설명 |
|------|------|
| **제로 설정 실행** | 앱 클릭 → 설정, 컨테이너, RDP 전부 자동 프로비저닝 |
| **원라인 설치** | `./install.sh` — 배포판 감지, 의존성 설치, 앱 등록 자동 |
| **14개 번들 앱** | Word, Excel, PowerPoint, Outlook, OneNote, Access, Notepad, Explorer, CMD, PowerShell, Paint, Calculator, VS Code, Teams |
| **DE 통합** | `.desktop` 엔트리, 아이콘, MIME 타입 앱 메뉴에 자동 등록 |
| **시스템 트레이** | PySide6 (Qt6) 트레이 아이콘으로 팟 제어, 앱 런처, 유지보수 |
| **자동 일시정지/재개** | 비활성 시 컨테이너 일시정지, 앱 실행 시 자동 재개 — CPU 절약 |
| **비밀번호 자동 로테이션** | 암호학적 랜덤 비밀번호, 7일마다 자동 변경 |
| **스마트 스케일링** | DE별 DPI 자동 감지 (GNOME, KDE, Sway, Hyprland, Cinnamon) |
| **잠금 파일 정리** | Office `~$*.docx` 잠금 파일 자동 제거 |
| **시간 동기화** | Linux sleep/wake 후 Windows 시계 강제 동기화 |
| **Windows 디블로트** | 텔레메트리, 광고, Cortana, 검색 인덱싱 한 번에 비활성화 |
| **다중 백엔드** | Podman (기본), Docker, libvirt/KVM, 수동 RDP |
| **깔끔한 삭제** | `./uninstall.sh` — winpodx 파일만 제거, 데이터 안 건드림 |
| **멀티세션 RDP** | RDPWrap + OffsetFinder — 여러 Windows 앱을 동시에 독립 창으로 실행 |
| **앱별 작업 표시줄** | WM_CLASS 매칭으로 앱마다 독립 작업 표시줄 아이콘 |
| **Windows 빌드 고정** | Feature update 차단, 보안 업데이트만 허용 — RDPWrap용 termsrv.dll 안정성 |
| **CI 의존성 추적** | RDPWrap, OffsetFinder, dockur/windows 업스트림 업데이트 시 자동 PR |
| **보안** | 설정 0600, 인증서 TOFU, TLS 전용 RDP, PID 잠금, 좀비 리퍼, 비밀번호 로그 필터링 |

## 빠른 시작

### 원라인 설치

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

설치 스크립트가 자동으로:
1. 배포판 감지 (openSUSE, Fedora, Ubuntu, Arch, ...)
2. 없는 의존성 설치 (Podman, FreeRDP, KVM) — 설치 전 확인
3. winpodx를 `~/.local/bin/winpodx/`에 복사
4. 설정 및 compose.yaml 생성
5. 14개 앱을 데스크톱 메뉴에 등록

이후에는 앱 메뉴에서 클릭만 하면 됩니다. 터미널에서는:

```bash
winpodx app run word              # Word 실행
winpodx app run word ~/문서.docx   # 파일과 함께 실행
winpodx app run desktop           # 전체 Windows 데스크톱
```

### 직접 실행 (설치 없이)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

## CLI 참조

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
winpodx pod start --wait          # 시작 + RDP 대기
winpodx pod stop                  # 정지 (활성 세션 경고)
winpodx pod status                # 상태 확인
winpodx pod restart

# 전원 관리
winpodx power --suspend           # 일시정지 (CPU 해제, 메모리 유지)
winpodx power --resume            # 재개

# 보안
winpodx rotate-password           # Windows RDP 비밀번호 수동 변경

# 유지보수
winpodx cleanup                   # Office 잠금 파일 제거
winpodx timesync                  # Windows 시간 동기화
winpodx debloat                   # 텔레메트리/광고/블로트 비활성화
winpodx uninstall                 # winpodx 파일 제거 (컨테이너 유지)
winpodx uninstall --purge         # 설정 포함 전체 제거

# 시스템
winpodx setup                     # 대화형 설정 위자드
winpodx info                      # 시스템 진단
winpodx tray                      # Qt 시스템 트레이
winpodx config show               # 현재 설정 확인
winpodx config set rdp.scale 140  # 설정 변경
winpodx config import             # winapps.conf 가져오기
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

[pod]
backend = "podman"
win_version = "11"           # 11 | 10 | ltsc10 | tiny11 | tiny10
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = true            # 앱 실행 시 자동 팟 시작
idle_timeout = 0             # 자동 일시정지 (초, 0 = 비활성화)
```

## 설치 / 삭제

```bash
./install.sh                # 설치 (배포판 감지, 의존성 설치, 앱 등록)
./uninstall.sh              # 삭제 (대화형, 단계별 확인)
./uninstall.sh --confirm    # 삭제 (자동, 설정 보존)
./uninstall.sh --purge      # 삭제 (설정 포함 전체)
```

**삭제 시 winpodx 파일만 제거합니다.** 절대 건드리지 않는 것:
- Podman 컨테이너/볼륨 (Windows VM 데이터)
- 시스템 패키지 (podman, freerdp, python3)
- 홈 디렉토리 파일

## 멀티세션 RDP (RDPWrap)

Windows Desktop 에디션은 사용자당 RDP 세션을 1개로 제한합니다 — 두 번째 앱을 열면 첫 번째가 끊깁니다. winpodx는 [RDPWrap](https://github.com/stascorp/rdpwrap)으로 이 제한을 해제하여 여러 앱을 독립 창에서 동시 실행할 수 있습니다.

### 동작 방식

1. **RDPWrap + OffsetFinder를 소스에서 빌드** — GitHub Actions CI (`Build RDPWrap` 워크플로우, 수동 트리거)
2. 빌드된 바이너리를 `config/oem/rdpwrap/`에 커밋 — 사전 빌드 바이너리 없음, 신뢰 문제 없음
3. 컨테이너 첫 부팅 시 `setup_rdpwrap.ps1`이 RDPWrap 설치 + **RDPWrapOffsetFinder** 실행으로 실제 `termsrv.dll`에서 `rdpwrap.ini` 생성 (심볼 기반, Microsoft 공식 PDB 서버 사용)
4. 앱별로 `/wm-class`와 `StartupWMClass` 설정으로 작업 표시줄 분리

### Windows 빌드 고정

RDPWrap용 `termsrv.dll` 안정성을 위해:
- Feature/빌드 업그레이드는 `TargetReleaseVersion` 레지스트리 정책으로 차단
- 보안 업데이트는 정상 설치
- 빌드 업그레이드는 winpodx 업데이트 시에만 진행

### 업스트림 모니터링

CI 워크플로우가 매주 새 릴리스를 확인하고 PR을 생성합니다:
- `stascorp/rdpwrap` — RDPWrap 패처
- `llccd/RDPWrapOffsetFinder` — 오프셋 추출 도구
- `dockur/windows` — Windows 컨테이너 이미지

코드 변경은 자동으로 적용되지 않습니다 — PR은 수동 리뷰 후 머지 필요.

### 수동 INI 재생성

Windows 보안 업데이트가 `termsrv.dll`을 변경하면, GUI의 **Update RDPWrap** 버튼으로 현재 DLL에서 오프셋을 재생성하세요.

## 지원 배포판

| 배포판 | 패키지 매니저 | 상태 |
|--------|-------------|------|
| openSUSE Tumbleweed/Leap | zypper | 테스트됨 |
| Fedora / RHEL / CentOS | dnf | 지원 |
| Ubuntu / Debian / Mint | apt | 지원 |
| Arch / Manjaro | pacman | 지원 |

## 테스트

```bash
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/ -v
ruff check src/ tests/         # 린트
```

## 기여

[CONTRIBUTING.ko.md](CONTRIBUTING.ko.md)를 참조하세요.

## 보안

[SECURITY.ko.md](SECURITY.ko.md)의 절차를 따라 주세요.

## 라이선스

MIT
