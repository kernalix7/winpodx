# 설치

[English](INSTALL.md) | **한국어**

winpodx 설치하는 모든 방법 — 원라인 인스톨러, distro 패키지 매니저, Nix, 소스 빌드, 오프라인 시나리오.

## 원라인 설치

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

distro 를 감지하고, 누락된 시스템 의존성 (Podman, FreeRDP, KVM, Python 3.9+) 을 확인 후 설치, winpodx 를 `~/.local/bin/winpodx-app/` 에 배치. Windows 앱 메뉴는 pod 첫 부팅 시 자동으로 채워짐 — discovery 가 실행 중인 Windows 게스트를 스캔하고 설치된 모든 앱을 실제 아이콘과 함께 등록. 의존성 설치 단계 외에는 root 불필요. openSUSE, Fedora (Atomic Desktops 포함: Silverblue, Kinoite, Sericea, Bluefin, Bazzite), Debian/Ubuntu, RHEL-family, Arch, NixOS 에서 동작.

> **Windows 라이선스.** dockur 가 pod 첫 부팅 시 Microsoft 에서 Windows ISO 를 다운로드. 결과로 만들어진 Windows 게스트의 사용은 Microsoft 의 Software License Terms (첫 활성화 시 표시되는 EULA) 의 적용을 받음. winpodx 는 Windows 를 재배포하지 않음, 본인 머신에서의 설치를 오케스트레이션할 뿐. 활성화는 본인의 Windows 라이선스 키로 — Home / Pro / Enterprise 모두 dockur 가 지원.

기본적으로 인스톨러는 **가장 최신의 GitHub release** (현재 `v0.5.0`) 에 pin. 프리릴리스 / 개발 버전은 opt-in.

## 버전 선택

`--main` (또는 `--ref TAG`) 로 개발 빌드 사용 가능, 그렇지 않으면 기본 release 사용:

```bash
# 최신 안정 release 설치 (기본)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# 최신 main HEAD 설치 (개발용, 불안정할 수 있음)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# 특정 태그, 브랜치, 커밋 설치
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --ref v0.5.0

# 환경변수 등가 (curl | bash 에서 -s -- 없이 동작)
WINPODX_REF=main   curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
WINPODX_REF=v0.5.0 curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

## 오프라인 / 에어갭 설치

인스톨러는 registry / 패키지 저장소 접근이 없는 머신을 위한 세 가지 선택 플래그 제공:

```bash
# git clone 대신 로컬 사본에서 winpodx 복사 (환경변수: WINPODX_SOURCE)
./install.sh --source /media/usb/winpodx

# 첫 부팅 시 가져오는 대신 Windows 이미지 tar 미리 로드 (환경변수: WINPODX_IMAGE_TAR)
./install.sh --image-tar /media/usb/windows-image.tar

# distro 패키지 설치 건너뛰기 (환경변수: WINPODX_SKIP_DEPS=1) — 의존성 없으면 일찍 실패
./install.sh --skip-deps

# 한 번에:
./install.sh --source /media/usb/winpodx --image-tar /media/usb/windows-image.tar --skip-deps
```

환경변수는 `curl | bash` 에서도 동작 — `WINPODX_SKIP_DEPS=1 curl ... | bash` 가능.

## Windows 에디션 선택

기본은 dockur 의 최신 Windows 11 이미지. fresh install 시 `--win-version VER` (또는 `WINPODX_WIN_VERSION` 환경변수) 로 다른 큐레이트 에디션 선택 가능:

```bash
# Win11 대신 Windows 10 LTSC 설치
./install.sh --win-version ltsc10

# IoT Enterprise LTSC (kiosk / appliance 용 장기 지원)
./install.sh --win-version iot11

# Debloat 커뮤니티 빌드
./install.sh --win-version tiny11

