# winpodx 라이프사이클 & 프로세스

winpodx pod 의 설치 / 업그레이드 / 마이그레이션 / 헬스 유지 전 과정을 코드 경로 단위로 설명. 각 섹션은 한 단계씩 — 누가 발사하나, 무엇을 하나, 코드가 어디 있나, 어떤 실패 모드를 다루나.

> **대상.** winpodx 의 어떤 코드 경로든 이해하거나 디버그해야 하는 메인테이너 / 고급 사용자. 일상 사용은 [README.ko.md](README.ko.md) 참고.

---

## 목차

1. [전체 단계 개요](#1-전체-단계-개요)
2. [신규 설치](#2-신규-설치-기존-config-없음)
3. [Sysprep 첫 부팅 (install.bat)](#3-sysprep-첫-부팅-installbat)
4. [업그레이드 설치 (기존 config 있음)](#4-업그레이드-설치-기존-config-있음)
5. [마이그레이션 (`winpodx migrate`)](#5-마이그레이션-winpodx-migrate)
6. [Apply 체인 (`apply_windows_runtime_fixes`)](#6-apply-체인-apply_windows_runtime_fixes)
7. [Multi-session 활성화](#7-multi-session-활성화)
8. [컨테이너 이미지 pinning](#8-컨테이너-이미지-pinning)
9. [Discovery (`winpodx app refresh`)](#9-discovery-winpodx-app-refresh)
10. [Transport 선택](#10-transport-선택-agent-vs-freerdp)
11. [복구 시나리오](#11-복구-시나리오)

---

## 1. 전체 단계 개요

```
                 ┌─────────────────┐
                 │  install.sh     │
                 │  (호스트 측)     │
                 └────────┬────────┘
                          │
        ┌─────────────────┴──────────────────┐
        │                                    │
   기존 config 없음                기존 config 있음
        │                                    │
        ▼                                    ▼
┌───────────────┐               ┌─────────────────────┐
│ winpodx setup │               │ setup 스킵           │
│  (대화형 또는   │               │ agent token staging │
│   default)    │               └──────────┬──────────┘
│ 작성:         │                          │
│  winpodx.toml │                          │
│  compose.yaml │                          │
│  agent_token  │                          │
└───────┬───────┘                          │
        │                                  │
        └──────────────┬───────────────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ winpodx pod         │
            │   wait-ready        │ (3 단계: 컨테이너,
            │                     │  RDP 포트, FreeRDP probe)
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ winpodx migrate     │ (기존 config 있을 때만)
            │  - 버전 비교         │
            │  - image pin 정렬    │
            │  - apply 체인        │
            └──────────┬──────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ winpodx app refresh │
            │  (3 계층 race-free  │
            │   discovery)        │
            └─────────────────────┘
```

상단 박스 3개가 진입점, 나머지는 구현. 아래에 각각 상세 설명.

---

## 2. 신규 설치 (기존 config 없음)

**트리거.** `~/.config/winpodx/winpodx.toml` 부재. 보통 처음 사용자가 `curl -sSL .../install.sh | bash` 돌릴 때.

**흐름.**

1. `install.sh` 가 distro 확인, 누락 의존성 설치 (podman, podman-compose, freerdp, libnotify), Python ≥ 3.9 검증.
2. `install.sh` 가 winpodx 소스를 `~/.local/bin/winpodx-app/` 에 추출 + `winpodx` 런처를 `~/.local/bin/winpodx` 에 작성.
3. `install.sh` 가 `python3 -m winpodx setup --non-interactive` 호출 (`src/winpodx/cli/setup_cmd.py::handle_setup`).
4. setup 이 `~/.config/winpodx/winpodx.toml` 작성:
   - `cfg.pod.image` default = `DOCKUR_IMAGE_PIN` (SHA-pinned `docker.io/dockurr/windows@sha256:…` digest — [§8](#8-컨테이너-이미지-pinning) 참고).
   - `cfg.rdp.password` 무작위 생성.
   - `cfg.pod.backend` 자동 감지 (podman > docker > libvirt).
5. setup 이 `generate_compose(cfg)` 실행 → `~/.config/winpodx/compose.yaml` 작성.
6. setup 이 `_ensure_oem_token_staged()` 실행 → `~/.config/winpodx/agent_token.txt` 작성 + OEM bind-mount source 디렉터리에 복사.
7. `install.sh` 가 `winpodx pod wait-ready --timeout 3600 --logs` 호출. dockur 가 pinned image 받고, Windows ISO (~7.5GB) 다운로드, 우리 OEM bundle 로 Sysprep ([§3](#3-sysprep-첫-부팅-installbat)) — 보통 5-10분.
8. migrate **스킵** (`installed_version.txt` 비교 대상 없음).
9. `install.sh` 가 `winpodx app refresh` 호출 ([§9](#9-discovery-winpodx-app-refresh)).

**최종 상태.** 완전 프로비저닝된 pod, multi-session 활성, agent 가 wscript wrapper 로 동작, 앱 메뉴 채워짐.

---

## 3. Sysprep 첫 부팅 (`install.bat`)

**위치.** `config/oem/install.bat`. OEM bind-mount 안에 있음 → dockur 가 첫 부팅 시 `C:\OEM\` 으로 복사 → `unattend.xml` 의 `FirstLogonCommands` 가 자동로그온 사용자의 로컬 콘솔 세션에서 1회 호출.

`WINPODX_OEM_VERSION` (파일 상단) 이 번들 버전. install.bat 또는 형제 리소스 변경 시 릴리스마다 bump.

**하는 일, 순서대로.**

1. **TermService 복구 동작** — `sc.exe failure TermService reset= 86400 actions= restart/5000/...`. transient TermService crash 자동 복구.
2. **MaxInstanceCount + multi-session 레지스트리** — `HKLM:\...\Terminal Server\WinStations\RDP-Tcp\MaxInstanceCount`, `fSingleSessionPerUser = 0`. OEM 시점의 권위 있는 cap; runtime apply 체인이 `cfg.pod.max_sessions` 변경 시 동기화.
3. **rdprrap install** (multi-session 활성화):
   - SHA256 검증된 번들을 `C:\OEM\rdprrap-*.zip` → `C:\winpodx\rdprrap\` 추출.
   - install + verify + marker 는 **`rdprrap-activate.ps1`** 에 위임 (OEM-time + runtime 활성화의 single source of truth; [§7](#7-multi-session-활성화) 참고).
4. **NIC / RDP 타임아웃 설정** — idle / disconnect / connection 타임아웃 비활성화해서 RemoteApp 세션이 1시간 후 끊기지 않도록.
5. **media_monitor.ps1 staging + autostart** — `media_monitor.ps1` 을 `C:\winpodx\` 에 복사하고 `HKCU\Run\WinpodxMedia` 를 **wscript+hidden-launcher.vbs wrapper** 로 등록 (OEM v19 부터 — 이전 버전은 bare `powershell.exe -WindowStyle Hidden` 이라 ~50ms conhost flash 새어나옴).
6. **VBS 런처 staging** — `hidden-launcher.vbs`, `launch_uwp.vbs`, `launch_uwp.ps1`, `agent-respawn.ps1`, `rdprrap-activate.ps1` 을 `C:\Users\Public\winpodx\launchers\` 에 복사. Public 디렉터리는 누구나 쓰기 가능 → User-level agent 가 나중에 runtime 마이그레이션 때 덮어쓸 수 있음.
7. **Agent autostart** — `HKCU\Run\WinpodxAgent` 를 wscript+hidden-launcher.vbs wrapper 가 `C:\OEM\agent.ps1` 가리키게 등록.
8. **URL ACL 사전 등록** — agent 의 `http://+:8765/` 리스너용 `netsh http add urlacl`. User-level agent 가 admin 권한 없이 bind 가능.
9. **OEM 마커** — `C:\winpodx\oem_version.txt` 작성 → 호스트가 어떤 번들로 프로비저닝됐는지 probe 가능.

**Idempotency.** install.bat 은 **1회** 만 실행. Sysprep 끝나면 dockur 가 절대 재실행 안 함. 이후 모든 유지보수는 호스트의 apply 체인이 처리 ([§6](#6-apply-체인-apply_windows_runtime_fixes)).

---

## 4. 업그레이드 설치 (기존 config 있음)

**트리거.** `~/.config/winpodx/winpodx.toml` 이미 존재. 보통 `curl -sSL .../install.sh | bash -s -- --main` 로 기존 winpodx 업데이트.

**흐름.**

1. `install.sh` 가 소스를 `~/.local/bin/winpodx-app/` 재추출 (호스트 코드 업데이트).
2. `winpodx setup --non-interactive` 실행 — 기존 config 감지, `Existing config found ..., skipping setup` 출력, `_ensure_oem_token_staged()` 실행, return. **여기서 compose 재생성 안 함**, 즉 동작 중인 pod 은 소스 코드 업데이트로 흔들리지 않음.
3. `winpodx pod wait-ready` 실행 — 컨테이너가 이미 떠 있고 따뜻하니 보통 몇 초 안에 완료.
4. `winpodx migrate` 실행 ([§5](#5-마이그레이션-winpodx-migrate)) — 기존 pod 마이그레이션의 canonical 위치.
5. `winpodx app refresh` 실행 → 발견된 앱 메뉴 갱신.

게스트를 변경하는 건 migrate 만. install.sh 자체는 step 2 이후 순수 호스트 측.

---

## 5. 마이그레이션 (`winpodx migrate`)

**코드.** `src/winpodx/cli/migrate.py::run_migrate`.

**목표.** 기존 pod 을 *main fresh install 이 만들 상태*로 정렬. 3개 독립 단계가 순서대로 실행:

```
        installed_version vs current
                    │
       ┌────────────┼─────────────┐
       │            │             │
     None        already       cross-version
   (fresh)      current      (예: 0.1.7 → 0.3.1)
       │            │             │
       ▼            ▼             ▼
   버전 기록      apply 체인     whats-new 출력
                  실행          + apply 체인
                                + (선택) refresh
```

### 5.1 버전 감지

`_detect_installed_version()` 이 `~/.config/winpodx/installed_version.txt` 읽음. 부재 + config 존재 = pre-tracker (v0.1.7) baseline 가정.

`_version_tuple()` 이 dot-segment 별 leading digit 추출 (`0.3.0-RTM1`, `0.3.0rc1`, `0.3.0+dev` 모두 `[:3]` 비교용으로 `(0, 3, 0)` 파싱). PR #82 이전엔 첫 non-int segment 에서 멈춰서 RTM-suffix 가 `(0, 3)` 반환 → 모든 shipped `(0, 3, 0)` 보다 작게 lex-비교 → RTM 사용자 모두에게 apply 체인 빠짐.

### 5.2 항상 실행되는 단계

기존 config 있으면 버전 무관 실행:

1. **`_probe_password_sync`** — 사전 FreeRDP auth probe. password 가 drift 됐으면 (cfg vs Windows 계정 불일치) `winpodx pod sync-password` 안내 진단 출력.
2. **`_ensure_canonical_image_pin`** — 일치 안 하면 `cfg.pod.image` 와 `compose.yaml` 을 `DOCKUR_IMAGE_PIN` 으로 재작성. 다음 `pod start` 시 컨테이너 1회 recreate (volume 보존 — ~30초, ISO 재다운로드 없음). Idempotent: pin 일치 → no-op.
3. **`_apply_runtime_fixes_to_existing_guest`** — `apply_windows_runtime_fixes(cfg)` 호출 ([§6](#6-apply-체인-apply_windows_runtime_fixes)).

### 5.3 cross-version 업그레이드에서만 실행

- `_print_whats_new` — `_VERSION_NOTES` 에서 `(installed, current]` 사이 모든 버전 release notes 출력.
- `_maybe_cleanup_legacy_bundled` — v0.1.9 경계 넘을 때만, 번들 프로필 시대의 stale `.desktop` 14개 제거 제안.

### 5.4 왜 "already current" 도 apply 체인 실행하나

Patch 버전 (0.1.9.x) 들은 `[:3]` 자르면 같은 `(0, 1, 9)` 튜플로 collapse 됨. 이 경로에서 apply 안 발사하면 `0.1.9.0 → 0.1.9.2` 업그레이드가 `0.1.9.x` 의 모든 수정을 silently 스킵. helper 들 idempotent 라 healthy pod 에 재실행해도 helper 당 marker probe + no-op return.

---

## 6. Apply 체인 (`apply_windows_runtime_fixes`)

**코드.** `src/winpodx/core/provisioner.py::apply_windows_runtime_fixes`. `winpodx pod apply-fixes`, GUI Tools 페이지 버튼, migrate 가 호출.

**순서 중요** — 각 단계는 이전 단계 실행 가정:

```
1. max_sessions          MaxInstanceCount 레지스트리 sync
2. rdp_timeouts          idle / disconnect / connection 타임아웃 비활성화
3. oem_runtime_fixes     NIC 절전 off, TermService 복구, …
4. vbs_launchers         VBS 파일 push + agent-respawn + WinpodxMedia 재작성
5. multi_session         marker probe + (필요 시) detached activation
```

**Helper 계약.** 각 helper 가 단일 PowerShell payload 만들어서 `_apply_via_transport` 로 전송 (agent /exec 우선, FreeRDP RemoteApp 폴백) 후 return. 모든 helper idempotent — 이미 적용된 pod 에 실행하면 marker probe + no-op return.

**Helper 별 상세.**

### 6.1 `_apply_max_sessions`

`HKLM:\...\WinStations\RDP-Tcp` 에 `MaxInstanceCount` 작성, Terminal Server root 의 `fSingleSessionPerUser` 클리어. TermService restart **안 함** — apply 자체가 그 서비스가 제공하는 RDP 세션 *안에서* 실행 중이라, 재시작 = apply mid-flight 사망. 레지스트리 쓰기만 충분; 새 값은 다음 자연 주기에 적용.

### 6.2 `_apply_rdp_timeouts`

RDP idle / disconnect / max-session 타임아웃 비활성화 + keep-alive 활성화 레지스트리 키 작성. 없으면 Windows 가 1시간 default idle 후 RemoteApp 세션 drop, NAT/firewall idle-cleanup 이 underlying TCP 죽일 수 있음.

### 6.3 `_apply_oem_runtime_fixes`

OEM 시점에 *유지돼야* 하지만 가끔 안 그런 설정들의 catch-all:

- NIC 절전 off (`Set-NetAdapterPowerManagement`)
- TermService 복구 동작 (5초 재시작, 3회 시도)
- ApplicationFrameHost / explorer.exe 안정성 tweak

### 6.4 `_apply_vbs_launchers`

5개 파일을 단일 `/exec` round-trip 으로 push:

| 파일 | 용도 |
|---|---|
| `hidden-launcher.vbs` | 일반 GUI-subsystem wrapper, 자식한테 SW_HIDE 전파 |
| `launch_uwp.vbs` | RemoteApp-friendly UWP 런처, launch_uwp.ps1 hidden 호출 |
| `launch_uwp.ps1` | C# helper 클래스 IApplicationActivationManager activator (PS-level COM cast 이슈 없음) |
| `agent-respawn.ps1` | Detached agent 재시작 (옛 거 죽이고 wscript wrapper 로 새거 spawn) |
| `rdprrap-activate.ps1` | Runtime rdprrap activator ([§7](#7-multi-session-활성화) 참고) |

그 다음 `HKCU\Run\WinpodxAgent` 와 `HKCU\Run\WinpodxMedia` 를 wscript+hidden-launcher.vbs wrapper 사용하도록 작성. `WinpodxMedia` 재작성은 legacy 항목 존재 조건부 (install.bat 이 media_monitor staging 스킵한 pod 에 stale 항목 만들지 않도록).

마지막으로 `agent-respawn.ps1` detached spawn → 새 wrapper 즉시 적용 (사용자 로그아웃 불필요). `/health` 가 3-4초 blip 후 새 wrapper 로 복구.

### 6.5 `_apply_multi_session`

[§7](#7-multi-session-활성화) 참고.

---

## 7. Multi-session 활성화

**목표.** rdprrap 이 `termsrv.dll` 을 patch 해서 같은 사용자의 여러 RDP 세션이 공존 가능. 없으면 새 RDP 연결마다 이전 세션 대체 — 무서운 "Select a session to reconnect to" 다이얼로그.

**Single source of truth.** `config/oem/rdprrap-activate.ps1` 이 OEM-time (synchronous, install.bat 에서) 과 runtime (detached, `_apply_multi_session` 또는 `winpodx pod multi-session on` 에서) 둘 다에서 호출됨.

### 7.1 활성화 메커니즘

2단계:

1. `rdprrap-installer install --skip-restart` — `HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll` 을 `termwrap.dll` (rdprrap 의 wrapper DLL) 가리키도록 patch.
2. `net stop TermService /y && net start TermService` — TermService 가 fresh start 시 새 DLL 로드.

step 2 가 모든 활성 RDP 세션 죽임 (TermService 가 그것들 관리하니까).

### 7.2 OEM-time 경로 (synchronous)

install.bat 이 `FirstLogonCommands` 의 **로컬 콘솔 세션**에서 실행. TermService 는 **RDP 세션만** 관리하므로, step 2 의 cycle 이 cmd.exe 부모 안 죽임. install.bat 이 `-Detached` 없이 `rdprrap-activate.ps1` 호출, 스크립트 종료까지 동기 대기, rc 로 분기.

### 7.3 Runtime 경로 (detached)

agent 는 user RDP 세션 *안에서* 실행 — step 2 에서 죽는 그 세션. inline `/exec` 면 응답 반환 전에 죽음.

`_apply_multi_session` 과 `winpodx pod multi-session on` 이 따라서 wscript+hidden-launcher.vbs 로 `rdprrap-activate.ps1 -Detached` spawn:

```
host /exec  ──► agent.ps1  ──► Start-Process wscript.exe ...rdprrap-activate.ps1 -Detached
                    │                   │
              host 에 OK 반환       (2초 sleep — host 응답 시간)
                                        ↓
                                 install + TermService cycle
                                        ↓
                                 marker := 'enabled' / 'installer-failed' / 등
                                        ↓
                                 (agent 세션은 ~중간에 죽음)
```

사용자 재접속 → HKCU\Run 발사 → wscript wrapper 로 새 agent.

### 7.4 Idempotency: marker + ServiceDll 교차확인

`_apply_multi_session` 이 `C:\winpodx\rdprrap\.activation_status` 읽음 (`rdprrap-activate.ps1` 이 작성):

| Marker 값 | 동작 |
|---|---|
| `enabled` | No-op return. Fast path. |
| 부재 / `not-activated` / `installer-failed` / `extract-failed` | ServiceDll 교차확인. 이미 `termwrap.dll` 면: marker 에 `enabled` 작성, no-op return (PR #85 — install.bat 이 failed 로 마킹했지만 patch 는 어쨌든 land 한 케이스 처리). 아니면: detached activator spawn. |

이 belt-and-suspenders 가 *이미* 동작 중인데 OEM-time 부분 실패한 pod 에서 TermService cycle 발사 방지. PR #85 이전엔 그런 pod 의 매 apply-fixes 호출이 agent 죽임.

### 7.5 `winpodx pod multi-session on/off/status`

- **`on`** — apply 체인 단계와 동일 코드 경로. Detached spawn, "OK: activation queued" 반환 + ~10초 disconnect 비용 명시.
- **`off`** — inline `rdprrap-conf --disable`. Disable 은 레지스트리 patch clear 만; TermService 는 다음 reboot 까지 cycle 불필요, agent 세션 안전.
- **`status`** — marker probe. apply-fixes multi_session 단계가 쓰는 동일 source, 표면 일관성.

---

## 8. 컨테이너 이미지 pinning

**코드.** `src/winpodx/core/config.py` 의 `DOCKUR_IMAGE_PIN` 상수.

**왜.** Pin 이전 (≤ v0.3.0), `cfg.pod.image` default 가 `:latest`. 매 `podman-compose up` 마다 dockur 가 푸시한 무엇이든에 대해 tag 재해상. digest 가 바뀌면 (자주 — dockur 릴리스 주기가 daily-ish), podman-compose 가 spec 다르다고 판단해서 **컨테이너 재생성** → fresh ISO 다운로드 → 분 단위 Sysprep → 게스트 상태 손실.

**포맷.** `docker.io/dockurr/windows@sha256:<64-char-hex>`.

**갱신 절차 (릴리스 시점).**

```
TOKEN=$(curl -sSL "https://auth.docker.io/token?service=registry.docker.io&scope=repository:dockurr/windows:pull" | jq -r .token)
curl -sSL -H "Authorization: Bearer $TOKEN" -I \
  -H 'Accept: application/vnd.docker.distribution.manifest.list.v2+json' \
  -H 'Accept: application/vnd.oci.image.index.v1+json' \
  "https://registry-1.docker.io/v2/dockurr/windows/manifests/latest" \
  | grep -i '^docker-content-digest:'
```

digest 를 `DOCKUR_IMAGE_PIN` 에 붙여넣고, 버전 bump, ship.

**마이그레이션.** Migrate 의 `_ensure_canonical_image_pin` 이 기존 pod 의 `cfg.pod.image` + `compose.yaml` 재작성. 다음 `pod start` 시 컨테이너 1회 recreate (volume 보존 — ~30초, ISO 재다운로드 없음, Sysprep 없음). Idempotent.

**사용자 opt-in 갱신.** `winpodx setup --update-image` 가 fresh `:latest` 를 pull 하는 **유일한** 경로:

1. `podman pull docker.io/dockurr/windows:latest`
2. `podman image inspect ... -f '{{json .RepoDigests}}'` → digest 해결
3. docker.io 항목으로 필터 → `cfg.pod.image := <digest>`
4. `compose.yaml` 재생성
5. "다음 pod start 시 컨테이너 recreate ~30초, volume 보존" 출력

---

## 9. Discovery (`winpodx app refresh`)

**코드.** `src/winpodx/core/discovery/__init__.py::discover_apps` (host) + `scripts/windows/discover_apps.ps1` (guest).

**기본 timeout.** 180초.

**3 race 회피 계층.**

### 9.1 Layer 1: 게스트 readiness gate

`discover_apps.ps1` 머리:

```
1초 간격 polling:
  AppXSvc.Status -eq 'Running'  AND
  ProgramData Start Menu .lnk 개수 > 0

3 연속 안정 sample 필요
60초 budget
```

Sysprep-방금-끝난 윈도우 (AppX 가 아직 inbox 앱 설치 중, Start Menu indexer 가 mid-propagation) 잡음.

### 9.2 Layer 2: 호스트 transport readiness

`_wait_for_transport_ready(cfg, max_wait_sec=30)` 이 agent `/health` 와 RDP port polling. 둘 중 하나 응답하면 즉시 return. migrate-방금-cycle-한-TermService 윈도우 (agent mid-respawn) 잡음.

### 9.3 Layer 3: retry-on-empty

첫 pass 후, `_looks_suspiciously_empty(apps)`:

- 총 개수 < 5 (stock Win11 항상 15+ 개)
- OR UWP 개수 == 0 (Calculator / Settings / Terminal 항상 있음)

Suspicious 면: 8초 대기, 1회 재시도. 큰 결과 선택 → retry 가 절대 regression 안 됨.

### 9.4 Discovery 소스

스크립트가 5개 소스 union, lowercase exec path 또는 UWP AUMID 로 dedupe:

1. **Registry App Paths** (`HKLM` + `HKCU`)
2. **Start Menu .lnk 재귀** (ProgramData + 모든 user profile)
3. **UWP / MSIX 패키지** via `Get-AppxPackage` + `AppxManifest.xml`
4. **Chocolatey + Scoop shim**
5. **Essentials allowlist** — File Explorer / Calculator / Settings 는 항상 emit (synthesized stub 으로) — `.lnk` 로 enum 안 되니까.

Junk 필터: uninstaller, redistributable, `LicenseManagerShellExt`, `WindowsPackageManagerServer` 등 숨김. 사용자 override (`hidden = true` in `app.toml`) 는 후속 refresh 에서도 보존.

### 9.5 출력

`~/.local/share/winpodx/discovered/` 에 persist. 각 앱이 `.toml` + `.desktop` 항목 → `~/.local/share/applications/` 에 등록 → 사용자 런처 메뉴 즉시 반영.

---

## 10. Transport 선택 (agent vs FreeRDP)

**코드.** `src/winpodx/core/transport/__init__.py::dispatch`.

**2개 transport.**

| Transport | 메커니즘 | 창 깜빡임 | 기본 timeout | Latency |
|---|---|---|---|---|
| **Agent** | HTTP `/exec` on `127.0.0.1:8765`, bearer 인증 | 없음 (CreateNoWindow=$true) | 60초 | 100-300ms |
| **FreeRDP** | RemoteApp PS invocation via xfreerdp | 불가피한 PS 콘솔 | 30초 | 3-5초 |

**선택 규칙.** `dispatch(cfg)` 가 1-2초 timeout 으로 agent `/health` 호출. agent 응답하면 `AgentTransport` 반환. 아니면 `FreerdpTransport`.

**사용처.** `core.updates`, `core.daemon.sync_windows_time`, `cli.pod.multi-session`, `cli.main.debloat`, GUI Tools 페이지 debloat 핸들러 — 모두 `windows_exec.run_via_transport` 통과.

**의도적 미사용.** Password rotation 과 `winpodx pod sync-password` rescue 경로 — 둘 다 직접 credential 인증 필요, FreeRDP 강제.

### 10.1 어느 transport 가 활성인지 확인

```
PYTHONPATH=src python3 -c "
from winpodx.core.config import Config
from winpodx.core.transport import dispatch
print(type(dispatch(Config.load())).__name__)"
```

`AgentTransport` → /exec 경로. `FreerdpTransport` → 폴백.

### 10.2 Agent 프로세스 트리

```
HKCU\Run\WinpodxAgent 가 user logon 시 트리거:
  wscript.exe hidden-launcher.vbs  (GUI subsystem, 콘솔 없음)
    └─ powershell.exe -File C:\OEM\agent.ps1  (SW_HIDE 상속)
       └─ /exec 호출마다 child PS
          (ProcessStartInfo + CreateNoWindow=$true)
```

Agent listener: `http://+:8765/` + `netsh http add urlacl` 사전 등록 (User-level, admin 불필요). Token: `C:\OEM\agent_token.txt` (호스트에서 bind-mount).

---

## 11. 복구 시나리오

### 11.1 Agent 가 죽고 안 살아남

**증상.** `curl http://127.0.0.1:8765/health` exit 56 / 무응답.

**원인.** TermService 가 cycle 됨 (multi-session 활성화, 수동 재시작 등). agent 의 RDP 세션이 같이 죽음. HKCU\Run 은 user logon 시점만 발사, service 재시작 시점 아님.

**해결.** Windows 앱 아무거나 띄움 — 새 RDP 세션이 HKCU\Run 트리거 → wscript wrapper 로 새 agent. 앱 안 띄우고 pod idle 이면 agent dead 유지.

**예방.** PR #85 (활성화 전 ServiceDll 교차확인) 이 가장 흔한 redundant 활성화 케이스 차단. install.bat OEM v15+ 가 marker 정확히 작성 → 후속 apply 가 fast path 로.

### 11.2 매 앱 launch 마다 PS 콘솔 깜빡

**증상.** 검정 콘솔 ~50ms 깜빡 (UWP 든 Win32 든 무관).

**진단.** agent /exec 로 `HKCU\Run` 값 probe. `wscript.exe ... hidden-launcher.vbs` 로 안 감싼 항목 찾기. 흔한 culprit:

- `WinpodxMedia` — PR #84 에서 수정 (옛날엔 bare `powershell.exe -WindowStyle Hidden`).
- `WinpodxAgent` — PR #58 에서 수정.

**해결.** `winpodx pod apply-fixes` 가 둘 다 재작성 (`vbs_launchers` 단계가 legacy 항목 존재 조건부라 재실행 안전).

### 11.3 "Select a session to reconnect to" 다이얼로그

**증상.** Multi-session 활성 안 됨. 매 새 앱 launch 가 이전 세션 대체.

**진단.** `HKLM:\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll` probe:

- `C:\Program Files\RDP Wrapper\termwrap.dll` → rdprrap registry-patched. Multi-session 이 동작해야 함; 그래도 다이얼로그 뜨면 TermService 가 옛 `termsrv.dll` 로드한 채 cycle 안 함. `winpodx pod multi-session on` 으로 cycle (~10초 disconnect 후 OK).
- `C:\Windows\System32\termsrv.dll` → patch 안 됨. `winpodx pod multi-session on` 으로 install + 활성화.

### 11.4 컨테이너 예상 외 재생성

**증상.** Pod 예기치 않게 재시작. dockur log 에 fresh Windows install / ISO 재다운로드.

**원인 (PR #83 이전).** `image: :latest` 가 podman-compose 에 의해 재해상. dockur 가 마지막 `up` 이후 새 `:latest` push. 새 digest → spec mismatch → recreate.

**해결.** Migrate (PR #83 의 `_ensure_canonical_image_pin`) 가 compose 를 pinned digest 로 재작성. 미래 `:latest` push 가 사용자 안 흔듬.

### 11.5 Discovery 가 빈/부분 결과 반환

**증상.** `winpodx app refresh` 끝나는데 메뉴에 몇 개만 / UWP 항목 없음.

**원인 (PR #86 이전).** 첫 부팅 race — AppXSvc 아직 deploying / Start Menu indexer 아직 propagating / agent mid-respawn.

**해결.** PR #86 의 계층화된 race 회피. 스크립트 stderr (apply-fixes 로그에 보임) 에서 `[discover] stable (...) — proceeding` (좋음) vs `[discover] stability budget exceeded` (pod 이 진짜 60초+ 걸렸음) 확인. budget 적중 의심되면 `winpodx app refresh` 재실행.

### 11.6 Agent token 불일치

**증상.** 모든 `/exec` 호출 401 실패.

**원인.** `~/.config/winpodx/agent_token.txt` (호스트) 와 `C:\OEM\agent_token.txt` (게스트) 불일치. 보통 수동 편집 또는 부분 복원 후.

**해결.** `winpodx setup --non-interactive` 재실행 — `_ensure_oem_token_staged()` 가 fresh token 재생성 + staging. agent 가 받아들이도록 pod 재시작.

### 11.7 Pod 시작 안 됨

**증상.** `winpodx pod start` 가 컨테이너 up 보고하지만 `wait-ready` 가 phase 1/2 통과 못함.

**진단.** `podman logs winpodx-windows --tail 50`. 찾을 것:

- `proc.sh: line 137: -1: substring expression < 0` → dockur 의 `:latest` push 내부 버그. PR #83 이후엔 pin 이 보호하니 발생 불가.
- `mknod: /dev/net/tun: File exists` → 무해 경고, root cause 아님.
- BdsDxe boot loop 인데 `Windows started successfully` 없음 → 게스트 mid-Sysprep. 그냥 대기.

컨테이너 자체가 시작 안 함 (podman 즉시 exit) 이면, `podman ps -a --filter name=winpodx` 와 `podman inspect winpodx-windows -f '{{.State.Error}}'` 확인.

---

## 참고

- **[CHANGELOG.ko.md](CHANGELOG.ko.md)** — 릴리스 히스토리.
- **[AGENT_V2_DESIGN.md](AGENT_V2_DESIGN.md)** — agent 프로토콜 설계 노트.
- **[TRANSPORT_ABC.md](TRANSPORT_ABC.md)** — transport 추상화 내부.
- **[LIFECYCLE.md](LIFECYCLE.md)** — English version.
