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
- **앱별 작업 표시줄 분리**: 앱마다 독립 WM_CLASS 및 `StartupWMClass`로 작업 표시줄 아이콘 분리
- **Windows 빌드 고정**: `TargetReleaseVersion` 레지스트리 정책으로 feature update 차단, 보안 업데이트만 허용
- **CI: 업스트림 업데이트 모니터링**: dockur/windows 매주 확인 → 자동 PR 생성
- **GUI: 컨테이너 재시작 프롬프트**: CPU, RAM, 포트 설정 변경 시 재시작 확인
- **GUI: 스케일 드롭다운**: FreeRDP 스케일을 유효 값(100%/140%/180%)으로 제한 (QComboBox)
- **GUI: 동시 실행 보호**: 스레딩 잠금으로 동시 앱 실행 충돌 방지
- **GUI: Windows Update 토글**: 활성화/비활성화 버튼 + 상태 표시, 3중 차단 (서비스 + 예약 작업 + hosts 파일)
- **사운드 및 프린터**: RDP 오디오 (`/sound:sys:alsa`) 및 프린터 리다이렉션 (`/printer`) 기본 활성화
- **USB 드라이브 공유**: 이동식 미디어 `/drive:media`로 자동 공유 — 세션 시작 후 꽂은 USB도 하위 폴더로 즉시 접근 가능
- **USB 장치 리다이렉션**: `/usb:auto` 기본 활성화 — FreeRDP urbdrc 플러그인 있으면 Windows에서 진짜 USB로 인식, 없으면 드라이브 공유로 폴백
- **USB 자동 드라이브 매핑**: Windows 측 FileSystemWatcher 스크립트가 USB 하위 폴더를 드라이브 문자(E:, F:, ...)로 자동 매핑, 제거 시 해제 — 이벤트 기반, 폴링 없음
- 데스크톱 통합: `.desktop` 엔트리, hicolor 아이콘, MIME 타입 등록, 아이콘 캐시 갱신
- argparse 기반 CLI: app, pod, config, setup, tray, info, cleanup, timesync, debloat, power, rotate-password 명령
- TOML 설정 파일 (자격 증명 보호를 위한 0600 권한)
- FreeRDP 세션 관리 및 프로세스 추적 (.cproc 파일), 좀비 프로세스 리퍼
- winapps.conf 가져오기 (기존 설정 마이그레이션)

### 보안
- 설정 파일 원자적 쓰기: `os.fsync(fd)` + 상위 디렉토리 fsync + `os.replace()` — 전원 차단 시 파일 깨짐 방지
- `bundled_data_path()` 심볼릭링크/경로 트래버설 방어: `.resolve()` + `is_relative_to()` 체크 및 `copy2(..., follow_symlinks=False)` — 번들 데이터 디렉토리 밖을 가리키는 설치 차단
- 데스크톱 통합 subprocess 타임아웃 (`update_icon_cache`, `notify-send`) — 응답 없는 헬퍼로 인한 무한 대기 방지
- `import_winapps_config`가 신뢰할 수 없는 `RDP_FLAGS`를 `_filter_extra_flags()`로 필터 — 악성 winapps.conf가 임의 FreeRDP 플래그 주입 차단
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
- 종료 코드 145 (SIGTERM) 정상 앱 종료로 처리, 에러 아님
- debloat에서 subprocess 에러 처리 및 타임아웃 (CLI + GUI)
- PowerShell 사용자 이름 이스케이프: `net user` 호출 시 작은따옴표를 두 번으로 변환하여 명령 주입 방지
- 비밀번호 타임스탬프 타임존 처리: naive 타임스탬프를 UTC로 업그레이드, `TypeError`도 `ValueError`와 함께 catch

