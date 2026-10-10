# 설치

[English](INSTALL.md) | **한국어**

WinPodX 설치하는 모든 방법 — 원라인 인스톨러, distro 패키지 매니저, Nix, 소스 빌드, 오프라인 시나리오.

## 원라인 설치

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

distro를 감지하고 누락된 시스템 의존성(Podman, FreeRDP, KVM, Python 3.10+)을 확인 후 설치하며, WinPodX를 `~/.local/bin/winpodx-app/`에 배치합니다. 프로비저닝 중 게스트 Start Menu에서 발견한 앱을 아이콘과 함께 등록합니다. 설치나 앱 발견이 실패하면 메뉴가 비어 있을 수 있으므로, 게스트 준비 후 `winpodx app refresh`로 다시 검색하세요. 데스크톱 앱에는 Selawik 대체 글꼴이 포함됩니다. 의존성 설치·호스트 설정 외에는 root가 필요 없습니다. openSUSE, Fedora(Atomic Desktops 포함), Debian/Ubuntu, RHEL 계열, Arch를 지원하며, NixOS는 아래 flake를 사용합니다.

> **Windows 라이선스.** WinPodX는 사용자 컴퓨터에서 설치를 진행하며 Windows를 재배포하지 않습니다. 사용 목적에 필요한 Windows 라이선스와 권한을 Microsoft의 해당 조건에 따라 보유해야 합니다.
>
> **Windows 사용 권한을 확인하세요.** 정품 인증만으로 해당 사용에 필요한 가상화·원격 액세스·다중 사용자 권한이 모두 입증되지는 않습니다. 에디션, 라이선스 채널, 관할 지역, 사용 목적에 적용되는 Microsoft 라이선스 조건과 보유 권한을 확인하세요. WinPodX의 MIT 라이선스와 rdprrap은 Windows 사용 권한을 확장하지 않습니다.

기본적으로 인스톨러는 **가장 최신의 GitHub release** 에 pin. 프리릴리스 / 개발 버전은 opt-in.

## 버전 선택

`--main` (또는 `--ref TAG`) 로 개발 빌드 사용 가능, 그렇지 않으면 기본 release 사용:

