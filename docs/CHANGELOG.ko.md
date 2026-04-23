# 변경 이력

[English](../CHANGELOG.md) | **한국어**

이 프로젝트의 주요 변경 사항은 이 문서에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 기반으로 하며,
버전 정책은 [Semantic Versioning](https://semver.org/lang/ko/)을 지향합니다.

## [Unreleased]

## [0.1.6] - 2026-04-22

### 추가
- **멀티세션 RDP — 번들/완전 오프라인.** [rdprrap](https://github.com/kernalix7/rdprrap) v0.1.0 zip (~1.6 MB, `config/oem/` 내부) 을 winpodx 패키지에 동봉하며, 최초 부팅 시 자동 설치합니다. 번들은 Windows 게스트의 `C:\OEM\` 으로 마운트되고, 핀 파일의 sha256 과 일치 여부를 확인한 뒤 압축이 풀립니다. 설치 시점에 네트워크 접근은 필요하지 않습니다. 실패 시 조용히 단일 세션으로 폴백합니다.
- `winpodx multi-session {status,enable,disable}` — RDP 세션을 열지 않고 리눅스 호스트에서 직접 패치를 관리.

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
- 컨테이너 이름, 앱 이름, 임포트된 RDP 플래그에 엄격한 allowlist 기반 입력 검증.
- 아이콘 설치 및 번들 데이터 조회 시 심볼릭 링크 / path traversal 차단.
- 정전 시 torn write 방지를 위한 원자적 쓰기 (`fsync` 포함).
- 패스워드 로그 출력 시 자동 마스킹; 로그 record args 도 초기화하여 지연 포맷팅 누출 방지.
- 인증서 정책: 로컬호스트에서만 `/cert:ignore`, 원격 연결에는 `/cert:tofu`.
- 동시 실행 레이스 방지를 위한 파일 배타 락 (PID 파일).
- 데스크탑 통합 헬퍼에 서브프로세스 타임아웃 적용 (무한 hang 방지).
- 임포트된 `winapps.conf` 의 RDP 플래그는 all-or-nothing 필터링; 부분 적용은 발생하지 않음.
- PowerShell 호출 시 사용자명 이스케이핑 (command injection 방지).

### 변경
- 기본 RDP 포트: **3390** (3389 에서 다른 컨테이너와 충돌 방지).
- 기본 VNC 포트: **8007** (LinOffice 8006 과 충돌 방지).
- FreeRDP 탐색 순서: `xfreerdp3 → xfreerdp → sdl-freerdp3 → sdl-freerdp → flatpak`.
- 언인스톨 시 항상 컨테이너 제거 (기존에는 `--purge` 에만 제거).
- 전체 데스크탑 없이 앱 창만 보이도록 RemoteApp / RAIL 활성화.
- 앱 실행마다 나오던 데스크탑 알림 제거 (너무 잦아서).