# Server 2022
./install.sh --win-version 2022
```

큐레이트 셋: `11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016`. Pre-Win10 에디션 (XP / Vista / 7 / 8 / Server 2003-2012) 은 Microsoft 보안 지원이 끝났고 winpodx 의 rdprrap / agent.ps1 / install.bat 가정과 안 맞음 — WARNING 한 줄 로그하고 dockur 로 통과되지만 정식 지원 아님.

`--win-version` 플래그는 fresh install 에만 적용 (기존 `winpodx.toml` 없을 때). 기존 설치에서 에디션 변경은 GUI 설정 → Container/VM → **Windows Edition** 드롭다운 (또는 config 삭제 후 `winpodx setup --win-version VER`).

자체 커스텀 ISO 부팅은 [고급: 커스텀 Windows ISO](ARCHITECTURE.ko.md#고급-커스텀-windows-iso) 참고.

## Windows 언어 선택

기본은 **영어 (미국)**. installer 실행 후 `~/.config/winpodx/winpodx.toml` 편집해서 표시 언어, 지역 형식, 키보드 레이아웃 설정 가능 (또는 fresh install 전에 미리 생성):

```toml
[pod]
# 한국어 예시
language = "Korean"
region = "ko-KR"
keyboard = "ko-KR"
```

일반적인 언어 설정:

| 언어 | `language` | `region` | `keyboard` |
|------|------------|----------|------------|
| 영어 (미국) | `English` | `en-001` | `en-US` |
| 한국어 | `Korean` | `ko-KR` | `ko-KR` |
| 스페인어 (스페인) | `Spanish` | `es-ES` | `es-ES` |
| 스페인어 (라틴 아메리카) | `Spanish` | `es-MX` | `la-Latin` |
| 프랑스어 (프랑스) | `French` | `fr-FR` | `fr-FR` |
| 독일어 (독일) | `German` | `de-DE` | `de-DE` |
| 이탈리아어 (이탈리아) | `Italian` | `it-IT` | `it-IT` |
| 포르투갈어 (브라질) | `Portuguese` | `pt-BR` | `pt-BR` |
| 포르투갈어 (포르투갈) | `Portuguese` | `pt-PT` | `pt-PT` |
| 일본어 | `Japanese` | `ja-JP` | `ja-JP` |
| 중국어 (간체) | `Chinese` | `zh-CN` | `zh-CN` |

이 설정은 **fresh Windows 설치**에만 적용. 이미 `winpodx setup` 실행하고 Windows 를 한 번 부팅했으면:
1. `winpodx pod stop` 으로 컨테이너 중지, storage volume 삭제, config 편집 후 `winpodx setup` 재실행, **또는**
2. Windows 안에서 수동으로 설정 → 시간 및 언어 → 언어 및 지역에서 변경

지원 언어 및 지역 코드 전체 목록은 [dockur/windows 문서](https://github.com/dockur/windows#how-do-i-change-the-language) 참고.

## 네이티브 패키지 매니저

미리 빌드된 RPM 과 `.deb` 패키지가 모든 [GitHub Release](https://github.com/kernalix7/winpodx/releases/latest) 에 첨부됨 — openSUSE/Fedora RPM 은 [openSUSE Build Service (`home:Kernalix7/winpodx`)](https://build.opensuse.org/package/show/home:Kernalix7/winpodx) 에서, 나머지는 GitHub Actions 에서. [`winpodx` AUR 패키지](https://aur.archlinux.org/packages/winpodx) 는 v0.5.2 부터 라이브 — Arch 사용자는 `yay -S winpodx` 또는 `paru -S winpodx` 로 설치.

### openSUSE Tumbleweed / Leap 15.6 / Leap 16.0 / Slowroll

```bash
sudo zypper addrepo \
  https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
sudo zypper refresh
sudo zypper install winpodx
```

필요에 따라 `openSUSE_Tumbleweed` 를 `openSUSE_Leap_16.0`, `openSUSE_Leap_15.6`, `openSUSE_Slowroll` 로 교체.

### Fedora 42 / 43 / 44

```bash
sudo dnf config-manager --add-repo \
  https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx
