# 변경 이력

[English](../CHANGELOG.md) | **한국어**

이 프로젝트의 주요 변경 사항은 이 문서에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 기반으로 하며,
버전 정책은 [Semantic Versioning](https://semver.org/lang/ko/)을 지향합니다.

## [Unreleased]

### 추가됨
- **제로 설정 자동 프로비저닝**: 첫 앱 실행 시 설정, compose.yaml, 팟 시작, 데스크톱 엔트리 자동 생성
- **14개 번들 앱 정의**: Word, Excel, PowerPoint, Outlook, OneNote, Access, Notepad, Explorer, CMD, PowerShell, Paint, Calculator, VS Code, Teams
- **자동 일시정지/재개**: 비활성 시 컨테이너 일시정지, 앱 실행 시 자동 재개, stop_event를 통한 정상 종료
- **비밀번호 자동 로테이션**: 암호학적으로 안전한 랜덤 비밀번호 (20자), 7일마다 자동 변경 (`password_max_age` 설정 가능), 실패 시 롤백
- **`winpodx rotate-password`**: 수동 비밀번호 변경 명령
- **Office 잠금 파일 정리**: `winpodx cleanup`으로 홈 디렉토리의 `~$*.*` 파일 제거
- **Windows 시간 동기화**: `winpodx timesync`로 호스트 sleep/wake 후 시계 강제 동기화
- **Windows 디블로트**: `winpodx debloat`로 텔레메트리, 광고, Cortana, 검색 인덱싱 비활성화
- **전원 관리**: `winpodx power --suspend/--resume`으로 컨테이너 수동 일시정지/재개
- **시스템 진단**: `winpodx info`로 디스플레이, 의존성, 설정 상태 확인
- **데스크톱 알림**: 앱 실행 시 D-Bus/notify-send를 통한 알림
- **스마트 DPI 스케일링**: GNOME, KDE Plasma 5/6, Sway, Hyprland, Cinnamon, 환경변수, xrdb 자동 감지
- **Qt 시스템 트레이**: 팟 제어, 앱 런처, 유지보수 도구, 유휴 모니터, 자동 갱신
- **백엔드 추상화**: Podman (기본), Docker, libvirt/KVM, 수동 RDP 통합 인터페이스
- **compose.yaml 자동 생성**: Podman/Docker 백엔드용 dockur/windows 이미지 설정
- **RDPWrap 멀티세션**: 여러 Windows 앱을 독립 창에서 동시 실행 (세션 재연결 깜빡임 없음)
- **RDPWrapOffsetFinder 통합**: 첫 부팅 시 실제 `termsrv.dll`에서 `rdpwrap.ini` 자동 생성 (Microsoft 심볼 서버 사용) — 커뮤니티 INI 의존 없음
- **앱별 작업 표시줄 분리**: 앱마다 독립 WM_CLASS 및 `StartupWMClass`로 작업 표시줄 아이콘 분리
- **Windows 빌드 고정**: `TargetReleaseVersion` 레지스트리 정책으로 feature update 차단, 보안 업데이트만 허용
- **CI: Build RDPWrap 워크플로우**: `windows-latest`에서 RDPWrap + OffsetFinder 소스 빌드 (수동 트리거)
- **CI: 업스트림 업데이트 모니터링**: stascorp/rdpwrap, llccd/RDPWrapOffsetFinder, dockur/windows 매주 확인 → 자동 PR 생성
- **GUI: Update RDPWrap 버튼**: 설정 패널에서 수동 INI 재생성
- **GUI: 컨테이너 재시작 프롬프트**: CPU, RAM, 포트 설정 변경 시 재시작 확인
- **GUI: 스케일 드롭다운**: FreeRDP 스케일을 유효 값(100%/140%/180%)으로 제한 (QComboBox)
- **GUI: 동시 실행 보호**: 스레딩 잠금으로 동시 앱 실행 충돌 방지
- **GUI: Windows Update 토글**: 활성화/비활성화 버튼 + 상태 표시, 3중 차단 (서비스 + 예약 작업 + hosts 파일)
- 데스크톱 통합: `.desktop` 엔트리, hicolor 아이콘, MIME 타입 등록, 아이콘 캐시 갱신
- argparse 기반 CLI: app, pod, config, setup, tray, info, cleanup, timesync, debloat, power, rotate-password 명령
- TOML 설정 파일 (자격 증명 보호를 위한 0600 권한)
- FreeRDP 세션 관리 및 프로세스 추적 (.cproc 파일), 좀비 프로세스 리퍼
- winapps.conf 가져오기 (기존 설정 마이그레이션)

### 보안
- 설정 및 compose.yaml 파일 0600 권한 생성
- RDP 인증서: localhost는 `/cert:ignore`, 원격은 `/cert:tofu` (Trust On First Use)
- 로그 출력에서 비밀번호 필터링
- 앱 이름 검증 (영숫자 + 대시/언더스코어만 허용, 인젝션 방지)
- 알림 텍스트 산타이즈 (제어문자 제거, HTML 이스케이프, 길이 제한)
- PID 파일 배타적 잠금 (`fcntl.flock`) — 동시 실행 시 레이스 컨디션 방지
- 좀비 프로세스 리퍼 (RDP 프로세스당 데몬 스레드) — 프로세스 테이블 누수 방지
- `_apply()` 설정 로딩 시 `dataclasses.fields()` 허용 목록 — 임의 속성 주입 방지
- SecurityLayer=2 (TLS) — OEM 설치 및 레지스트리 템플릿에서 암호화된 RDP 채널
- Podman 백엔드에서 TLS 전용 RDP 인증 (`/sec:tls`) — `podman unshare` 네임스페이스에서 NLA/Kerberos 실패
- RDPWrap CI 소스 빌드 (Apache 2.0 라이선스) — 사전 빌드 바이너리 없음
- RDPWrap/OffsetFinder 라이선스 파일 바이너리와 함께 번들 (Apache 2.0 / MIT 준수)
- 종료 코드 145 (SIGTERM) 정상 앱 종료로 처리, 에러 아님
- debloat에서 subprocess 에러 처리 및 타임아웃 (CLI + GUI)

### 변경됨
- 기본 RDP 포트 3389 → 3390 (다른 컨테이너와 충돌 방지)
- 기본 VNC 포트 8007 (LinOffice 8006과 충돌 방지)
- FreeRDP 탐색 순서: xfreerdp3 → xfreerdp → sdl-freerdp3 → sdl-freerdp → flatpak
- `wlfreerdp` 탐색 순서에서 제거 (FreeRDP 프로젝트에서 공식 지원 종료)
- 언인스톨 시 항상 컨테이너 제거 (이전: `--purge` 플래그 필요)

### 변경됨
- FreeRDP에 `podman unshare --rootless-netns` 래퍼 — rootless Podman RDP 접속에 필수
- 앱별 데스크톱 알림 제거 (매번 실행 시 알림이 과다)

### 제거됨
- `data/templates/app.desktop.j2` (미사용 Jinja2 템플릿)
- 데드코드: `icons_cache_dir()`, `decode_base64_icon()`, `MISSING_DEPS_MSG`
