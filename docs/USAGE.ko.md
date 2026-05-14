# 사용법

[English](USAGE.md) | **한국어**

CLI, GUI, 설정, 헬스 체크. 설치 후 필요한 모든 것.

## 앱 실행

```bash
winpodx app run word              # Word 실행
winpodx app run word ~/doc.docx   # 파일 열기
winpodx app run desktop           # 전체 Windows 데스크톱
```

또는 그냥 애플리케이션 메뉴에서 앱 아이콘 클릭 — winpodx 가 pod 첫 부팅 시 발견된 모든 Windows 앱을 `.desktop` 엔트리로 등록함.

## CLI 레퍼런스

```bash
# 앱
winpodx app list                  # 사용 가능한 앱 목록
winpodx app run word              # Word 실행 (첫 실행 시 자동 프로비저닝)
winpodx app run word ~/doc.docx   # Word 에서 파일 열기
winpodx app run desktop           # 전체 Windows 데스크톱 세션
winpodx app install-all           # 모든 앱을 desktop 메뉴에 등록
winpodx app sessions              # 활성 세션 표시
winpodx app kill word             # 활성 세션 종료
winpodx app refresh               # 게스트 재스캔 후 앱 목록 재구성

# Pod 관리
winpodx pod start --wait          # 시작 후 RDP 준비 대기
winpodx pod stop                  # 중지 (활성 세션 있으면 경고)
winpodx pod status                # 상태 + 세션 수
winpodx pod restart
winpodx pod apply-fixes           # Windows 측 런타임 fix 재적용 (idempotent)
winpodx pod sync-password         # 비밀번호 drift 복구 (cfg ↔ Windows)
winpodx pod multi-session on      # bundled rdprrap 멀티세션 RDP 토글
winpodx pod multi-session status
winpodx pod wait-ready --logs     # Windows 첫 부팅 대기 + 진행 + 컨테이너 로그

# 전원 관리
winpodx power --suspend           # 컨테이너 pause (CPU 해제, 메모리 유지)
winpodx power --resume            # pause 된 컨테이너 resume

# 보안
winpodx rotate-password           # Windows RDP 비밀번호 회전

# Reverse-open (host listener / guest sync)
winpodx host-open status          # listener daemon + manifest 상태
winpodx host-open list            # 발견된 호스트 앱 목록 (live 또는 --cached)
winpodx host-open refresh         # 호스트 재스캔 + 게스트로 manifest push
winpodx host-open enable          # reverse-open 켜기
winpodx host-open disable         # reverse-open 끄기
winpodx host-open add <slug>      # allowlist 에 앱 추가
winpodx host-open remove <slug>   # allowlist 에서 제거 (또는 --deny)
winpodx host-open start-listener
winpodx host-open stop-listener
winpodx host-open daemon-status

# 유지보수
winpodx cleanup                   # Office lock 파일 제거 (~$*.*)
winpodx timesync                  # Windows 시간 강제 동기화
winpodx debloat                   # 텔레메트리, 광고, bloat 비활성화
winpodx uninstall                 # winpodx 파일 제거 (컨테이너 유지)
winpodx uninstall --purge         # config 포함 전부 제거

# 시스템
winpodx setup                     # 대화형 설정 마법사
winpodx info                      # 디스플레이, 의존성, config 진단
winpodx check                     # 모든 헬스 프로브 실행 (pod / RDP / agent / disk / …)
winpodx check --json              # 같은 프로브, machine-readable JSON
winpodx gui                       # Qt6 메인 윈도 실행 (Apps / Settings / Tools / Terminal)
winpodx tray                      # Qt 시스템 트레이 아이콘 실행
winpodx config show               # 현재 config 표시
winpodx config set rdp.scale 140  # config 값 변경
winpodx config import             # 기존 winapps.conf import
```

## GUI

`winpodx gui` 로 실행. Qt6 메인 윈도는 5개 페이지:

