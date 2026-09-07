<div align="center">

<img src="images/CI.svg" alt="WinPodX" width="320">

### 앱을 클릭하면 Word가 열립니다. 그게 전부입니다.

<p>실제 아이콘과 작업 표시줄 통합을 갖춘 Windows 앱을 Linux 창으로 실행합니다.<br>
상시 전체 화면 데스크톱 없이 FreeRDP RemoteApp과 dockur/windows를 사용합니다.</p>

[![Beta](https://img.shields.io/badge/status-beta-orange?style=for-the-badge)](#status-beta)
[![Latest](https://img.shields.io/github/v/release/kernalix7/winpodx?include_prereleases&style=for-the-badge&label=latest&color=2962FF)](https://github.com/kernalix7/winpodx/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/kernalix7/winpodx/ci.yml?branch=main&style=for-the-badge&label=CI)](https://github.com/kernalix7/winpodx/actions/workflows/ci.yml)
[![tests](https://img.shields.io/badge/tests-4000%2B-2EA44F?style=for-the-badge)](#testing)

[![license](https://img.shields.io/github/license/kernalix7/winpodx?style=flat-square&color=blue)](../LICENSE)
[![python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)

###### Works on

[![openSUSE](https://img.shields.io/badge/openSUSE-73BA25?style=flat-square&logo=opensuse&logoColor=white)](https://www.opensuse.org/)
[![Fedora](https://img.shields.io/badge/Fedora-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/)
[![Fedora Atomic Desktops](https://img.shields.io/badge/Fedora%20Atomic-294172?style=flat-square&logo=fedora&logoColor=white)](https://fedoraproject.org/atomic-desktops/)
[![Debian](https://img.shields.io/badge/Debian-A81D33?style=flat-square&logo=debian&logoColor=white)](https://www.debian.org/)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-E95420?style=flat-square&logo=ubuntu&logoColor=white)](https://ubuntu.com/)
[![RHEL family](https://img.shields.io/badge/RHEL%20%2F%20Alma%20%2F%20Rocky-EE0000?style=flat-square&logo=redhat&logoColor=white)](https://www.redhat.com/)
[![Arch](https://img.shields.io/badge/Arch-1793D1?style=flat-square&logo=archlinux&logoColor=white)](https://archlinux.org/)
[![NixOS](https://img.shields.io/badge/NixOS-5277C3?style=flat-square&logo=nixos&logoColor=white)](INSTALL.ko.md#nix)
[![AppImage](https://img.shields.io/badge/AppImage-any%20distro-6F42C1?style=flat-square&logo=appimage&logoColor=white)](INSTALL.ko.md)

<sub>[English](../README.md) · **한국어** · [설치](INSTALL.ko.md) · [사용법](USAGE.ko.md) · [기능](FEATURES.ko.md) · [구조](ARCHITECTURE.ko.md) · [비교](COMPARISON.ko.md)</sub>

</div>

---

> ### 상태: 베타
>
> WinPodX는 활발히 개발 중이며 현재 버전은 **v0.11.0**입니다.
>
> - 반응형 탐색과 사용자 지정 창 장식을 갖춘 Windows 11 설정 스타일 데스크톱 앱
> - pod 상태, 리소스 사용량, 실행 중인 앱, 고정 앱, reverse-open을 모은 Dashboard
> - 검색 가능한 Applications 목록, 그룹화된 Settings, 도구, 터미널 로그, 진단, 장치 관리
> - CLI와 GUI에서 dockur HTTP 프로비저닝 진행 상태를 우선 표시하고 로그로 대체
> - 간결한 `winpodx launch` 시작 메뉴 스타일 플라이아웃과 새로 고친 트레이 실행기
>
> 전체 변경 내역은 [CHANGELOG](CHANGELOG.ko.md)에서 확인할 수 있습니다.

## 세 명령으로 설치

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
winpodx setup
winpodx gui
```

일반적인 curl 설치에서는 설치 프로그램이 앞의 두 명령을 처리합니다. 패키지, AppImage, 소스 또는 wheel로 설치했다면 `winpodx setup`을 실행하세요. 설정을 만들고, 호스트를 확인하고, Windows를 프로비저닝하고, 앱을 찾고, 데스크톱 항목을 등록합니다.

개발 브랜치를 설치하려면 설치 명령에 `-s -- --main`을 추가하세요. Windows VM을 보존하고 WinPodX만 제거하려면 `curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm`을 실행합니다. VM과 설정까지 지울 때만 `--purge`를 추가하세요.

## 제공하는 기능

<a href="images/demo.png">
  <img src="images/demo.png" alt="KDE에서 Linux 창으로 실행 중인 Windows 앱" width="720">
</a>

Windows 앱은 각자의 Linux 창으로 열리며 아이콘, 작업 표시줄 항목, 파일 연결, 클립보드, 오디오, 프린터, 공유 파일을 사용합니다. 전체 Windows 데스크톱이 필요할 때만 `winpodx app run desktop`을 사용하세요.

### 데스크톱 앱

<a href="images/gui-dashboard.png">
  <img src="images/gui-dashboard.png" alt="새 데스크톱 앱의 WinPodX Dashboard" width="720">
</a>

데스크톱 앱은 Windows 11 설정 스타일 NavigationView를 사용합니다. Dashboard에는 pod 상태, 시작과 중지, 리소스 링, 빠른 작업, 실행 중인 앱, 고정 앱, reverse-open이 한 화면에 있습니다. Applications는 시작 메뉴 타일, 범주별 개수, 검색, 격자 또는 목록 표시, 컨텍스트 작업을 제공합니다. Settings는 연결, 하드웨어, Windows Update, 통합, 지역화, 위험 작업을 정리하며 저장 전까지 변경 표시를 냅니다.

Tools는 pod와 시스템 작업을 실행하고, Terminal에는 로그와 명령이 있으며, Info에는 상태 점검과 복사 가능한 진단 정보가 있습니다. Devices는 USB와 PCI 할당을 그룹화하고 License가 마지막 페이지를 구성합니다. 탐색 창은 기본적으로 320px이고 1100px 아래에서는 48px 아이콘 레일로 바뀌며, 햄버거 버튼으로 콘텐츠 위에 열 수 있습니다. 자세한 내용은 [GUI 둘러보기](USAGE.ko.md#qt6-gui-둘러보기)를 참고하세요.

## 요구 사항

| 요구 사항 | 확인 | 없을 때 할 일 |
|---|---|---|
| Intel VT-x 또는 AMD-V 활성화 | `lscpu \| grep -i virtualization` | 펌웨어에서 Intel Virtualization Technology, SVM Mode 또는 VT-x를 켭니다. |
| KVM 로드 | `lsmod \| grep kvm` | `kvm_intel` 또는 `kvm_amd`를 로드합니다. |
| 사용자의 KVM 접근 | `id -nG \| tr ' ' '\n' \| grep kvm` | `sudo usermod -aG kvm $USER`를 실행하고 로그아웃 후 다시 로그인합니다. |

가상화 기능이 있는 x86_64 또는 aarch64 CPU, 최소 8GB RAM, 권장 12GB RAM, 기본 Windows 디스크 64GB와 ISO를 둘 디스크 공간이 필요합니다. FreeRDP 3+와 compose provider가 있는 Podman 또는 Docker를 설치하세요. rootless Podman은 `/etc/subuid`, `/etc/subgid` 항목도 필요합니다. `winpodx setup-host`와 `winpodx doctor`가 일반적인 호스트 설정 문제를 알려 주고 고칠 수 있습니다.

## 주요 기능

| 영역 | 내용 |
|---|---|
| 매끄러운 앱 실행 | RemoteApp이 실제 아이콘, `WM_CLASS`, 작업 표시줄 통합, 파일 연결, 다중 모니터, 최대 50개의 독립 RDP 세션을 갖춘 Linux 창으로 Windows 앱을 엽니다. |
| 앱 찾기 | 시작 메뉴에 표시되는 Win32와 UWP 앱을 아이콘과 함께 가져옵니다. `winpodx app refresh` 또는 Applications에서 언제든 갱신할 수 있습니다. |
| 공유 | 양방향 클립보드, 오디오, 프린터, `\\tsclient\home`, 이동식 미디어, USB 패스스루, PCI 패스스루를 지원합니다. PCI 할당은 재시작이 필요하고 위험할 수 있으면 표시합니다. |
| Reverse-open | Linux 앱이 Windows의 **연결 프로그램** 메뉴에 나타나며, 제어된 리스너를 통해 호스트에서 파일을 받습니다. |
| Pod 운영 | Podman이 기본 백엔드이고 Docker와 수동 RDP도 지원합니다. pod는 유휴 시 자동 일시정지, 멈춘 게스트 복구, 비밀번호 교체, Windows 디스크 확장, 게스트 수정 동기화를 수행할 수 있습니다. |
| 개인정보 및 조정 | 선택적 Windows 디블로트, 호스트 적응형 KVM 조정, DPI 감지, 검증된 FreeRDP 추가 플래그, 시간 동기화, 선택적 베어메탈 위장을 제공합니다. |
| 언어 | CLI, 트레이, 데스크톱 앱은 영어, 한국어, 중국어, 일본어, 독일어, 프랑스어, 이탈리아어를 지원합니다. |

설정 스키마와 기술 세부 사항은 [FEATURES.ko.md](FEATURES.ko.md)를 참고하세요.

## 설치 방법

지원하는 모든 배포판에서는 위 curl 설치 프로그램을 사용하거나, 아래 네이티브 패키지로 설치할 수 있습니다. AppImage, 소스, 오프라인, Nix, 업데이트, 제거 방법은 [INSTALL.ko.md](INSTALL.ko.md)에 있습니다. 패키지 설치에는 바이너리만 들어가므로 직접 `winpodx setup`을 실행해야 합니다. curl 설치는 설치 프로그램을 다시 실행해 제자리에서 업데이트합니다. 패키지와 AppImage 설치는 처음 사용한 방식으로 업데이트하세요.

```bash
# openSUSE Tumbleweed / Leap / Slowroll
sudo zypper addrepo https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper install winpodx

# Fedora 42 / 43 / 44 (dnf5 — Fedora 41+)
sudo dnf config-manager addrepo --from-repofile=https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx

# Debian / Ubuntu — 최신 release 에서 맞는 .deb 다운로드 후
sudo apt install ./winpodx_<version>_all_debian13.deb

# AlmaLinux / Rocky / RHEL 9 / 10 — 최신 release 에서 맞는 .rpm
sudo dnf install ./winpodx-<version>-0.noarch.el10.rpm

# Arch
yay -S winpodx

# Nix
nix run github:kernalix7/winpodx

# AppImage (distro-agnostic, single file)
# 최신 GitHub release 에서 winpodx-<version>-x86_64.AppImage 다운로드
chmod +x winpodx-*-x86_64.AppImage
./winpodx-*-x86_64.AppImage setup
```

RHEL 9, AlmaLinux 9, Rocky Linux 9에서는 기본 `python3`가 지원 최소 버전보다 낮습니다. el9 패키지는 AppStream의 Python 3.11 스택을 함께 설치합니다.

## 문서

| 문서 | 내용 |
|---|---|
| [INSTALL.ko.md](INSTALL.ko.md) | 설치, 업데이트, 오프라인, 소스, Nix, 제거 방법 |
| [USAGE.ko.md](USAGE.ko.md) | CLI 참조, GUI 둘러보기, 상태 점검, 설정 |
| [FEATURES.ko.md](FEATURES.ko.md) | RemoteApp, reverse-open, 주변기기, 앱 찾기, 장치 패스스루 |
| [ARCHITECTURE.ko.md](ARCHITECTURE.ko.md) | 시스템 다이어그램, 소스 트리, 데이터 흐름 |
| [COMPARISON.ko.md](COMPARISON.ko.md) | winapps, LinOffice, winboat, Wine과의 비교 |
| [CHANGELOG.ko.md](CHANGELOG.ko.md) | 버전 변경 내역 |
| [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md) | 개발 설정과 기여 절차 |
| [SECURITY.ko.md](SECURITY.ko.md) | 보안 제보 절차 |

## 지원 배포판

openSUSE Tumbleweed, Leap, Slowroll. Fedora와 Fedora Atomic 데스크톱. Debian과 Ubuntu. AlmaLinux, Rocky Linux, RHEL. Arch와 Manjaro. NixOS와 다른 배포판의 Nix를 지원합니다. 패키지와 저장소 명령은 [INSTALL.ko.md](INSTALL.ko.md)를 참고하세요.

## 테스트

```bash
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/ -n auto
ruff check src/ tests/
ruff format --check src/ tests/
```

## 기여 및 라이선스

변경을 보내기 전 [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md)를 읽어 주세요. 보안 제보는 [SECURITY.ko.md](SECURITY.ko.md)의 절차를 따릅니다. WinPodX는 Kim DaeHyun의 [MIT 라이선스](../LICENSE) 소프트웨어입니다.

## Star History

<a href="https://github.com/kernalix7/winpodx/tree/star-history">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kernalix7/winpodx/star-history/chart-dark.svg" />
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/kernalix7/winpodx/star-history/chart.svg" />
    <img alt="WinPodX GitHub 별 기록" src="https://raw.githubusercontent.com/kernalix7/winpodx/star-history/chart.svg" width="900" />
  </picture>
</a>

## 후원 / Support

WinPodX 가 Linux 데스크톱을 조금이라도 더 좋게 만들었다면:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-EA4AAA?logo=githubsponsors&logoColor=white&style=for-the-badge)](https://github.com/sponsors/kernalix7)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white&style=for-the-badge)](https://ko-fi.com/kernalix7)
[![Fairy](https://img.shields.io/badge/🧚_Fairy-EE6E73?style=for-the-badge&logoColor=white)](https://fairy.hada.io/@kernalix7)

GitHub Sponsors 는 정기 / 일시 후원; Ko-fi 는 해외 카드 / PayPal 결제; fairy.hada.io 는 국내 결제용.
버그 리포트, PR, 별점도 환영합니다 —
Bug reports, PRs, and stars on the repo are equally appreciated and free.