```

`Fedora_43` 을 `Fedora_42` 또는 `Fedora_44` 로 교체.

### Fedora Atomic Desktops (Silverblue / Kinoite / Sericea / Bluefin / Bazzite)

Atomic Fedora 는 `dnf` 대신 `rpm-ostree` 사용 — 동일 OBS RPM 을 부팅된 deployment 에 `--apply-live` 로 레이어링 (재부팅 불필요, 라이브 deployment 가 받으면). 받지 못하는 경우 다음 부팅용으로 staged. 범용 `install.sh` 가 `rpm-ostree` 를 autodetect 해서 레이어링 경로 실행; 수동으로도 가능:

```bash
sudo curl -sSL \
  https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo \
  -o /etc/yum.repos.d/home-Kernalix7-winpodx.repo
sudo rpm-ostree install --apply-live winpodx     # 먼저 라이브 적용 시도
# 부팅된 deployment 에서 라이브 적용을 지원하지 않으면:
sudo rpm-ostree install winpodx                  # staged; 재부팅으로 활성화
```

`Fedora_43` 을 베이스 이미지에 맞게 `Fedora_42` 또는 `Fedora_44` 로 교체.

### Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10

[최신 release](https://github.com/kernalix7/winpodx/releases/latest) 에서 맞는 `.deb` 를 다운로드 후 설치:

```bash
sudo apt install ./winpodx_<version>_all_debian13.deb   # 본인 환경에 맞는 것 선택
```

### AlmaLinux / Rocky / RHEL 9 & 10

el9 는 `python3-tomli` 위해 EPEL 필요. [최신 release](https://github.com/kernalix7/winpodx/releases/latest) 에서 맞는 `.rpm` 다운로드 후 설치:

```bash
sudo dnf install epel-release                            # el9 만
sudo dnf install ./winpodx-<version>-1.noarch.el9.rpm    # 또는 .el10.rpm
```

### Arch Linux / Manjaro

선호하는 AUR helper 로 설치:

```bash
yay -S winpodx
# 또는
paru -S winpodx
```

PKGBUILD 는 [`packaging/aur/PKGBUILD`](../packaging/aur/PKGBUILD) 에 있고, 태그 푸시 (`v*.*.*`) 마다 버전 + tarball sha256 자동 stamp 후 `aur.archlinux.org/winpodx.git` 로 푸시.

## Nix

NixOS / nix-on-any-distro 사용자를 위한 flake 제공:

```bash
# 설치 없이 바로 실행
nix run github:kernalix7/winpodx

# 프로필에 설치
nix profile install github:kernalix7/winpodx

# flake input 으로
inputs.winpodx.url = "github:kernalix7/winpodx";
```

wrapper 가 FreeRDP, podman / podman-compose, iproute2, libnotify 를 bundle 해서 기본 Podman 백엔드는 바로 동작. Docker 와 libvirt 백엔드는 해당 도구가 호스트에 설치되어 있어야 함.

## 소스에서

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

소스 인스톨러는 자동으로:
1. distro 감지 (openSUSE, Fedora, Ubuntu, Arch, ...)
2. 누락된 의존성 (Podman, FreeRDP, KVM) 설치, 설치 전 확인
3. winpodx 를 `~/.local/bin/winpodx-app/` 로 복사
4. config 와 `compose.yaml` 생성
5. pod 첫 부팅 시 자동 discovery (`winpodx app refresh`) fire 해서 메뉴 채우기

### 수동 실행 (설치 없이)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

## 언인스톨

파이프 환경에서는 `--confirm` 또는 `--purge` 필수 (curl 이 stdin 을 소비하는 동안 대화형 프롬프트가 터미널에서 읽을 수 없음):

```bash
# winpodx 파일만 제거, Windows 컨테이너 + 데이터 유지
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --confirm

# 완전 삭제: 컨테이너, 볼륨, config, 런처, 전부
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
```

**언인스톨은 winpodx 파일만 제거.** 다음은 절대 건드리지 않음:
- Podman 컨테이너 / 볼륨 (Windows VM 데이터) — `--purge` 없이는 그대로
- 시스템 패키지 (podman, freerdp, python3)
- 홈 디렉토리 파일들