```bash
# 최신 안정 release 설치 (기본)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash

# 최신 main HEAD 설치 (개발용, 불안정할 수 있음)
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --main

# 특정 태그, 브랜치, 커밋 설치
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --ref vX.Y.Z

# 환경변수 등가 (curl | bash 에서 -s -- 없이 동작)
WINPODX_REF=main  curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
WINPODX_REF=vX.Y.Z curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

> **업그레이드.** `install.sh` 재실행 (또는 패키지 업그레이드) 시 새 버전이 그 자리에서 설치됨. 실행 중인 시스템 트레이 / GUI 는 새 버전이 적용되도록 자동으로 재시작 — 수동 재시작 불필요. Windows pod 는 그동안 계속 실행됨. 설치 방식별 전체 절차와 Bazzite / rpm-ostree 호스트는 아래 [WinPodX 업데이트](#winpodx-업데이트) 참고.

## 수동 설치 (provisioning 건너뛰기)

바이너리만 먼저 설치하고 Windows 게스트 커스터마이즈 (edition / 언어 / debloat / tuning knob 선택) 를 ~7.5 GB ISO 다운로드 + Sysprep + OEM apply 시작 전에 하고 싶은 사용자는 `--manual` 전달:

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash -s -- --manual
# 또는 env var:
WINPODX_MANUAL=1 curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

Manual 모드는 바이너리·desktop entry·아이콘만 설치하며 프로비저닝을 건너뜁니다. 다음 bare `winpodx` 실행은 터미널 설정 선택 세 가지를 제공하고, `winpodx gui`는 여섯 페이지 설치 마법사를 엽니다:

- **Auto** — 호스트 감지 default, non-interactive (= 기본 `install.sh` 가 했을 것)
- **Customize** — wizard 모드 (모든 knob 선택); `winpodx setup --customize` 와 동등
- **Skip** — 변경 없이 종료; 다음 실행시 prompt 재발

패키지 매니저 설치 후 발화하는 flow 와 동일. wizard 원하지만 `install.sh` 중간에 인터럽트 받기 싫을 때 사용.

터미널 마법사 (`winpodx setup --customize`)는 Windows 디스크의 저장 위치와 읽을 수 있는 로컬 Windows ISO를 사용할지 묻습니다. Enter를 누르면 기본 저장 디렉터리를 유지하고 ISO를 다운로드하며, pod 생성 전에 두 선택을 검토합니다. 명시한 저장 디렉터리는 쓰기 가능하고 비어 있어야 합니다. 잘못되었거나 읽을 수 없는 경로는 Windows 설치를 시작하기 전에 오류와 함께 중단합니다. 이 질문을 통해 기존 설정이나 named volume 저장소를 이동하거나 덮어쓰거나 교체하지 않습니다. 의도적으로 위치를 옮기려면 `winpodx setup --migrate-storage`를 사용하세요. 로컬 ISO는 신규 Windows 설치에만 사용할 수 있습니다. 스크립트에서 쓰는 `winpodx setup --storage-path PATH --win-iso PATH`와 `install.sh --storage-dir PATH`도 그대로 사용할 수 있습니다.

GUI 첫 실행도 같은 선택을 여섯 페이지에서 받습니다: Welcome, Configuration, Prerequisites, Review, Install, Finish. 선택한 백엔드, RAM, 디스크, 저장 위치, 선택적 로컬 ISO를 검사한 뒤 프로비저닝합니다. 재설치는 백엔드, 저장 위치, 설치 미디어를 바꾸지 않으며, 검토 뒤에만 삭제 확인을 받습니다. 로컬 ISO만으로 완전 오프라인 설치가 보장되지는 않습니다.

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

큐레이트 셋: `11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016`. Pre-Win10 에디션 (XP / Vista / 7 / 8 / Server 2003-2012) 은 Microsoft 보안 지원이 끝났고 WinPodX 의 rdprrap / agent.ps1 / install.bat 가정과 안 맞음 — WARNING 한 줄 로그하고 dockur 로 통과되지만 정식 지원 아님.

`--win-version` 플래그는 fresh install 에만 적용 (기존 `winpodx.toml` 없을 때). 기존 설치에서 에디션 변경은 GUI 설정 → Container/VM → **Windows Edition** 드롭다운 (또는 config 삭제 후 `winpodx setup --win-version VER`).

자체 커스텀 ISO 부팅은 [고급: 커스텀 Windows ISO](ARCHITECTURE.ko.md#고급-커스텀-windows-iso) 참고.

## Windows 언어 선택

Windows 설치 언어는 기본적으로 호스트 로케일을 따릅니다. installer 실행 후 `~/.config/winpodx/winpodx.toml`을 편집해 표시 언어, 지역 형식, 키보드 레이아웃을 설정할 수 있습니다(또는 신규 설치 전에 미리 생성):

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

> **패키지 매니저 설치 후 `winpodx setup`을 한 번 실행하세요.** 패키지는 앱과 데스크톱 통합을 설치하며 Windows 프로비저닝을 자동으로 시작하지 않습니다. `winpodx setup`은 기본적으로 자동 감지 설정을 사용하고, `--customize`는 터미널 마법사를 엽니다. 준비 대기, 게스트 수정, 앱 발견, reverse-open 설정을 포함하는 공통 프로비저닝 흐름을 실행합니다. curl 인스톨러는 이 흐름을 직접 호출합니다:
>
> ```bash
> winpodx setup                # 자동 감지 기본값으로 프로비저닝
> winpodx app run desktop      # 준비된 Windows 데스크톱 실행
> ```

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
sudo dnf config-manager addrepo --from-repofile=\
https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_43/home:Kernalix7.repo
sudo dnf install winpodx
```

`Fedora_43` 을 `Fedora_42` 또는 `Fedora_44` 로 교체.

> **메모:** Fedora 41+ 는 dnf5 — 위 syntax (`addrepo --from-repofile=`) 가 그것. dnf4 (Fedora ≤40, EOL) 는 `sudo dnf config-manager --add-repo <URL>`. @payayas 가 #228 에서 제보.

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

