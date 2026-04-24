# 변경 이력

[English](../CHANGELOG.md) | **한국어**

이 프로젝트의 주요 변경 사항은 이 문서에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 기반으로 하며,
버전 정책은 [Semantic Versioning](https://semver.org/lang/ko/)을 지향합니다.

## [Unreleased]

## [0.1.8] - 2026-04-25

### 추가
- **Windows 앱 동적 발견.** 새 CLI `winpodx app refresh` 서브커맨드와 Qt GUI Apps 페이지의 "Refresh Apps" 버튼이 Windows 게스트에 실제 설치된 앱을 열거하고 기본 번들 14개 프로필과 함께 등록합니다. 컨테이너 내부에서 `scripts/windows/discover_apps.ps1` 이 Registry `App Paths` (HKLM + HKCU), Start Menu `.lnk` 재귀, UWP/MSIX (`Get-AppxPackage` + `AppxManifest.xml`), Chocolatey / Scoop shim 4개 소스를 스캔하고 실제 바이너리/패키지 로고에서 추출한 base64 아이콘을 포함한 JSON 배열을 반환합니다. Linux 호스트 (`winpodx.core.discovery`) 는 `podman cp` 로 스크립트를 복사하고 `podman exec powershell` 로 실행한 뒤, 결과를 `~/.local/share/winpodx/discovered/<slug>/` 아래 TOML + PNG/SVG 아이콘 파일로 저장합니다. 번들 / 사용자 직접 추가 / 발견 앱은 세 디렉토리로 분리 관리되며 로딩 시 "사용자 > 발견 > 번들" 우선순위로 병합됩니다 — 재발견 실행은 발견 트리만 건드립니다.
- **UWP RemoteApp 실행.** `rdp.build_rdp_command` 가 `launch_uri` + 엄격 정규식 검증된 AUMID (`<PackageFamilyName>!<AppId>`) 를 받아 UWP 앱을 `/app:program:explorer.exe,cmd:shell:AppsFolder\<AUMID>` 로 매핑합니다. `/wm-class` fallback 이 `winpodx-uwp-<aumid-slug>` 로 슬러그당 고유하게 지정되어 두 UWP 앱이 같은 힌트를 공유할 때도 Linux 태스크바 그루핑이 분리됩니다.
- **CI PowerShell Core smoke 테스트.** 새 `discover-apps-ps` 잡이 Ubuntu runner 에 `pwsh` 를 설치하고 모든 PR 에서 `discover_apps.ps1 -DryRun` 을 실행해 `core.discovery` 가 기대하는 JSON 배열 shape 을 stdout 이 파싱 가능한지 검증합니다.
- **업그레이드 후 마이그레이션 위자드.** 새 CLI `winpodx migrate` 가 사용자가 건너뛴 모든 버전의 릴리즈 노트를 순차적으로 보여주고, 원하면 `winpodx app refresh` 를 바로 실행해 Windows 앱 메뉴를 한 번에 채워 줍니다. `install.sh` 는 업그레이드 감지 시 (`~/.config/winpodx/winpodx.toml` 존재) `winpodx migrate` 를 자동 호출합니다 — 건너뛰려면 `WINPODX_NO_MIGRATE=1` 설정. 자동화용 플래그: `--no-refresh` (discovery 만 스킵), `--non-interactive` (모든 프롬프트 비활성화). 위자드는 `~/.config/winpodx/installed_version.txt` 에 현재 버전을 기록하며, 이 파일이 없는 사전-0.1.8 설치는 `0.1.7` 에서 업그레이드하는 것으로 간주합니다.
- **`pod.max_sessions` 설정 노출.** 기본값 10 유지, `[1, 50]` 범위로 clamp. `ensure_ready()` 가 매 provisioning 시 값을 읽어 게스트의 `HKLM:\...\Terminal Server\MaxInstanceCount` 와 비교하고, 다를 때만 레지스트리 재작성 + `TermService` 재기동 — 활성 RemoteApp 세션이 매번 끊기지 않습니다. 적용 시 `fSingleSessionPerUser=0` 도 함께 재확정. `winpodx.core.config` 의 `estimate_session_memory` / `check_session_budget` 헬퍼가 `winpodx config show`, `winpodx config set`, `winpodx info`, 그리고 GUI Settings 페이지에서 **`max_sessions` 가 `ram_gb` 예산을 초과할 때에만** 경고를 표시합니다 — 기본 설정은 조용합니다.
- **오프라인 / 에어갭 설치용 `install.sh` 로컬 경로 플래그.** `--source PATH` 는 git clone 대신 로컬 디렉토리에서 winpodx 를 복사합니다 (`pyproject.toml` + `src/winpodx/` 존재 검증). `--image-tar PATH` 는 `podman load -i` (또는 `docker load -i`) 로 Windows 컨테이너 이미지를 사전 로드해 최초 부팅 시 레지스트리 접근이 필요 없게 합니다. `--skip-deps` 는 배포판 의존성 설치 단계를 완전히 스킵하며 필수 도구가 이미 설치돼 있지 않으면 즉시 실패합니다. 각 플래그에 대응하는 환경 변수 (`WINPODX_SOURCE`, `WINPODX_IMAGE_TAR`, `WINPODX_SKIP_DEPS`) 도 제공 — `curl | bash` 호출도 조합 가능. `install.sh --help` 로 전체 사용법 확인.

### 변경
- `AppInfo` 에 `source: "bundled" | "discovered" | "user"`, `args`, `wm_class_hint`, `launch_uri` 필드 추가. GUI 가 발견 엔트리를 뱃지로 구분할 수 있고 RDP 실행이 UWP 앱을 타겟팅할 수 있게 됩니다.
- `desktop.entry._install_icon` 이 아이콘 파일 확장자에 따라 `hicolor/scalable/apps/` (SVG) vs `hicolor/32x32/apps/` (PNG) 로 분기 설치. 발견 앱의 추출된 PNG 아이콘이 번들 SVG 아이콘과 나란히 깔끔하게 설치됩니다.

## [0.1.7] - 2026-04-23

### 변경
- **번들된 rdprrap 을 v0.1.3 로 갱신 (라이선스 컴플라이언스 릴리즈).** 업스트림이 0.1.0, 0.1.1, 0.1.2 GitHub 릴리즈 자산을 모두 철회했습니다. 0.1.0 / 0.1.1 은 rdprrap 이 코드를 포팅해 온 세 업스트림(`stascorp/rdpwrap` Apache-2.0, `llccd/TermWrap` MIT, `llccd/RDPWrapOffsetFinder` MIT) 이 요구하는 소스 레벨 저작자 고지(attribution notices) 가 누락되어 있었습니다. 0.1.2 는 `NOTICE` + `vendor/licenses/` 를 추가해 법적 공백은 해소했지만, rdpwrap 파생 Rust 소스 16개 중 9개만 나열하고 `rdprrap-conf` About 다이얼로그의 copyright 라인이 `LICENSE` 와 불일치하는 위생 문제가 남아 있었습니다. 0.1.3 은 `NOTICE` 를 업스트림 바이너리별(RDPWInst / RDPConf / RDPCheck)로 재편해 16개 전부를 열거하고, About 다이얼로그 copyright 를 `LICENSE` 와 정렬했으며, 채택한 Contributor Covenant 텍스트에 CC BY 4.0 출처를 명시합니다. 0.1.1 의 레지스트리 readback 수정(`OriginalServiceDll` 이 `termsrv.dlll` 로 저장되던 문제) 도 그대로 포함합니다. 새 번들 SHA256 은 `config/oem/rdprrap_version.txt` 에 고정되며, 기존 게스트도 컴플라이언스 번들로 재설치되도록 first-boot OEM 버전을 6 으로 올렸습니다.

### 문서
- 최상위 [`THIRD_PARTY_LICENSES.md`](../THIRD_PARTY_LICENSES.md) 추가. 번들된 rdprrap 바이너리와 런타임/선택 Python 의존성(PySide6 LGPL, libvirt-python LGPL, docker-py Apache-2.0, tomli MIT) 을 문서화합니다.
- `debian/copyright` 가 번들된 rdprrap 파일을 별도 선언하도록 보강했고, ZIP 내부의 `NOTICE` / `vendor/licenses/` 텍스트가 업스트림 Apache-2.0 / MIT 저작자 고지 요건을 충족한다는 사실을 명시했습니다.

### 수정
- **`install.sh` 가 `curl … | bash` 경로에서 정상 동작.** 파이프로 실행되면 bash 가 stdin 에서 스크립트를 읽으므로 `BASH_SOURCE[0]` 가 unset 상태가 되고, 파일 상단의 `set -u` 가드와 결합되어 install.sh 205 줄에서 `BASH_SOURCE[0]: unbound variable` 로 리포 클론 전에 중단되었습니다. 로컬/원격 분기가 소스 경로를 빈 값으로 기본 처리하도록 변경되어, 로컬 소스 트리가 없을 때 자연스럽게 git clone 경로로 폴백합니다. CachyOS + Python 3.14 + fish shell 환경에서 리포트 ([#3](https://github.com/kernalix7/winpodx/issues/3)).

### 보안 / 컴플라이언스
- rdprrap 0.1.0 을 번들한 winpodx 0.1.6 은 동일한 저작자 고지 누락 결함을 그대로 가지고 있었습니다. 0.1.6 GitHub 릴리즈 자산은 철회되었으며(태그는 보존), 0.1.7 이 Windows 게스트에 컴플라이언스 rdprrap 번들(0.1.3, `NOTICE` + `vendor/licenses/` 포함) 을 내려주는 첫 winpodx 릴리즈입니다.

## [0.1.6] - 2026-04-22

### 추가
- **멀티세션 RDP — 번들/완전 오프라인.** [rdprrap](https://github.com/kernalix7/rdprrap) v0.1.0 zip (~1.6 MB, `config/oem/` 내부) 을 winpodx 패키지에 동봉하며, Windows 무인 설치 단계에서 자동 적용합니다. 번들은 게스트 최초 부팅 시 `C:\OEM\` 로 스테이징되고, 핀 파일의 sha256 과 일치 여부를 확인한 뒤 압축이 풀립니다. 설치 시점에 네트워크 접근은 필요하지 않습니다. 실패 시 조용히 단일 세션으로 폴백합니다. 게스트 측 관리 채널(설치 후 enable/disable/status)은 향후 릴리즈로 예정되어 있습니다.

## [0.1.5] - 2026-04-21

### 추가
- **AlmaLinux 9 / AlmaLinux 10** 용 prebuilt RPM 추가 (RHEL 9/10, Rocky 9/10 에도 그대로 설치 가능). 모든 GitHub Release 에 자동 첨부.
- Arch Linux AUR 패키징 인프라 추가 (메인테이너 1회 세팅 후 활성화 — 자세한 절차는 [`packaging/aur/README.md`](../packaging/aur/README.md)).

### 변경
- **최소 Python 버전을 3.11 → 3.9 로 낮춤.** 기본 `python3` 가 3.9 인 배포판 (RHEL 9 / AlmaLinux 9 / Rocky 9) 에 별도 Python 모듈 없이 바로 설치 가능.

### 수정
- OBS RPM 자동 다운로드가 새로 퍼블리시된 에셋을 제대로 수거하도록 수정.

## [0.1.4] - 2026-04-21

### 수정
- `.deb` 빌드가 "missing files" 로 실패하던 문제 해결.
- 타겟 매트릭스 외의 마이너 아키텍처에서 발생하는 빌드 서비스 측 문제로 인해 OBS 퍼블리시가 실패로 찍히지 않도록 개선.

## [0.1.3] - 2026-04-21

### 수정
- OBS 퍼블리시 단계가 빌드 대기 중 인증 에러 루프에 빠지지 않도록 수정.
- `.deb` 빌드가 테스트 스위트를 돌리지 않도록 수정 (테스트는 GitHub Actions 업스트림에서 실행).

## [0.1.2] - 2026-04-21

### 수정
- 태그 푸시 이후 RPM / `.deb` 퍼블리시 워크플로우가 제대로 실행되어 Release 에 아티팩트가 첨부되도록 수정.
- 업스트림 `pyproject.toml` 버전이 최신 git 태그보다 앞서있어도 RPM 빌드가 실패하지 않도록 개선.

## [0.1.1] - 2026-04-21

### 추가
- **Release 별 prebuilt 패키지**:
  - RPM: openSUSE Tumbleweed, Leap 15.6, Leap 16.0, Slowroll, Fedora 42, Fedora 43.
  - `.deb`: Debian 12 / 13, Ubuntu 24.04 / 25.04 / 25.10.
  - 소스 dist + wheel.
- README "설치" 섹션에 배포판별 설치 방법 추가.

### 변경
- AppImage 패키징 제거: Python + Qt + FreeRDP + Podman 의존성 때문에 단일 파일 배포의 이점이 거의 없음.

### 수정
- 주간 업스트림 업데이트 체크가 권한 에러로 실패하지 않고 추적용 Issue 를 생성하도록 변경.

## [0.1.0] - 2026-04-21

첫 공개 릴리즈.

### 추가
- **Zero-config 자동 프로비저닝**: 첫 앱 실행 시 설정 파일 생성, compose 파일 생성, 컨테이너 시작, 데스크탑 엔트리 등록이 자동으로 수행됨.
- **14개 번들 앱 정의**: Word, Excel, PowerPoint, Outlook, OneNote, Access, 메모장, 탐색기, CMD, PowerShell, 그림판, 계산기, VS Code, Teams.
- **자동 서스펜드 / 리줌**: 유휴 시 컨테이너 일시정지, 다음 앱 실행 시 자동 복구; 종료 시 정상 셧다운.
- **패스워드 자동 로테이션**: 암호학적 난수 20자 패스워드, 7일마다 교체 (설정 가능), 실패 시 자동 롤백.
- **수동 패스워드 로테이션**: `winpodx rotate-password`.
- **Office 락 파일 정리**: `winpodx cleanup` 이 홈 디렉터리의 `~$*.*` 락 파일 제거.
- **Windows 시간 동기화**: `winpodx timesync` 로 호스트 sleep/wake 후 시계 재동기화.
- **Windows 디블로트**: `winpodx debloat` 로 텔레메트리, 광고, Cortana, 검색 인덱싱 비활성화.
- **전원 관리**: `winpodx power --suspend/--resume` 로 컨테이너 수동 일시정지/복구.
- **시스템 진단**: `winpodx info` 로 디스플레이, 의존성, 설정 상태 확인.
- **데스크탑 알림** (D-Bus / `notify-send`) 앱 실행 시 자동 표시.
- **스마트 DPI 스케일링**: GNOME, KDE Plasma 5/6, Sway, Hyprland, Cinnamon, env var, xrdb 에서 스케일 자동 감지.
- **Qt 시스템 트레이**: pod 제어, 앱 런처, 유지보수 도구, 유휴 모니터, 자동 새로고침.
- **멀티 백엔드**: Podman (기본), Docker, libvirt/KVM, manual RDP — 통일된 인터페이스.
- Podman/Docker 백엔드용 **compose 파일 자동 생성** (`dockur/windows` 이미지 사용).
- **앱별 작업표시줄 분리**: 각 앱이 고유한 WM_CLASS / `StartupWMClass` 보유.
- **Windows 빌드 고정**: `TargetReleaseVersion` 정책으로 기능 업데이트 차단, 보안 업데이트는 유지.
- **업스트림 업데이트 모니터링**: `dockur/windows` 신규 릴리즈를 매주 체크.
- **동시 실행 보호**: 쓰레딩 락으로 동시 앱 실행 시 크래시 방지.
- GUI 의 **Windows Update 토글** (서비스 + 예약 작업 + hosts 파일 3중 차단).
- **사운드 + 프린터** 리다이렉션 기본 활성화.
- **USB 드라이브 공유** + hot-plug (재연결 없이 subfolder 로 표시).
- FreeRDP `urbdrc` 사용 가능 시 **USB 장치 리다이렉션**; 없으면 드라이브 공유로 graceful fallback.
- Windows 측 **USB 자동 드라이브 문자 매핑** (이벤트 기반, 폴링 없음).
- 데스크탑 통합: `.desktop` 엔트리, hicolor 아이콘, MIME 등록, 아이콘 캐시 리프레시.
- 자격 증명 보호용 제한 권한 (`0600`) TOML 설정 파일.
- 프로세스 추적 + 좀비 리퍼 포함 FreeRDP 세션 관리.
- `winapps.conf` 임포트 (기존 winapps 설정 마이그레이션용).

### 보안
- RDP 를 **127.0.0.1** 에만 바인딩; 네트워크 노출 없음.
- **TLS 전용** RDP 채널 (SecurityLayer=2); NLA 는 loopback 바인딩 환경에서만 비활성화.