| 페이지 | 동작 |
|------|------|
| **Apps** | 설치된 앱 프로필의 grid / list view, 검색 + 카테고리 필터, 앱별 실행 (3초 cooldown), Add / Edit / Delete 앱 프로필 다이얼로그 |
| **Settings** | RDP (user / IP / port / scale / DPI / 비밀번호 회전), Container (backend / CPU / RAM / idle timeout), 그리고 reverse-open 패널 (enable 토글, allowlist + denylist, 라이브 daemon 상태, refresh / start / stop 버튼) 한 화면에 |
| **Tools** | Suspend / Resume / Full Desktop 버튼, Clean Locks / Sync Time / Debloat, 그리고 원클릭 Windows Update **활성/비활성** 토글 |
| **Terminal** | 명령 allowlist 제한된 embedded 셸 (`podman`, `docker`, `virsh`, `winpodx`, `xfreerdp`, `systemctl`, `journalctl`, `ss`, `ip`, `ping`, ...) + 퀵 버튼 (Status / Logs / Inspect / RDP Test / Clear) |
| **Info** | 라이브 **Health** 카드 (pod / RDP / agent / OEM / disk / 비밀번호 age / 앱 수) + System / Display / Dependencies / Pod / Config 스냅샷 |

시스템 트레이 (`winpodx tray`) 는 가벼운 대안 — pod 컨트롤, 앱 런처 서브메뉴 (상위 20 + Full Desktop), 유지보수 서브메뉴 (Clean Locks / Sync Time / Suspend), 선택적 idle-monitor 스레드.

## 헬스 체크

`winpodx check` 가 GUI Health 카드가 쓰는 모든 프로브를 실행하고 각각 한 줄 결과 출력:

```
=== winpodx check ===

  [OK  ] pod_running        running (ip=127.0.0.1)  (58ms)
  [OK  ] rdp_port           127.0.0.1:3390 reachable  (0ms)
  [OK  ] agent_health       version=0.2.2-rev4  (63ms)
  [OK  ] agent_auth_ready   bearer token available  (1ms)
  [OK  ] oem_version        bundle=24  (3ms)
  [OK  ] password_age       7d remaining (max_age=7d)  (0ms)
  [OK  ] apps_discovered    41 app(s) in /home/.../discovered  (3ms)
  [OK  ] disk_free          401.0/3725 GiB free  (0ms)

Overall: OK
```

상태 범례: `OK` (녹색) / `WARN` (노랑 — 정보성, exit 0) / `FAIL` (빨강 — exit 1) / `SKIP` (회색 — config 로 비활성). machine-readable output 은 `--json`.

## 설정

설정 파일: `~/.config/winpodx/winpodx.toml` (자동 생성, `0600` 권한)

```toml
[rdp]
user = "User"
password = ""                # 자동 생성 랜덤 비밀번호
password_updated = ""        # ISO 8601 timestamp
password_max_age = 7         # 자동 회전까지 일수 (0 = 비활성)
ip = "127.0.0.1"
port = 3390
scale = 100                  # DE 에서 자동 감지
dpi = 0                      # Windows DPI % (0 = 자동)
extra_flags = ""             # 추가 FreeRDP 플래그 (allowlist)

[pod]
backend = "podman"
win_version = "11"                               # 11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016 — 커스텀 ISO 는 ARCHITECTURE.md 참고
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = true                                # 앱 실행 시 pod 자동 시작
idle_timeout = 0                                 # 자동 suspend 까지 초 (0 = 비활성)
boot_timeout = 300                               # 첫 부팅 unattended 설치 대기 초
image = "docker.io/dockurr/windows:latest"       # 컨테이너 이미지 (에어갭 미러용 override)
disk_size = "64G"                                # dockur 에 전달하는 가상 디스크 크기

[reverse_open]
enabled = true                                   # v0.5.0 부터 기본 활성
allow = []                                       # 비어있으면 발견된 모든 앱
deny = []                                        # manifest 에서 제외할 앱
```

`winpodx config set <key> <value>` 또는 에디터로 직접 수정 — TOML 은 3.11+ 에서 stdlib (`tomli` on 3.9/3.10) 로 파싱.