RHEL 9, AlmaLinux 9, Rocky Linux 9에서는 기본 `python3`가 지원 최소 버전보다 낮습니다. el9 패키지는 AppStream의 Python 3.11 스택을 함께 설치합니다. [최신 release](https://github.com/kernalix7/winpodx/releases/latest) 에서 맞는 `.rpm` 다운로드 후 설치:

```bash
sudo dnf install ./winpodx-<version>-0.noarch.el9.rpm    # 또는 .el10.rpm
```

### Arch Linux / Manjaro

선호하는 AUR helper 로 설치:

```bash
yay -S winpodx
# 또는
paru -S winpodx
```

PKGBUILD 는 [`packaging/aur/PKGBUILD`](../packaging/aur/PKGBUILD) 에 있고, 태그 푸시 (`v*.*.*`) 마다 버전 + tarball sha256 자동 stamp 후 `aur.archlinux.org/winpodx.git` 로 푸시.

**개발 빌드 — `main` 추적 (`winpodx-git`).** 릴리즈 대신 최신 미공개 코드를 돌리려면:

```bash
yay -S winpodx-git
```

`winpodx-git` 은 `main` 브랜치에서 빌드 (버전은 git 에서 자동 도출 — 리빌드할 때마다 최신 커밋을 가져옴, `yay -Syu --devel` 로 자동 업데이트). `winpodx` 를 `provides`/`conflicts` 하므로 둘 중 하나만 설치. 레시피 + 메인테이너 노트: [`packaging/aur-git/`](../packaging/aur-git/).

## AppImage (Thin 번들: Python + Qt + FreeRDP + WinPodX; 호스트 컨테이너 런타임 필요)

WinPodX는 0.6.0부터 Thin x86_64 AppImage 모델을 사용합니다. Python, WinPodX, PySide6의 필요한 Qt6 모듈, FreeRDP 클라이언트를 포함하고 컨테이너 런타임은 호스트 것을 사용합니다. 이전 Fat 번들은 컨테이너 도구와 라이브러리까지 포함해 호스트 스택과 충돌할 수 있었습니다 (#357, #363). 사용하지 않는 Qt 모듈은 `packaging/appimage/slim-pyside6.sh`로 제거합니다.

0.12.0 후보 레시피는 Ubuntu 24.04의 FreeRDP `3.32.1+dfsg-0ubuntu0.24.04.1`, Python `3.11.17+20261009`, PySide6 `6.12.0`을 사용합니다. 출처 수집기는 소유 패키지, 라이선스 텍스트, wheel URL, 다운로드 해시를 기록합니다. 구현된 소스 파이프라인은 Qt/PySide, CPython, PyPI, Ubuntu, AppImage 런타임 원본을 SHA256 검증·미러링해 소스/용도 색인, 라이선스, 입력 잠금, 레시피와 함께 `winpodx-appimage-sources.tar.gz`에 담고, 태그 빌드에서 바이너리와 함께 아카이브, `PROVENANCE.json`, `SOURCE-OFFER.txt`, `SHA256SUMS`를 첨부합니다. 각 아티팩트에는 고유한 출처 및 소스 제공 기록이 있으며, 이는 포괄적인 법적 적합성이나 보안 승인을 의미하지 않습니다. 잠기지 않은 Alpine apk 입력은 런타임 재구성의 한계입니다. 모든 네이티브 구성 요소의 바이트 단위 동일 재구성을 주장하지 않습니다.

> **FreeRDP 클라이언트 선택.** 클라이언트 소스는 설정할 수 있습니다. auto 모드는 RAIL 최소 버전을 충족한 네이티브 `xfreerdp`를 우선합니다. 그렇지 않으면 Flatpak을 시도하고, Flatpak이 없으면 네이티브 클라이언트를 대안으로 유지합니다.

호스트 측 요구사항:

- distro 패키지 매니저로 설치한 컨테이너 런타임: **`podman ≥ 4` 권장**, `docker` 도 지원 (manual 백엔드는 기존 RDP 호스트를 사용). 없으면 위 RPM / DEB / AUR / `install.sh` 한 줄 설치 활용.
- 호스트 커널이 `/dev/kvm` 노출 (BIOS 에서 VT-x / AMD-V 활성화 시 대부분 distro 기본 동작).
- 현재 사용자가 `kvm` 그룹 멤버 (rootless Podman 용 `/etc/subuid` + `/etc/subgid` 엔트리 존재 — 최근 distro 는 사전 구성; `cat /etc/subuid` 로 확인).

`setup-host` 위저드는 AppImage 에 그대로 포함 — kvm 그룹 / subuid 단계를 수동 sudo 대신 polkit 한 번으로:

```bash
# 1. 최신 GitHub release 에서 AppImage 다운로드
#    -> winpodx-x86_64.AppImage

# 2. 실행 권한 부여
chmod +x winpodx-x86_64.AppImage

# 3. (선택) 첫 실행 호스트 setup — kvm 그룹, subuid/subgid, kvm 모듈
./winpodx-x86_64.AppImage setup-host          # detect + 대화형 프롬프트
./winpodx-x86_64.AppImage setup-host --apply  # 프롬프트 없이 적용
./winpodx-x86_64.AppImage setup-host --status # detect 만, mutation 없음

# 4. 로그아웃 + 재로그인 — 새 kvm 그룹 멤버십 적용

# 5. 표준 winpodx setup (cores / RAM / timezone 자동감지)
./winpodx-x86_64.AppImage setup

# 6. 데스크탑 (또는 설치된 Windows 앱 이름) 실행
./winpodx-x86_64.AppImage app run desktop
```

권장 사용 환경:

- **Immutable distro** (Fedora Silverblue / Kinoite, openSUSE Aeon, Steam Deck) — 호스트 podman 만 layering 후 사용.
- **잠긴 환경** — `curl ... | bash` 못 돌지만 다운로드 받은 단일 실행 파일은 돌 수 있는 경우.
- **친구 노트북 임시 시도** — 호스트에 podman / docker 가 이미 있을 때.

비권장:

- 시스템 패키지 매니저 통합 선호 (위 RPM / DEB / AUR 경로 추천).
- 시스템 업데이트 cycle 로 자동 업데이트 받기 원함 (AppImage 는 수동 다운로드 + 교체; AppImageUpdate 연동 별도 작업).
- 진정한 호스트 zero-prep — Thin AppImage 는 호스트 컨테이너 런타임 필요. 설치까지 위임하고 싶으면 `install.sh` 사용.

레시피와 CI 는 `packaging/appimage/` 에 — 로컬 빌드는 `packaging/appimage/README.md` 참조. 회귀 테스트 (`tests/test_appimage_recipe.py`) 가 `podman` / `podman-compose` / `conmon` / `crun` / `netavark` / `aardvark-dns` / `passt` / `pasta` / `slirp4netns` / `fuse-overlayfs` 중 어느 하나라도 레시피에 다시 들어오는 순간 CI 실패.

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

wrapper가 FreeRDP, Podman, podman-compose, iproute2, libnotify를 `PATH`에 추가합니다. 호스트의 KVM과 rootless 컨테이너 설정도 필요합니다. Docker 백엔드는 호스트에 Docker가 설치되어 있어야 하며, manual 백엔드는 직접 제공하는 RDP 호스트에 연결합니다.

## 소스에서

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
./install.sh
```

소스 인스톨러는 자동으로:
1. distro 감지 (openSUSE, Fedora, Ubuntu, Arch, ...)
2. 누락된 의존성 (Podman, FreeRDP, KVM) 설치, 설치 전 확인
3. WinPodX 를 `~/.local/bin/winpodx-app/` 로 복사
4. config 와 `compose.yaml` 생성
5. pod 첫 부팅 시 자동 discovery (`winpodx app refresh`) fire 해서 메뉴 채우기

### 수동 실행 (설치 없이)

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
export PYTHONPATH="$PWD/src"
python3 -m winpodx app run word
```

## WinPodX 업데이트

별도 update 명령은 없으며, 설치한 방식과 같은 방식으로 업데이트합니다.

**curl 원라이너 / 소스 설치** (`~/.local/bin/winpodx-app/`): 설치할 때 쓴 명령을 그대로 다시 실행:

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/install.sh | bash
```

기존 설치를 감지해 venv 만 교체하고 config 와 Windows VM 디스크는 그대로 유지합니다. 업그레이드 후 첫 실행 시 `migrate` 체인이 자동으로 갱신된 guest 스크립트(agent, rdprrap, OEM fix)를 실행 중인 guest에 push하고 release note를 표시합니다. 실행 중인 트레이 / GUI도 자동 재시작하며 Windows pod는 계속 실행됩니다. curl 원라이너로 설치한 **Bazzite 및 기타 rpm-ostree 호스트**도 같은 명령으로 업데이트하며 `--main`이나 VM 초기화가 필요 없습니다. 로컬 clone은 `git pull && ./install.sh` 사용.

**openSUSE / Fedora (OBS RPM):**

```bash
sudo zypper refresh && sudo zypper update winpodx      # openSUSE
sudo dnf update winpodx                                 # Fedora
```

**Fedora Atomic Desktop (rpm-ostree):** `sudo rpm-ostree upgrade` 실행. live 적용이 아니라 staged 되었다고 출력될 때만 재부팅합니다.

**Debian / Ubuntu:** 최신 release의 새 `.deb`를 기존 패키지 위에 설치:

```bash
sudo apt install ./winpodx_<version>_all_debian13.deb
```

**AlmaLinux / Rocky / RHEL:** 최신 `.rpm`을 기존 패키지 위에 설치:

```bash
sudo dnf install ./winpodx-<version>-0.noarch.el10.rpm
```

**Arch:** `yay -S winpodx` (또는 `paru -S winpodx`). `winpodx-git`은 `yay -Syu --devel`로 최신 commit을 가져옵니다.

**AppImage:** 최신 release의 새 AppImage로 기존 파일을 교체합니다. in-place self-update는 없습니다.

**Nix:** profile 설치는 `nix profile upgrade winpodx`, 직접 실행은 `nix run github:kernalix7/winpodx`를 다시 실행합니다.

어느 경로든 `winpodx --version`으로 실행 버전을 확인할 수 있습니다. guest가 host보다 뒤처지면 `winpodx doctor`가 version drift를 알리고 `winpodx guest sync --force`로 guest 스크립트를 다시 push할 수 있지만, 보통 다음 pod 시작 때 자동 동기화됩니다.

## 언인스톨

설치 경로 (curl / pip / deb / rpm / aur) 무관하게 동일한 canonical 스크립트 하나.

두 진입점 동일 동작:

```bash
# 모든 동작 중단, Windows 컨테이너 + config 유지 (apt remove 의미)
./uninstall.sh
# 또는
winpodx uninstall

# 완전 삭제: 컨테이너 + 볼륨 + ~50GB Windows 디스크 + config + 런처 (apt purge 의미)
./uninstall.sh --purge --yes
# 또는
winpodx uninstall --purge --yes
```

`winpodx uninstall` 은 thin wrapper — 셋 중 하나에서 `uninstall.sh` 찾아서 exec: `/usr/share/winpodx/uninstall.sh` (deb/rpm/aur), `~/.local/bin/winpodx-app/uninstall.sh` (curl), wheel shared-data 디렉토리 (pip).

파이프 환경에서는 `--yes` 또는 `--purge` 필수 (curl 이 stdin 을 소비하는 동안 대화형 프롬프트가 터미널에서 읽을 수 없음):

```bash
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --yes
curl -fsSL https://raw.githubusercontent.com/kernalix7/winpodx/main/uninstall.sh | bash -s -- --purge
```

패키지 매니저 (apt/dnf/zypper/pacman) 로 설치한 경우, `uninstall.sh` 가 이를 감지하고 패키지 제거를 먼저 권장 — 올바른 순서: 패키지 매니저의 post-remove hook 이 `uninstall.sh` 를 user-side cleanup 용으로 재실행, 패키지 DB 와 디스크 상태 동기화.

**기본 모드는 절대 안 건드림** (전부 지우려면 `--purge`):
- Podman 컨테이너 / 볼륨 (Windows VM 디스크, ~50GB)
- `~/.config/winpodx/` config + compose.yaml
- Storage bind-mount 내용

**둘 다 절대 안 건드림:**
- 시스템 패키지 (podman, freerdp, python3) — 패키지 매니저 제거 prompt 가 처리
- 홈 디렉토리의 다른 파일들
