<div align="center">

<img src="../CI.svg" alt="winpodx" width="320">

### 앱 클릭하면 Word 가 뜬다. 끝.

<p>Windows 앱마다 네이티브 Linux 윈도 — 진짜 아이콘, 진짜 <code>WM_CLASS</code>,<br>
태스크바 핀 가능. FreeRDP RemoteApp + dockur/windows. Zero config.</p>

<pre><code># 최신 안정 release (기본)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# 최신 main HEAD (개발용, 불안정할 수 있음)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# 언인스톨 (Windows VM 데이터 유지; 전부 삭제는 --purge)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm</code></pre>

<a href="images/demo.png">
  <img src="images/demo.png" alt="winpodx 실행 모습 — KDE 데스크톱 위에서 Windows 앱이 각각 네이티브 Linux 창으로" width="720">
</a>

<sub>Windows 정보 / 작업 관리자 / PowerShell 이 각각 Linux 창으로, winpodx Apps 그리드와 나란히.</sub>

[![Beta](https://img.shields.io/badge/status-beta-orange?style=for-the-badge)](#상태-베타)
[![Latest](https://img.shields.io/github/v/release/kernalix7/winpodx?include_prereleases&style=for-the-badge&label=latest&color=2962FF)](https://github.com/kernalix7/winpodx/releases)

[![license](https://img.shields.io/github/license/kernalix7/winpodx?style=flat-square&color=blue)](../LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![tests](https://img.shields.io/badge/tests-1090%2B-2EA44F?style=flat-square)](#테스트)
[![CI](https://img.shields.io/github/actions/workflow/status/kernalix7/winpodx/ci.yml?branch=main&style=flat-square&label=CI)](https://github.com/kernalix7/winpodx/actions/workflows/ci.yml)
[![stars](https://img.shields.io/github/stars/kernalix7/winpodx?style=flat-square&color=FFD93D&logo=github&logoColor=white)](https://github.com/kernalix7/winpodx/stargazers)
[![downloads](https://img.shields.io/github/downloads/kernalix7/winpodx/total?style=flat-square&color=2EA44F)](https://github.com/kernalix7/winpodx/releases)

###### Works on

[![openSUSE](https://img.shields.io/badge/openSUSE-73BA25?style=flat-square&logo=opensuse&logoColor=white)](https://www.opensuse.org/)
[![Fedora](https://img.shields.io/badge/Fedora-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/)
[![Fedora Atomic Desktops](https://img.shields.io/badge/Fedora%20Atomic-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/atomic-desktops/)
[![Debian](https://img.shields.io/badge/Debian-A81D33?style=flat-square&logo=debian&logoColor=white)](https://www.debian.org/)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=flat-square&logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![RHEL family](https://img.shields.io/badge/RHEL%20%2F%20Alma%20%2F%20Rocky-EE0000?style=flat-square&logo=redhat&logoColor=white)](https://www.redhat.com/)
[![Arch](https://img.shields.io/badge/Arch-1793D1?style=flat-square&logo=archlinux&logoColor=white)](https://archlinux.org/)
[![NixOS](https://img.shields.io/badge/NixOS-5277C3?style=flat-square&logo=nixos&logoColor=white)](INSTALL.ko.md#nix)

<sub>[English](../README.md) &nbsp;·&nbsp; **한국어** &nbsp;·&nbsp; [설치](INSTALL.ko.md) &nbsp;·&nbsp; [사용법](USAGE.ko.md) &nbsp;·&nbsp; [기능](FEATURES.ko.md) &nbsp;·&nbsp; [아키텍처](ARCHITECTURE.ko.md) &nbsp;·&nbsp; [비교](COMPARISON.ko.md)</sub>

</div>

---

> ### 상태: 베타
> winpodx 는 활발히 개발 중입니다 (**v0.5.0**). v0.5.0 의 헤드라인은 **reverse-open**: Linux 앱이 Windows 게스트 우클릭 "Open with…" 메뉴에 기본으로 노출, 앱별 정확한 아이콘과 함께. 게스트 안에서 `.txt` 더블클릭, 메뉴에서 Kate 선택 → 실제 호스트 파일 경로로 Linux Kate 에서 열림. host→guest 파이프라인 (`127.0.0.1:8765` bearer-auth HTTP agent, FreeRDP RemoteApp 폴백) 은 v0.3.x 부터 그대로. 첫 설치는 여전히 ~5–10분 소요 (Windows VM ISO 다운로드 + Sysprep + OEM apply); 진행 상황은 `winpodx pod wait-ready --logs` 로 확인. 문제 발생 시 <https://github.com/kernalix7/winpodx/issues> 에 이슈 등록해주세요.

**Full-screen RDP 아님.** Windows 앱이 각각 네이티브 Linux 윈도 — 진짜 아이콘, pin 가능, alt-tab, 파일 연결 양방향. 진짜 Windows 데스크톱 필요할 때만 `winpodx app run desktop`.

winpodx 는 백그라운드에서 Windows 컨테이너 ([dockur/windows](https://github.com/dockur/windows)) 를 실행하고, FreeRDP RemoteApp 으로 Windows 앱을 네이티브 Linux 앱처럼 표시합니다. 게스트 안의 bearer-auth HTTP agent 가 host→guest 명령 채널을 처리해서 PowerShell 창이 깜빡이지 않음. 반대 방향 — Linux 앱이 Windows "Open with…" 메뉴에 노출 — 은 호스트 측 listener 가 게스트 내 슬러그별 Rust shim 이 작성한 JSON 요청을 소비하는 식으로 처리. **외부 Python 의존성 거의 없음** (Python 3.11+ 는 표준 라이브러리만; 3.9/3.10 은 순수 파이썬 `tomli` 폴백 1개).

## 빠른 설치

원라인 (지원되는 모든 Linux distro):

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

또는 네이티브 패키지 매니저:

```bash
# openSUSE Tumbleweed / Leap / Slowroll
sudo zypper addrepo https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper install winpodx

# Fedora 42 / 43
sudo dnf config-manager --add-repo https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx

# Debian / Ubuntu — 최신 release 에서 맞는 .deb 다운로드 후
sudo apt install ./winpodx_0.5.0_all_debian13.deb

# AlmaLinux / Rocky / RHEL 9 / 10 — 최신 release 에서 맞는 .rpm
sudo dnf install ./winpodx-0.5.0-0.noarch.el10.rpm

# Arch
yay -S winpodx

# Nix
nix run github:kernalix7/winpodx
```

오프라인 / 에어갭 빌드, 소스 설치, 버전 pin, 언인스톨은 [docs/INSTALL.ko.md](INSTALL.ko.md) 참조.

## 실행

```bash
winpodx app run word              # Word 실행
winpodx app run word ~/doc.docx   # 파일 열기
winpodx app run desktop           # 전체 Windows 데스크톱
```

또는 그냥 애플리케이션 메뉴에서 앱 아이콘 클릭. 전체 CLI, Qt6 GUI, 헬스 체크, 설정은 [docs/USAGE.ko.md](USAGE.ko.md) 참조.

## 주요 기능

<table>
<tr><td width="50%">

**Reverse-open (v0.5.0 신규)**
- Linux 앱이 Windows 게스트 우클릭 "Open with…" 메뉴에 기본 노출
- 짧은 메뉴 + 긴 "다른 앱 선택" 다이얼로그 양쪽에 앱별 정확한 아이콘
- 선택 시 호스트 `xdg-open` 으로 파일 열기 round-trip
- 호스트 측 Linux 앱 + MIME 연결을 freedesktop 표준에서 자동 발견
- `winpodx host-open` CLI 또는 GUI Settings 패널로 관리
- [상세 →](FEATURES.ko.md#reverse-open-linux-앱이-windows-open-with-에)

</td><td width="50%">

**매끄러운 앱 윈도**
- RemoteApp (RAIL) 이 각 Windows 앱을 네이티브 Linux 윈도로 렌더링 — 전체 데스크톱 아님
- `WM_CLASS` 매칭 통한 앱별 taskbar 아이콘 (`/wm-class:<stem>` + `StartupWMClass`)
- 양방향 파일 연결: Linux 파일 관리자에서 `.docx` 더블클릭 → Word 가 열림
- 멀티세션 RDP: bundled [rdprrap](https://github.com/kernalix7/rdprrap) 이 최대 10개 독립 세션 자동 활성화
- RAIL 전제조건이 unattended 설치 중 자동 설정

</td></tr>
<tr><td width="50%">

**제로 설정 실행**
- 첫 앱 클릭이 모든 것 자동 프로비저닝: config, 컨테이너, desktop 엔트리
- 첫 부팅 시 자동 discovery 가 실행 중인 Windows 게스트 스캔, 설치된 모든 앱 (Registry App Paths, Start Menu, UWP/MSIX, Chocolatey, Scoop) 을 실제 아이콘과 함께 등록
- `winpodx app refresh` 또는 GUI Refresh 버튼으로 언제든 수동 재스캔
- 멀티 백엔드: Podman (기본), Docker, libvirt/KVM, manual RDP

</td><td width="50%">

**주변기기 & 공유**
- **클립보드**: 양방향 복사-붙여넣기 (텍스트 + 이미지) — 기본 활성
- **사운드**: RDP 오디오 스트리밍 (`/sound:sys:alsa`) — 기본 활성
- **프린터**: Linux 프린터가 Windows 에 공유 — 기본 활성
- **홈 디렉토리**: `\\tsclient\home` 으로 공유
- **USB 드라이브**: FileSystemWatcher 통한 드라이브 레터 (E:, F:, …) 자동 매핑; 세션 시작 후 꽂은 USB 도 서브폴더로 접근 가능
- **USB 디바이스 패스스루**: `extra_flags` 에 `/usb:auto` 추가하면 opt-in

</td></tr>
<tr><td width="50%">

**자동화 & 보안**
- 자동 suspend / resume: idle 시 컨테이너 pause, 다음 실행 시 resume
- 비밀번호 자동 회전: 20자 암호학적 비밀번호, 7일 주기, atomic rollback
- 스마트 DPI 스케일링: GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb 자동 감지
- Windows debloat: 텔레메트리, 광고, Cortana, 검색 인덱싱 기본 비활성
- FreeRDP `extra_flags` allowlist (regex 검증) 가 사용자 input 안전 경계
- 시간 동기화: 호스트 sleep/wake 후 Windows 시계 강제 resync

</td><td width="50%">

**운영 & 회복력**
- 오프라인 / 에어갭 설치 (`--source` + `--image-tar`)
- 원라인 언인스톨 (Windows VM 데이터 유지; `--purge` 로 전부 삭제)
- `winpodx check` 통한 헬스 체크 (pod / RDP / agent / disk / round-trip / 비밀번호 age)
- Qt6 GUI: Apps / Settings / Tools / Terminal / Info 페이지 — 가벼운 시스템 트레이도 별도
- stdlib 지향 Python (3.11+ 는 pip-deps 없음; 3.9 / 3.10 은 `tomli` 폴백 1개)

</td></tr>
</table>

[docs/FEATURES.ko.md](FEATURES.ko.md) 에서 멀티세션 RDP 내부, 앱 프로필 스키마, reverse-open 아키텍처 등 자세한 deep dive 확인.

## 문서

| 문서 | 내용 |
|----------|---------------|
| [INSTALL.ko.md](INSTALL.ko.md) | 모든 설치 경로 — 원라인, 패키지 매니저, 오프라인, Nix, 소스 |
| [USAGE.ko.md](USAGE.ko.md) | CLI 레퍼런스, Qt6 GUI 투어, 헬스 체크, 설정 파일 |
| [FEATURES.ko.md](FEATURES.ko.md) | Reverse-open, 멀티세션 RDP, 주변기기, 앱 프로필, 자동 discovery |
| [ARCHITECTURE.ko.md](ARCHITECTURE.ko.md) | 동작 방식 (다이어그램), 기술 스택, 소스 트리, 데이터 흐름 |
| [COMPARISON.ko.md](COMPARISON.ko.md) | winpodx vs winapps / LinOffice / winboat, 그리고 winpodx vs Wine |
| [CHANGELOG.ko.md](CHANGELOG.ko.md) | 전체 버전 이력 |
| [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md) | 개발 셋업 + 워크플로우 |
| [SECURITY.ko.md](SECURITY.ko.md) | 보안 공개 프로세스 |

## 지원 distro

| Distro | 패키지 매니저 | 상태 |
|--------|-----------------|--------|
| openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll | zypper | Tested |
| Fedora 42 / 43 / 44 | dnf | Supported |
| Fedora Silverblue / Kinoite / Sericea / Bluefin / Bazzite (42 / 43 / 44) | rpm-ostree (OBS, `--apply-live`) | Supported |
| Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10 | apt | Supported |
| AlmaLinux / Rocky / RHEL 9 / 10 | dnf | Supported |
| Arch / Manjaro | pacman + `yay -S winpodx` | Supported |
| NixOS (와 모든 distro 위의 Nix) | nix flake | Supported |

각 태그 푸시 (`v*.*.*`) 마다 모든 채널에 자동 publish — 메인테이너 상세는 [packaging/](../packaging/) 참조.

## 테스트

```bash
# 리포 루트에서 (설치 불필요)
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/    # 1090+ 테스트
ruff check src/ tests/      # 린트
ruff format --check src/ tests/
```

## 기여

개발 셋업, 브랜치 명명, 커밋 컨벤션, CI 기대치는 [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md) 참조.

## 보안

보안 이슈는 [SECURITY.ko.md](SECURITY.ko.md) 의 프로세스 따르기.

## Star History

<a href="https://star-history.com/#kernalix7/winpodx&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=kernalix7/winpodx&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=kernalix7/winpodx&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=kernalix7/winpodx&type=Date" />
  </picture>
</a>

## 후원 / Support

winpodx 가 Linux 데스크톱을 조금이라도 더 좋게 만들었다면:

[![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white&style=for-the-badge)](https://ko-fi.com/kernalix7)
[![Fairy](https://img.shields.io/badge/🧚_Fairy-EE6E73?style=for-the-badge&logoColor=white)](https://fairy.hada.io/@kernalix7)

Ko-fi 는 해외 카드 / PayPal 결제; fairy.hada.io 는 국내 결제용.
버그 리포트, PR, 별점도 환영합니다 —
Bug reports, PRs, and stars on the repo are equally appreciated and free.

## 라이선스

[MIT](../LICENSE) — Kim DaeHyun (kernalix7@kodenet.io)
