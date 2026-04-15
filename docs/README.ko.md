<div align="center">

<img src="../data/winpodx-icon.svg" alt="winpodx" width="128">

# winpodx

**Linux에서 Windows 앱을 심리스하게 실행**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](../LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://www.python.org/)
[![Backend: Podman](https://img.shields.io/badge/Backend-Podman-purple.svg)](https://podman.io/)
[![Tests: 92 passed](https://img.shields.io/badge/Tests-92%20passed-brightgreen.svg)](#테스트)

[English](../README.md) | **한국어**

*Linux 앱 메뉴에서 아이콘을 클릭합니다. Word가 열립니다. 끝.*

</div>

---

winpodx는 백그라운드에서 Podman을 통해 Windows 컨테이너를 실행하고, FreeRDP RemoteApp으로 Windows 앱을 네이티브 Linux 앱처럼 표시합니다. VM 수동 설정 불필요. **외부 Python 의존성 없음** — 표준 라이브러리만 사용 (Python 3.11+).

## 주요 기능

<table>
<tr><td width="50%">

**심리스 앱 창**
- RemoteApp (RAIL)으로 각 앱을 네이티브 Linux 창으로 렌더링
- 앱별 독립 작업 표시줄 아이콘 (WM_CLASS)
- 파일 연결 (`.docx` 더블클릭 → Word 실행)
- 멀티세션 지원 (앱별 독립 RDP 세션) 계획 중

</td><td width="50%">

**실행 및 자동화**
- 제로 설정 자동 프로비저닝
- 14개 번들 앱 프로필
- `.desktop` 엔트리, 아이콘, MIME 타입
- Qt6 시스템 트레이
- 자동 일시정지/재개 (CPU 절약)
- 비밀번호 자동 로테이션 (7일 주기)

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
| RDP | FreeRDP 3+ (xfreerdp) |
| 컨테이너 | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |

## 빠른 시작

### 설치

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

## 앱 프로필

앱 프로필은 **메타데이터 전용**입니다 — Windows 앱의 위치를 정의할 뿐, 앱 자체가 아닙니다. 실제 Windows 앱은 Windows 컨테이너 안에 설치해야 합니다.

### 번들 프로필 (14개 앱)

| 프로필 | 설치 필요? |
|--------|-----------|
| Notepad, Explorer, CMD, PowerShell, Paint, Calculator | 아니오 — Windows 기본 내장 |
| Word, Excel, PowerPoint, Outlook, OneNote, Access | 예 — 컨테이너에 Office 설치 필요 |
| VS Code | 예 — 컨테이너에 VS Code 설치 필요 |
| Teams | 예 — 컨테이너에 Teams 설치 필요 |

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

> **상태: 계획 중** — 멀티세션 지원은 별도 프로젝트로 개발 중입니다.

현재 Windows Desktop 에디션은 사용자당 RDP 세션을 1개로 제한합니다 — 두 번째 앱을 열면 기존 세션이 재연결됩니다. 각 앱은 심리스 RemoteApp (RAIL) 창으로 열리지만, 동시에 하나만 활성화됩니다.

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
├── scripts/windows/       # PowerShell 스크립트 (디블로트, 시간 동기화, RDP 설정)
├── .github/workflows/     # CI: 업스트림 업데이트 확인
└── tests/                 # pytest 테스트 스위트 (92개 테스트)
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
python3 -m pytest tests/ -v    # 92개 테스트
ruff check src/ tests/         # 린트
```

## 기여

개발 설정 및 워크플로우는 [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md)를 참조하세요.

## 보안

보안 이슈는 [SECURITY.ko.md](SECURITY.ko.md)의 절차를 따라 주세요.

## 라이선스

[MIT](LICENSE) - Kim DaeHyun (kernalix7@kodenet.io)
