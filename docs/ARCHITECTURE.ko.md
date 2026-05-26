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
| i18n | `winpodx.core.i18n` (영어 원문을 key 로, 언어별 flat JSON 카탈로그) |
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

## Guest sync 서브시스템

**코드.** `src/winpodx/core/guest_sync.py`. 설계 노트: [docs/design/GUEST_SYNC_DESIGN.md](design/GUEST_SYNC_DESIGN.md).

호스트의 winpodx 를 업그레이드하면 호스트 바이너리는 갱신되지만, 첫 설치
때 staging 된 게스트 측 아티팩트 (`C:\OEM\agent.ps1`, urlacl 예약,
rdprrap / `shim.exe` / `rcedit.exe`, 헬퍼 스크립트) 는 사용자가 Windows 를
밀고 재설치하기 전까지 stale 상태로 남습니다. Guest sync 가 재설치 없이 이
간극을 메웁니다.

**핵심 enabler.** `/oem` 은 호스트 `config/oem` 의 **live bind mount**
(`compose.py` 의 `{oem_dir}:/oem:Z`) — 즉 호스트 업그레이드 후 동작 중인
컨테이너의 `/oem` 에 *이미* 새 파일이 들어 있음 (이미지 재빌드 없음).
게스트 전달은 `winpodx pod recover-oem` 과 동일 채널 재사용: 컨테이너에서
`/oem` tar → `127.0.0.1:8766` 일회성 HTTP 서버로 serve → 게스트가 QEMU NAT
게이트웨이 `10.0.2.2` 로 pull. sync 중에는 agent 가 살아 있으므로 pull 과
후속 fix 가 noVNC paste 대신 bearer-auth `/exec` 엔드포인트로 동작.

`sync_guest` 는 부분 실패 후 재실행이 안전하도록 순서가 잡혀 있음:

1. **`/oem` 전달** — 게스트 `Invoke-WebRequest` + `tar -xzf` → `C:\OEM`.
   `install.bat` 은 **재실행 안 함** (autologon / 계정 설정 같은 one-shot
   첫 부팅 로직을 담고 있어 live install 에서 재발사하면 안 됨).
2. **urlacl 예약** — install.bat 의 netsh 블록을 `/exec` 로 재적용
   (겹치는 `:8765` 예약 삭제 후 `WD` SID SDDL 로 `http://+:8765/` 재등록).
3. **Idempotent 레지스트리 / runtime fix** — `apply_windows_runtime_fixes(cfg)`
   호출 (apply-fixes 와 동일 체인). 갱신된 바이너리에 대해 rdprrap 도 재활성화.
4. **agent 재시작** — agent 는 자신이 실행되는 `/exec` 를 제공하므로 동기적으로
   스스로를 `Stop-Process` 할 수 없음. **one-shot scheduled task** 가 ~5초 뒤
   발사해 현재 agent 를 멈추고 `C:\OEM\agent.ps1` 을 재기동; `/exec` 호출이 먼저
   반환된 뒤 새 agent 가 교정된 urlacl 로 `:8765` 재바인드.
5. **버전 stamp** — 1–3 단계가 성공한 경우에만
   `C:\winpodx\install-state\guest_version.json` (`{winpodx, oem_bundle}`) 작성.

**Staleness 판정.** 호스트 current = `winpodx.__version__` +
`core.info._bundled_oem_version()`. `guest_sync_needed(cfg)` 가 `/exec` 로 stamp
읽음; stamp 가 존재 **하고** 오래됐으면 sync 발사, stamp 부재면 기록만 (진행 중인
첫 부팅 install 을 방해 안 함). pod readiness 후 `cfg.pod.guest_autosync`
(default `True`) 면 자동 실행, podman/docker 로 gate. 수동:
`winpodx pod sync-guest [--force]` 와 GUI Tools → Sync Guest 액션. `sync_guest`
는 CLI/GUI 가 행을 렌더링할 수 있도록 단계별 결과 맵 반환.

## Disk auto-grow 서브시스템

**코드.** `src/winpodx/core/disk.py` (sizing + 게스트 extend),
`src/winpodx/core/daemon.py` (idle 경로) 에서 트리거.

dockur 는 `cfg.pod.disk_size` 가 증가하고 컨테이너가 재생성될 때만 가상 디스크
*이미지*를 키울 뿐, 게스트의 C: 파티션은 절대 확장 안 하고 **online resize 도
없음**. winpodx 가 양쪽을 처리하는 idle-time auto-grow 를 추가.

**트리거.** pod start / idle 시, C: used% 가
`cfg.pod.disk_autogrow_threshold_pct` (default 80) 를 넘고 **동시에** pod 이 idle 일 때.

**Sizing.** `cfg.pod.disk_autogrow_target_free_pct` 여유 (default 30%) 를 복원할
만큼만 키우되 `cfg.pod.disk_autogrow_increment` 단위 (default `32G`) 로 올림.
ceiling 은 선택적 `cfg.pod.disk_max_size` 와 *호스트가 실제로 back 가능한 양* —
`current + (host_free − reserve)` (reserve 가 auto-grow 의 호스트 디스크 고갈 방지)
중 작은 쪽. 어느 headroom 도 없으면 로그 한 줄과 함께 grow 스킵.

**왜 idle 전용.** dockur 에 online resize 가 없으므로 매 grow 가 **컨테이너를
재생성** (빠른 게스트 reboot). idle 전용 스케줄링이 live RemoteApp 세션을 절대
중단 안 함을 보장.

**게스트 extend.** 이미지가 커지면 새 공간은 디스크 끝에 붙지만 C: 는 원래
위치에서 끝남. extend 는 `/exec` 로 동작: `Resize-Partition -DriveLetter C`.
dockur 의 Windows 레이아웃은 C: **바로 뒤**에 작은 WinRE Recovery 파티션을 두어
extend 를 막으므로 — 단계가 WinRE 를 detach (`reagentc /disable`), 막는 recovery
파티션 삭제, C: extend, 그 다음 WinRE 재활성화 (`reagentc /enable`, 전용 파티션이
없으면 `C:\Windows` 로 폴백).

## UI 국제화 (i18n)

**코드.** `src/winpodx/core/i18n.py`; 카탈로그 `src/winpodx/locale/<lang>.json`.

Linux 측 UI 텍스트 (tray, GUI, CLI) 는 `winpodx.core.i18n.tr(text)` 로 감쌈.
**영어 원문이 카탈로그 key** — `tr()` 이 활성 언어 카탈로그에서 원문을 찾고,
miss 시 동일 영어 원문으로 문자열별 폴백 → 카탈로그가 불완전해도 UI 가 비지 않음.
카탈로그는 flat `{ "<english>": "<translation>" }` JSON. 활성 언어는
`[ui] language` (default `auto` — 호스트 로케일을 `$LC_ALL` / `$LC_MESSAGES` /
`$LANG` 에서 매핑, unknown → 영어) 에서 해소. 7개 언어 제공: en, ko, zh, ja, de,
fr, it. (`pod.language` 와는 별개 — 그건 *Windows 게스트* 설치 언어.)

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
