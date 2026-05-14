# 아키텍처

[English](ARCHITECTURE.md) | **한국어**

winpodx 가 어떻게 조립되어 있는지: 앱 실행 시 데이터 흐름, 기술 스택, 소스 트리 레이아웃.

## 동작 방식

```
                     ┌─────────────────────────────┐
  앱 메뉴에서         │     Linux Desktop (KDE,     │
  "Word" 클릭   ───>  │     GNOME, Sway, ...)       │
                     └──────────────┬──────────────┘
                                    │
                     ┌──────────────▼──────────────┐
                     │         winpodx             │
                     │  ┌─────────────────────┐    │
                     │  │ 자동 프로비저닝:    │    │
                     │  │  config → password  │    │
                     │  │  → container → RDP  │    │
                     │  │  → desktop entries  │    │
                     │  └─────────────────────┘    │
                     └──────────────┬──────────────┘
                                    │ FreeRDP RemoteApp
                     ┌──────────────▼──────────────┐
                     │   Windows Container (Podman)│
                     │   ┌──────────────────────┐  │
                     │   │  Word  Excel  PPT ...│  │
                     │   │ multi-session/rdprrap│  │
                     │   └──────────────────────┘  │
                     │   127.0.0.1:3390 (TLS)      │
                     └─────────────────────────────┘
```

Pod 의 명령 채널은 게스트 안에서 `127.0.0.1:8765` 에 listen 하는 bearer-auth HTTP agent (loopback 전용). RDP 자체는 `127.0.0.1:3390` 에서 TLS 암호화로 동작. Reverse-open (Linux 앱이 Windows "Open with..." 메뉴에 노출되는 기능) 은 별도의 호스트 측 listener daemon 이 `\\tsclient\home` 공유를 통해 요청을 받음.

## 기술 스택

