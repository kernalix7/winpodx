<div align="center">

<img src="images/CI.svg" alt="WinPodX" width="320">

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
  <img src="images/demo.png" alt="WinPodX 실행 모습 — KDE 데스크톱 위에서 Windows 앱이 각각 네이티브 Linux 창으로" width="720">
</a>

<sub>Windows 정보 / 작업 관리자 / PowerShell 이 각각 Linux 창으로, WinPodX Dashboard (라이브 Pod / RAM / CPU 게이지, 워크스페이스 타일) 와 나란히.</sub>

[![Beta](https://img.shields.io/badge/status-beta-orange?style=for-the-badge)](#상태-베타)
[![Latest](https://img.shields.io/github/v/release/kernalix7/winpodx?include_prereleases&style=for-the-badge&label=latest&color=2962FF)](https://github.com/kernalix7/winpodx/releases)

[![license](https://img.shields.io/github/license/kernalix7/winpodx?style=flat-square&color=blue)](../LICENSE)
[![python](https://img.shields.io/badge/python-3.9%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![tests](https://img.shields.io/badge/tests-1800%2B-2EA44F?style=flat-square)](#테스트)
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
[![AppImage](https://img.shields.io/badge/AppImage-any%20distro-6F42C1?style=flat-square&logo=appimage&logoColor=white)](INSTALL.ko.md)

<sub>[English](../README.md) &nbsp;·&nbsp; **한국어** &nbsp;·&nbsp; [설치](INSTALL.ko.md) &nbsp;·&nbsp; [사용법](USAGE.ko.md) &nbsp;·&nbsp; [기능](FEATURES.ko.md) &nbsp;·&nbsp; [아키텍처](ARCHITECTURE.ko.md) &nbsp;·&nbsp; [비교](COMPARISON.ko.md)</sub>

</div>

---

> ### 상태: 베타
> WinPodX 는 활발히 개발 중입니다 (**v0.10.2**). **v0.7.0** 은 **베어메탈 위장** (#246, opt-in / 기본 꺼짐) 을 도입 — `pod.disguise_level balanced | max` 로 Windows 게스트가 VM 탐지 소프트웨어 (Nvidia GPU 패스스루 "code 43", launch-gate VM 체크, VM 비적합 설치 프로그램) 에게 물리 머신처럼 보임 (al-khaser 0.82 검증 완료), 기본 게스트 사용자명은 `WPX-User` 로 변경. **v0.7.1** 은 UX + 통합 릴리즈: 탐지된 Windows 앱이 **자동 파일 연결** 로 파일 관리자의 "다른 프로그램으로 열기" 메뉴에 표시 (#545, 기본 활성화, *추가* 만 — 기본 핸들러로 설정 안 함); GUI 에 **앱 관리** 추가 — 탐지값 재설정, 커스텀 아이콘 선택기, 다중 선택 일괄 숨기기/삭제, 삭제 앱 복원 목록 (#530); **빠른 앱 런처** (`winpodx launch`, #561) 가 DE 핫키에 바인딩 가능한 시작 메뉴 스타일 선택기 제공; **`winpodx gui` 가 더 이상 터미널을 막지 않음** (#549); `winpodx doctor` 가 RemoteApp 창이 깨지는 **구버전 FreeRDP 를 경고** (#546); `install.sh --main` 이 **Atomic Fedora** 에서 존중됨 (#548). **v0.7.2** 는 버그 수정 릴리즈: KDE/Plasma 에서 *Refresh Apps* GUI 크래시(#567) 와 트레이 *Terminate Session* / *USB Devices* 서브메뉴(#573) 수정, 중국어/일본어/한국어 이름 앱 디스커버리(#553), `pod stop` 시 컨테이너 보존(업데이트 시 재생성 없음), 자격증명 누락 시 명확한 에러(#569). **v0.7.3** 은 **Windows VM 자체의 파일을 reverse-open** — 공유 Home뿐 아니라 게스트 `C:` 를 SMB 공유 + kio-fuse 마운트해 호스트 앱이 실제 게스트 파일을 열고 편집이 원본에 저장됨 (#616, KDE) —, VM RAM을 해제하는 opt-in **idle 자동 정지** (#622), 터치스크린 / 스타일러스 / 펜 패스스루용 **`+multitouch`** 플래그 (#623) 추가; 또한 느린 게스트에서 `winpodx app refresh` 타임아웃(#619), 대시보드 RAM / Disk 게이지가 "n/a"에 멈추는 문제(#634) 수정, 설치를 불안정하게 만들던 USB 드라이브 문자 자동매핑 제거(#613, #638). **v0.7.4** 는 **Windows VM 저장 위치 선택**(`install.sh --storage-dir`, #646)과 **로컬 Windows ISO 설치**로 다운로드 생략(`--win-iso`, #647), 24GB+ 호스트의 **VM RAM 기본 8GB**(#630)를 추가; 그리고 수정 — 실행 중 앱에서 두 번째 문서 열기, Deepin 등 PATH가 잘리는 데스크탑의 *연결 프로그램*, RDP 비번 누락 시 명확한 에러(#569), GUI Logs 버튼의 Docker 백엔드 준수, `.deb`의 `podman-compose` 포함(#644), 유지보수 다이얼로그 크기(#550). **v0.8.0** 는 앱 검출을 **기본 시작메뉴 전용**으로 — Windows 시작메뉴에 실제로 뜨는 앱만, 중첩 폴더 하위그룹으로 그룹화, 옛 전체스캔은 `desktop.full_app_scan` opt-in (#581); `[pod] keyboard` 를 FreeRDP 세션 레이아웃으로 전달(#660); `--win-iso` 를 compose-up *전*에 스테이징해 다운로드 대신 그 ISO로 설치(#647); reverse-open 리스너 자가복구; 번들 rdprrap **0.3.0** 로 bump; GUI 크래시 다수 수정 — *Refresh Apps* SIGSEGV, off-thread 배율 SIGABRT, Debloat 작업창 자동닫힘 안 됨(#550), 길거나 CJK 이름서 레이아웃 재귀 SIGSEGV(#553). **v0.9.0** 는 Windows 앱이 Linux의 **URL-scheme 링크**를 처리하게 합니다 — `mailto:` 링크를 클릭하면 Outlook이 열리고, `slack:` / `vnc:` 같은 앱 스킴이 알맞은 Windows 앱으로 라우팅됩니다(디스커버리 중 자동 수확 → `x-scheme-handler` 등록, #421, #694). 또한 **신뢰 경계를 강화** — 발견된 게스트 앱이 호스트의 `http`/`https` 기본 핸들러를 조용히 탈취할 수 없고(명시적 opt-in만), 세션 창-reaper가 방금 재실행된 앱을 오살하거나 일시적 스캔 실패에 세션을 몰살하지 않습니다(#680 후속); 그리고 multiarch/KF6 레이아웃의 kio-fuse 검출(#697), 트레이와 함께 자동 시작되는 reverse-open 리스너(#691), 절전/복구 D-Bus 구독(#690), `git` 없을 때 자동 설치하는 설치 원라이너(#705) 수정. **v0.9.1** 은 버그 수정 + 강화 릴리스: non-purge `uninstall.sh`가 Windows VM 디스크를 지울 수 있던 **데이터 손실 버그**(#716)를 고치고, 패키지 설치본에서 `uninstall --purge`가 실제로 purge하며, 패키지 정리를 pre-remove hook으로 옮겨 `apt`/`dnf`/`pacman` 제거가 동작하고, `install.sh` 업그레이드를 원자적으로 만듭니다(#720, #722); reverse-open "Linux Apps" 바로가기가 이제 파일 열기뿐 아니라 **앱을 직접 실행**하고(#616), `install.sh`가 Qt GUI용 `libxcb-cursor`를 자동 설치하며(#712), non-purge 언인스톨 후 재설치 시 앱 메뉴를 다시 채웁니다(#723). **v0.10.0** 은 고정된 `dockur/windows` 이미지를 v6.00 을 거쳐 v6.02 로 올립니다(#721, #735, dockur 메인테이너 @kroese 의 요청) — Podman 브리지 NAT 범위 확장과 업스트림 QEMU 업데이트. **v0.10.1** 은 버그 수정 릴리스: X11 에서 RAIL 앱 창이 제 아이콘을 표시하고(#702), KDE Plasma 트레이의 시작/정지/재시작이 다시 동작하며(#725), XWayland 에서 디스커버리가 FreeRDP `rc=12` 로 중단되지 않고(#694), dockur 핀이 v6.01 로 이동해 메인테이너의 rootless NAT 수정에 따라 강제 `NETWORK=user` 를 제거합니다(#735). **v0.10.2** 는 버그 수정 릴리스: 게스트 에이전트가 긴 실행 중에도 반응성을 유지해 반복적으로 죽는 것처럼 보이지 않고(#751), Bazzite 같은 rpm-ostree 호스트에서 설치 프로그램이 기존 venv 설치를 가리지 않으며(#752), `podman-compose` 가 없을 때 알기 힘든 "no such container" 대신 명확하게 실패하고(#753), `pod start` 가 호스트 포트 충돌(예: 3390 포트를 쓰는 Ubuntu GNOME 원격 데스크톱)을 미리 점검해 한 시간 동안 멈추는 대신 바로 알려주며(#754), dockur 핀이 `wait-ready` 의 실시간 다운로드 진행률과 함께 v6.02 로 이동합니다(#735). AppImage 는 **Thin** (~110 MB) — FreeRDP + Python + Qt + WinPodX 만 — 컨테이너 런타임은 호스트의 `podman` / `docker` 사용. 0.6.0 에서 정착된 CLI 표면 유지 — **`winpodx guest`** (게스트 작업), **`winpodx install`** (설치/디스크 작업), **`winpodx doctor`** (`--json` / `--quick` / `--fix` 진단); post-create 체인은 단일 **`winpodx provision`**. 첫 설치는 여전히 ~5–10분 소요 (Windows VM ISO 다운로드 + Sysprep + OEM apply); 진행 상황은 `winpodx pod wait-ready --logs` 로 확인. 문제 발생 시 <https://github.com/kernalix7/winpodx/issues> 에 이슈 등록해주세요.

**Full-screen RDP 아님.** Windows 앱이 각각 네이티브 Linux 윈도 — 진짜 아이콘, pin 가능, alt-tab, 파일 연결 양방향. 진짜 Windows 데스크톱 필요할 때만 `winpodx app run desktop`.

WinPodX 는 백그라운드에서 Windows 컨테이너 ([dockur/windows](https://github.com/dockur/windows)) 를 실행하고, FreeRDP RemoteApp 으로 Windows 앱을 네이티브 Linux 앱처럼 표시합니다. 게스트 안의 bearer-auth HTTP agent 가 host→guest 명령 채널을 처리해서 PowerShell 창이 깜빡이지 않음. 반대 방향 — Linux 앱이 Windows "Open with…" 메뉴에 노출 — 은 호스트 측 listener 가 게스트 내 슬러그별 Rust shim 이 작성한 JSON 요청을 소비하는 식으로 처리. **외부 Python 의존성 거의 없음** (Python 3.11+ 는 표준 라이브러리만; 3.9/3.10 은 순수 파이썬 `tomli` 폴백 1개).

## 최소 요구사항

**설치 전에** 머신이 가상화를 실제로 지원하는지 확인. WinPodX 는 KVM 기반 컨테이너에서 Windows 실행 — 아래 셋 없으면 설치는 끝까지 진행되지만 Windows 가 절대 부팅 안 됨.

| 요구사항 | 확인 명령 | 해결 |
|---|---|---|
| **BIOS / UEFI 에 Intel VT-x 또는 AMD-V 활성** | `lscpu \| grep -i virtualization` 에 `VT-x` 또는 `AMD-V` 표시 | 재부팅 → 펌웨어 설정 → "Intel Virtualization Technology" / "SVM Mode" / "VT-x" 활성. 노트북에서 기본 OFF 인 경우 많음. |
| **kvm 커널 모듈 로드** | `lsmod \| grep kvm` 에 `kvm_intel` 또는 `kvm_amd` | `sudo modprobe kvm_intel` (Intel) 또는 `sudo modprobe kvm_amd` (AMD). BIOS 활성 후 자동 로드. |
| **사용자가 `kvm` 그룹에 속함** | `id -nG \| tr ' ' '\n' \| grep kvm` 가 `kvm` 반환 | `sudo usermod -aG kvm $USER` 후 로그아웃 → 재로그인. |

하드웨어: 가상화 확장 지원 x86_64 또는 aarch64 CPU, 8GB+ RAM (12GB+ 권장), Windows 이미지용 디스크 여유 ~30GB. `install.sh` 가 패키지 설치 단계 후 `/dev/kvm` 없으면 같은 진단으로 중단. "설치는 잘 됐는데 Windows 가 안 떠요" 류 버그 리포트 대부분이 위 세 줄 중 하나가 원인.

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

> **패키지 매니저 / AppImage 설치 후:** `winpodx setup` 한번 실행 → `~/.config/winpodx/winpodx.toml` + compose.yaml 생성. curl 원라이너는 이 단계를 자동으로 해주고 Windows 첫 부팅까지 ~5–10분 대기; 패키지 설치는 바이너리만 ship — `apt install` / `dnf install` / `yay -S` / 첫 AppImage 실행이 갑자기 10분짜리 Windows ISO 다운로드 트리거하지 않게. setup 후엔 그냥 앱 실행 (`winpodx app run desktop`) 만 해도 첫 실행시 pod 자동 provision.
>
> Thin AppImage (0.6.0) 는 Python + Qt + WinPodX + FreeRDP 만 번들 — 컨테이너 런타임은 호스트 (`podman` ≥ 4 권장, `docker` 도 지원) 에 두어서 이미 있는 호스트 stack 과 충돌하지 않음 (#357, #363). 0.6.0 이전 fat AppImage 는 podman stack 전체를 번들하고 호스트 것을 가렸음. 남은 호스트 측 요건: 패키지 매니저로 설치한 컨테이너 런타임, `/dev/kvm`, `kvm` 그룹 멤버십, rootless Podman 용 `/etc/subuid` / `/etc/subgid`. `winpodx setup-host` 가 kvm / subuid 부분을 `pkexec` 한 번으로 처리; `winpodx doctor` 가 그래도 부족한 걸 안내.

오프라인 / 에어갭 빌드, 소스 설치, 버전 pin, 언인스톨은 [docs/INSTALL.ko.md](INSTALL.ko.md) 참조.

## 첫 실행 설정

`curl install.sh` 원라이너 썼으면 setup 이 이미 돌았고 Windows VM 부팅 중 — [실행](#실행) 으로 skip. 그 외 install 경로 (패키지 매니저, AppImage, source, pip) 는 첫 앱 실행 전에 setup 한번 돌려야 함:

```bash
# 자동 setup — 호스트 자동감지 기본값, 프롬프트 없음
winpodx setup

# 대화형 wizard — backend, cores, RAM, edition, language, timezone, debloat preset 선택
winpodx setup --customize
```

Setup 이 `~/.config/winpodx/winpodx.toml` + `compose.yaml` 작성, GUI 런처 등록, 호스트의 FreeRDP + Podman / Docker + KVM 확인. 부족하면 출력 마지막에 distro 별 설치 명령 (예: Debian / Ubuntu 면 `sudo apt install xfreerdp3 podman podman-compose`, Fedora 면 `sudo dnf install ...`) — 실행 후 `winpodx setup` 재실행.

첫 앱 실행이 pod provision, dockur 이미지 pull, Windows ISO 다운로드 + Sysprep + OEM apply 거쳐 사용 가능한 RDP 세션까지 ~5-10분. `winpodx pod wait-ready --logs` 가 컨테이너 진행 라이브 출력:

```bash
winpodx app run desktop          # 첫 실행 ~5-10분, 이후 실행은 거의 즉시
winpodx pod wait-ready --logs    # 선택: 첫 부팅 진행상황 라이브 보기
```

이후 언제든 `winpodx doctor` 로 호스트 상태 재확인 + drift 시 다음 fix 명령 surface:

```bash
winpodx doctor                   # read-only — 필요한 fix 만 출력
winpodx guest apply-fixes          # guest 측 런타임 fix 재적용 (RDP timeout, NIC power-save 등)
```

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

**Reverse-open**
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
- 멀티모니터 RAIL (0.6.0): remote-app 윈도를 두 번째 모니터로 끌어도 입력이 계속 동작 — 기본 활성 (`cfg.rdp.multimon`, 기본 `span`)
- RAIL 전제조건이 unattended 설치 중 자동 설정

</td></tr>
<tr><td width="50%">

**제로 설정 실행**
- 첫 앱 클릭이 모든 것 자동 프로비저닝: config, 컨테이너, desktop 엔트리
- 첫 부팅 시 자동 discovery 가 실행 중인 Windows 게스트 스캔, 설치된 모든 앱 (Registry App Paths, Start Menu, UWP/MSIX, Chocolatey, Scoop) 을 실제 아이콘과 함께 등록
- `winpodx app refresh` 또는 GUI Refresh 버튼으로 언제든 수동 재스캔
- 멀티 백엔드: Podman (기본), Docker, manual RDP (libvirt 백엔드는 0.6.0 에서 제거 — 자체 libvirt 도메인을 쓰려면 ≤0.5.x 유지 또는 manual 백엔드 사용)

</td><td width="50%">

**주변기기 & 공유**
- **클립보드**: 양방향 복사-붙여넣기 (텍스트 + 이미지) — 기본 활성
- **사운드**: RDP 오디오 스트리밍 (`/sound:sys:alsa`) — 기본 활성
- **프린터**: Linux 프린터가 Windows 에 공유 — 기본 활성
- **홈 디렉토리**: `\\tsclient\home` 으로 공유
- **USB 드라이브**: FileSystemWatcher 통한 드라이브 레터 (E:, F:, …) 자동 매핑; 세션 시작 후 꽂은 USB 도 서브폴더로 접근 가능; USB 데스크톱 바로가기 (`\\tsclient\media`) 가 항상 열림 — 마운트된 미디어 없으면 에러 대신 빈 폴더로 열림
- **호스트 USB / PCI 디바이스 패스스루** (0.6.0): 실제 호스트 디바이스를 Windows 게스트로 전달 — `winpodx device list / attach <id> / detach <id>`, GUI "Devices" 탭 (호스트↔게스트 2열 mover), 시스템 트레이 USB 스위처. USB 는 라이브 핫플러그 (`cfg.pod.usb_live`, 기본 활성); PCI 는 boot-add 라 게스트 재시작 + `--force` / 다이얼로그 확인 필요

</td></tr>
<tr><td width="50%">

**자동화 & 보안**
- 자동 suspend / resume: idle 시 컨테이너 pause, 다음 실행 시 resume
- 로그인 시 pod 자동 시작 (v0.5.9, opt-in): `winpodx autostart on` 이 트레이 autostart 엔트리 설치 → 로그인 시 pod 시작/resume. 기본 off (`autostart off|status`, 또는 GUI Settings 체크박스)
- UNRESPONSIVE → 자동 복구 (v0.5.5): stalled RDP 게스트 감지 후 in-guest TermService 사이클로 self-heal — `pod restart` 불필요
- 호스트 적응형 Windows-on-KVM 튜닝 프로파일 (v0.5.5): `+invtsc`, `platform_tick` 등 호스트 capability gating — `tuning_profile = auto|safe|off`
- 비밀번호 자동 회전: 20자 암호학적 비밀번호, 7일 주기, atomic rollback
- 스마트 DPI 스케일링: GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb 자동 감지
- Windows debloat: 텔레메트리, 광고, Cortana, 검색 인덱싱 기본 비활성
- FreeRDP `extra_flags` allowlist (regex 검증) 가 사용자 input 안전 경계
- 시간 동기화: 호스트 sleep/wake 후 Windows 시계 강제 resync
- **베어메탈 위장** (0.7.0, opt-in): `pod.disguise_level balanced|max` 로 Windows 게스트가 VM 비적합 소프트웨어에게 물리 머신처럼 보임 (Nvidia GPU code 43, launch-gate 체크, VM 비적합 설치 프로그램) — balanced 는 CPUID/KVM 시그니처 은폐 + 호스트 SMBIOS 미러링; max 는 로컬 빌드 패치된 QEMU 이미지 추가 (`winpodx disguise build-image`); al-khaser 검증 완료; 기본 꺼짐

</td><td width="50%">

**운영 & 회복력**
- 다국어 UI (v0.5.9): 트레이 / GUI / CLI 가 7개 언어로 완전 번역 (en / ko / zh / ja / de / fr / it), `$LANG` 에서 자동 감지 — `winpodx language <code>` 또는 GUI Settings → "WinPodX UI language" 로 변경
- Windows 디스크 자동 확장 (v0.5.9): C: 가 idle 중 임계치 넘게 차면 호스트 여유 공간 한도 내에서 스스로 확장 — 수동은 `winpodx install grow-disk [SIZE]`, `winpodx install disk-usage`, GUI Tools → Grow Disk
- Guest sync (v0.5.9): 호스트 업그레이드 후 갱신된 agent / urlacl / rdprrap / fixes 를 실행 중인 게스트에 push — pod 시작 시 1회 자동, 또는 `winpodx guest sync [--force]`
- 오프라인 / 에어갭 설치 (`--source` + `--image-tar`)
- 원라인 언인스톨 (Windows VM 데이터 유지; `--purge` 로 전부 삭제)
- `winpodx doctor` 통한 헬스 체크 (deps / pod / RDP / agent / disk / round-trip / 비밀번호 age; `--json` 머신리더블, `--quick` 가벼운 서브셋, `--fix` 흔한 finding 의 idempotent 자동 복구)
- 재설계된 Qt6 GUI (0.6.0): 좌측 Start-menu 스타일 네비게이션 사이드바 + 새로운 **Dashboard** 홈 (라이브 Pod / RAM / CPU 링 게이지, 디스크 사용량, 자동 복구 상태 카드, pinned/recent 워크스페이스 타일, reverse-open 토글); 앱 런처는 이제 "All apps" 페이지가 되어 Devices / Settings / Tools / Terminal / Info 와 나란히 — 가벼운 시스템 트레이도 별도. 자체 SVG 아이콘 세트, 반응형 reflow, 커맨드 바를 겸하는 hero search
- stdlib 지향 Python (3.11+ 는 pip-deps 없음; 3.9 / 3.10 은 `tomli` 폴백 1개)

</td></tr>
</table>

[docs/FEATURES.ko.md](FEATURES.ko.md) 에서 멀티세션 RDP 내부, 앱 프로필 스키마, reverse-open 아키텍처 등 자세한 deep dive 확인.

## 문서

| 문서 | 내용 |
|----------|---------------|
| [INSTALL.ko.md](INSTALL.ko.md) | 모든 설치 경로 — 원라인, 패키지 매니저, AppImage, 오프라인, Nix, 소스 |
| [USAGE.ko.md](USAGE.ko.md) | CLI 레퍼런스, Qt6 GUI 투어, 헬스 체크, 설정 파일 |
| [FEATURES.ko.md](FEATURES.ko.md) | Reverse-open, 멀티세션 RDP, 주변기기, 앱 프로필, 자동 discovery |
| [ARCHITECTURE.ko.md](ARCHITECTURE.ko.md) | 동작 방식 (다이어그램), 기술 스택, 소스 트리, 데이터 흐름 |
| [COMPARISON.ko.md](COMPARISON.ko.md) | WinPodX vs winapps / LinOffice / winboat, 그리고 WinPodX vs Wine |
| [CHANGELOG.ko.md](CHANGELOG.ko.md) | 전체 버전 이력 |
| [CONTRIBUTING.ko.md](CONTRIBUTING.ko.md) | 개발 셋업 + 워크플로우 |
| [SECURITY.ko.md](SECURITY.ko.md) | 보안 공개 프로세스 |

## 지원 distro

| Distro | 패키지 매니저 | 상태 |
|--------|-----------------|--------|
| openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll | zypper | Tested |
| Fedora 42 / 43 / 44 / Rawhide | dnf | Supported |
| Fedora Silverblue / Kinoite / Sericea / Bluefin / Bazzite (42 / 43 / 44) | rpm-ostree (OBS, `--apply-live`) | Supported |
| Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10 / 26.04 | apt | Supported |
| AlmaLinux / Rocky / RHEL 9 / 10 | dnf | Supported |
| Arch / Manjaro | pacman + `yay -S winpodx` | Supported |
| NixOS (와 모든 distro 위의 Nix) | nix flake | Supported |

각 태그 푸시 (`v*.*.*`) 마다 모든 채널에 자동 publish — 메인테이너 상세는 [packaging/](../packaging/) 참조.

## 테스트

```bash
# 리포 루트에서 (설치 불필요)
export PYTHONPATH="$PWD/src"
python3 -m pytest tests/    # 1800+ 테스트
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

WinPodX 가 Linux 데스크톱을 조금이라도 더 좋게 만들었다면:

[![GitHub Sponsors](https://img.shields.io/badge/Sponsor-GitHub-EA4AAA?logo=githubsponsors&logoColor=white&style=for-the-badge)](https://github.com/sponsors/kernalix7)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-F16061?logo=ko-fi&logoColor=white&style=for-the-badge)](https://ko-fi.com/kernalix7)
[![Fairy](https://img.shields.io/badge/🧚_Fairy-EE6E73?style=for-the-badge&logoColor=white)](https://fairy.hada.io/@kernalix7)

GitHub Sponsors 는 정기 / 일시 후원; Ko-fi 는 해외 카드 / PayPal 결제; fairy.hada.io 는 국내 결제용.
버그 리포트, PR, 별점도 환영합니다 —
Bug reports, PRs, and stars on the repo are equally appreciated and free.

## 라이선스

[MIT](../LICENSE) — Kim DaeHyun (kernalix7@kodenet.io)