### 수정됨
- 설정 `_apply()` bool 강제변환: `bool("false")`가 `True`를 반환하던 버그 — 명시적 문자열 매핑으로 수정
- 비밀번호 롤백: 이미 덮어쓴 새 비밀번호로 되돌리던 버그 — 원본 비밀번호 보존 후 롤백
- RDP `launch_app()` lock 파일 누수: `Popen` 실패 시 PID 파일 미정리 — 예외 경로에서 정리
- DPI 감지: `_xrdb_scale()` DPI 0 방어 — 0.0 스케일 팩터 방지
- YAML 이스케이프: `_yaml_escape()`에 `\n`, `\r` 처리 추가 — YAML 구조 인젝션 방지
- libvirt `get_ip()`: returncode 확인 및 `TimeoutExpired` 예외 처리 추가
- FreeRDP RemoteApp: RAIL 모드에서 즉시 전송 실패를 유발하던 `/rfx` 플래그 제거
- RDP reaper 스레드: stderr 파이프 데드락 — 64KB 파이프 버퍼가 꽉 차면 `proc.wait()`가 무한 대기; `communicate()` 사용으로 변경, 마지막 2KB를 세션에 저장
- TOML writer: 제어문자 0x00-0x1F, 0x7F이 이스케이프 없이 출력되어 파일 깨짐; `\uXXXX`로 이스케이프
- media_monitor.ps1: `net use /delete` 종료 코드 미확인; 언마운트 실패 시 tracking 유지로 다음 sync에서 재시도
- RDP 세션 재사용: `_find_existing_session`이 cmdline에 `winpodx`만 있으면 세션으로 인정 (`winpodx app list` 같은 무관한 프로세스 포함). PID 재사용 시 `process=None`인 가짜 세션 반환. `process.is_freerdp_pid()`로 통합하고 `freerdp`/`xfreerdp`만 허용
- `linux_to_unc`: `$HOME` 밖 경로(`/tmp` 등)에 대해 공유되지 않은 UNC 경로를 조용히 반환 → Windows "경로 없음" 에러. 이제 `ValueError` raise, 호출자가 명확한 에러 메시지로 변환
- 비밀번호 로테이션 state 마커: `cfg.save()`와 Windows 롤백이 모두 실패 시 `.rotation_pending` 마커를 기록. `ensure_ready()`가 매 실행 시 사용자에게 경고하고 `winpodx rotate-password` 수동 실행 안내
- `unregister_mime_types`: `mimeapps.list`에서 winpodx 항목을 포함한 전체 line을 삭제해 다른 앱 연결까지 날리던 버그. `configparser` 파싱으로 해당 항목만 제거하고 atomic write
- 데스크톱 엔트리 및 테마 인덱스: `encoding="utf-8"` 명시 — 한글/일본어 등 non-ASCII `full_name`이 `C`/`POSIX` 로케일에서 설치 실패하던 버그 수정
- GUI 아이콘 탐색: `Path(__file__).parent × 4`는 source layout에서만 동작하고 `pip install` 후 실패. `bundled_data_path()` 헬퍼가 source, wheel share-data, `~/.local/share/winpodx/data/` 순으로 탐색
- 팟 시작 레이스: 컨테이너는 시작됐지만 RDP 포트가 아직 리스닝 안 할 때 첫 앱 실행 실패. `pod.start()`가 반환 전 `backend.wait_for_ready(timeout=cfg.pod.boot_timeout)` 호출
- 하드코드된 컨테이너 이름: 여러 모듈이 `"winpodx-windows"` 리터럴을 사용해 컨테이너를 커스터마이징한 사용자가 winpodx를 못 쓰던 문제. 이제 `cfg.pod.container_name`으로 통일
- `setup` EOF 처리: 파이프로 stdin을 넣으면 `input()`이 `EOFError`로 크래시. 새 `_ask()` 헬퍼가 non-TTY 감지 시 기본값 반환; `handle_rotate_password`는 임시 compose 파일로 3단계 커밋 사용
- 비밀번호 알파벳: PowerShell 작은따옴표 이스케이프에서 일부 조합이 깨지던 `

 제거; 모든 쉘 컨텍스트에서 안전한 `!@#%&*`만 유지
- 앱 설치 재시도: `winpodx app install-all`이 `RuntimeError`에서 조용히 실패하던 버그. `(ProvisionError, RuntimeError)`로 확대하고 일괄 설치 후 아이콘 캐시 갱신
- Wayland DPI 감지: 이전에는 첫 출력만 보던 것을 모든 출력 순회 후 최대 스케일 채택; 컴포지터가 스케일을 노출하지 않으면 Qt `devicePixelRatio()`로 폴백
- 데스크톱 엔트리 아이콘 정리: 래스터 포맷이 `hicolor/scalable/apps/`에 조용히 설치되던 문제 (spec는 SVG만 허용). scalable은 SVG 강제, 래스터는 크기별 디렉토리로 폴백
- 앱 프로필 쓰기: `gui/app_dialog.save_app_profile`이 명시적 `encoding="utf-8"` 사용 — 데스크톱 엔트리에서 고친 non-ASCII 크래시와 동일 문제
- Teams 앱 경로: `data/apps/teams/app.toml`이 구 Classic Teams 실행 파일을 가리키던 것을 `%LOCALAPPDATA%\Microsoft\WindowsApps\ms-teams.exe`로 수정
- Explorer 앱 카테고리: 파일 매니저에 부적절한 `Office` 제거, `FileTools`, `System` 추가
- CI audit 잡: libvirt 헤더 없는 GitHub 러너에서 `pip install -e .[all]`이 실패. 새 `all-no-libvirt` extra로 audit 범위 유지하며 CI 언블록
- 테스트 격리: 새 `tests/conftest.py` autouse fixture가 `HOME`과 `XDG_*`를 tmp 디렉토리로 리다이렉트 — 테스트가 개발자의 실제 설정을 덮어쓰는 문제 방지

### 변경됨
- 기본 RDP 포트 3389 → 3390 (다른 컨테이너와 충돌 방지)
- 기본 VNC 포트 8007 (LinOffice 8006과 충돌 방지)
- FreeRDP 탐색 순서: xfreerdp3 → xfreerdp → sdl-freerdp3 → sdl-freerdp → flatpak
- `wlfreerdp` 탐색 순서에서 제거 (FreeRDP 프로젝트에서 공식 지원 종료)
- 언인스톨 시 항상 컨테이너 제거 (이전: `--purge` 플래그 필요)
- RemoteApp (RAIL) 활성화: `fDisabledAllowList` + `fInheritInitialProgram` 레지스트리 키 — 바탕화면 없이 앱 창만 심리스 표시
- FreeRDP에 `podman unshare --rootless-netns` 래퍼 — rootless Podman RDP 접속에 필수
- 앱별 데스크톱 알림 제거 (매번 실행 시 알림이 과다)

### 제거됨
- **RDPWrap 멀티세션**: 모든 RDPWrap 바이너리, 스크립트, CI 워크플로우, Python 모듈 제거 — 멀티세션 지원은 별도 프로젝트로 개발 예정
- `data/templates/app.desktop.j2` (미사용 Jinja2 템플릿)
- 데드코드: `icons_cache_dir()`, `decode_base64_icon()`, `MISSING_DEPS_MSG`