| 레이어 | 기술 |
|-------|------|
| 언어 | Python 3.9+ (3.11+ 는 stdlib 만; 3.9/3.10 은 `tomli` 폴백) |
| CLI | argparse (stdlib) |
| GUI (선택) | PySide6 (Qt6) |
| 설정 | TOML (3.11+ 는 stdlib `tomllib` / 3.9/3.10 은 `tomli`; 자체 writer) |
| RDP | FreeRDP 3+ (xfreerdp, RemoteApp/RAIL) |
| Guest agent | PowerShell `HttpListener` on `127.0.0.1:8765` (bearer auth, base64 인코딩 `/exec` payload) |
| 컨테이너 | Podman / Docker ([dockur/windows](https://github.com/dockur/windows)) |
| VM | libvirt / KVM |
| Reverse-open shim | Rust (`windows_subsystem = "windows"`, vendored rcedit 로 슬러그별 아이콘 embed) |
| CI | GitHub Actions (lint + test on 3.9-3.13 + pip-audit) |

## 프로젝트 구조

```
winpodx/
├── install.sh             # 원라인 인스톨러 (pip 없음)
├── uninstall.sh           # 깔끔한 언인스톨러
├── src/winpodx/
│   ├── cli/               # argparse 명령 (app, pod, config, setup, host-open, ...)
│   ├── core/              # Config, RDP, pod lifecycle, provisioner, daemon
│   ├── backend/           # Podman, Docker, libvirt, manual
│   ├── desktop/           # .desktop 엔트리, 아이콘, MIME, tray, 알림
│   ├── display/           # X11/Wayland 감지, DPI 스케일링
│   ├── gui/               # Qt6 메인 윈도, 앱 다이얼로그, 테마, reverse-open Settings 카드
│   ├── reverse_open/      # Discovery, ICO 변환, listener daemon, sync transport
│   └── utils/             # XDG 경로, 의존성, TOML writer, winapps 호환
├── data/                  # winpodx GUI desktop 엔트리 + 아이콘 + 설정 예시
├── config/oem/
│   ├── install.bat        # Windows OEM 첫 부팅 오케스트레이션
│   └── reverse-open/      # register-apps.ps1, unregister-apps.ps1, Rust shim, rcedit
├── scripts/windows/       # PowerShell 스크립트 (debloat, time sync, USB mapping, 앱 discovery)
├── packaging/             # OBS / AUR / RHEL spec + 메인테이너 문서
├── debian/                # Debian 소스 패키지 레이아웃
├── docs/                  # 사용자 문서 (영어 + 한국어 mirror)
├── .github/workflows/     # CI: lint + test + publish (OBS / RHEL / deb / AUR)
└── tests/                 # pytest 테스트 스위트
```

## 주요 데이터 흐름

- **앱 실행.** CLI → `provisioner.ensure_ready()` (config + 비밀번호 회전 + compose + resume + pod + bundled apps + desktop 엔트리) → FreeRDP 세션 → `.cproc` 추적 + reaper 스레드 + desktop 알림.
- **앱 설치 (Linux 측).** AppInfo (TOML) → `.desktop` 파일 생성 → 아이콘 설치 → MIME 등록 → 아이콘 캐시 refresh.
- **파일 열기 (host → guest).** Linux 경로 → UNC 경로 변환 (`\\tsclient\home\...`) → RDP `/app-cmd`.
- **자동 suspend.** `daemon.run_idle_monitor()` → N 초 동안 세션 없으면 `podman pause` → lock 파일 정리.
- **자동 resume.** `provisioner` → `daemon.ensure_pod_awake()` → `podman unpause` → RDP 대기.
- **비밀번호 회전.** `ensure_ready()` → `password_max_age` 확인 → 새 비밀번호 생성 → config + compose 저장 → 컨테이너 재생성 → 실패 시 rollback.
- **Reverse-open (guest → host).** Windows Explorer "Open with..." → 슬러그별 `winpodx-<slug>.exe` shim → `\\tsclient\home\.local\share\winpodx\reverse-open\incoming\<uuid>.json` 에 atomic JSON 쓰기 → host listener 가 픽업 → `safe_open_unc` TOCTOU-safe 경로 해소 → 호스트에서 `xdg-open` 호출.

## 고급: 커스텀 Windows ISO

winpodx 는 dockur 가 큐레이트한 Windows 에디션 (Win10 / 11, LTSC, IoT
LTSC, Tiny, Server 2016+) 을 기본 지원합니다. 목록은
`src/winpodx/core/config.py` 의 `_KNOWN_WIN_VERSIONS` 에 있고 GUI
설정 → Container/VM 카드 드롭다운으로 노출됩니다.

dockur 가 큐레이트하지 **않는** Windows ISO (직접 만든 프로그램 미리
깐 이미지, 특정 debloat 프리셋이 들어간 Enterprise 에디션, dockur 가
태그하지 않은 로컬라이즈 빌드) 를 부팅하고 싶다면 수동으로 통과시킬
수 있습니다. **이 경로는 비공식 / 비지원** — winpodx 의 OEM 스크립트
(`install.bat`, `agent.ps1`, `rdprrap`) 는 dockur 큐레이트 Win10+
패밀리에 맞춰 작성되어 있습니다. 커스텀 ISO 는 부팅은 될 수 있어도
에이전트 / 멀티세션 활성화 / RemoteApp 디스커버리가 깨질 수 있습니다.
커스텀 ISO 관련 버그 리포트는 사용자가 직접 디버깅해야 합니다.

위 면책 조항에 동의한다면:

1. ISO 파일을 읽기 가능한 위치에 둡니다 (예: `~/winpodx-custom.iso`).
2. `winpodx.toml` 에 `win_version = "custom"` 설정:

   ```toml
   [pod]
   win_version = "custom"
   ```

   winpodx 는 "known list 에 없다" WARNING 한 줄을 로그에 남기고
   값을 dockur 에 그대로 통과시킵니다.

3. 생성된 `~/.config/winpodx/compose.yaml` 을 편집해서 dockur 가
   기대하는 경로에 ISO 마운트:

   ```yaml
   services:
     windows:
       volumes:
         - ~/winpodx-custom.iso:/storage/custom.iso
         # ...기존 volumes 는 그대로
   ```

4. 컨테이너 재생성:

   ```bash
   winpodx pod stop
   podman compose -f ~/.config/winpodx/compose.yaml up -d
   ```

compose 템플릿은 일부 코드 경로에서 (`winpodx setup`, `winpodx pod
start`, GUI 의 Save 버튼이 cpu / ram / port / user 변경을 감지했을 때
등) `winpodx setup` 이 재생성합니다 — 그 시점에 수동 편집이 덮어
써집니다. 재생성 후 다시 적용해야 합니다.

이 패턴을 자주 사용하게 되고 upstream dockur 가 해당 에디션을 추가하지
않는다면 feature request 를 올려주세요: `cfg.pod.custom_iso_path`
필드 추가는 검토 대상이지만 아직 정식 출시되지 않았습니다.
