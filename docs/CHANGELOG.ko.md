# 변경 이력

[English](../CHANGELOG.md) | **한국어**

이 프로젝트의 주요 변경 사항은 이 문서에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 기반으로 하며,
버전 정책은 [Semantic Versioning](https://semver.org/lang/ko/)을 지향합니다.

## [Unreleased]

## [0.1.9.5] - 2026-04-26

### 수정
- **결과 파일의 BOM 으로 인한 거짓 "fail" 보고.** v0.1.9.4 가 runtime apply 를 FreeRDP RemoteApp PowerShell 로 라우팅했는데, wrapper 가 `Out-File -Encoding utf8` 사용 → Windows PowerShell 5.1 은 UTF-8 BOM 을 붙임 → 호스트가 기본 utf-8 코덱으로 `json.loads` 시 BOM 거부 → "result file unparseable: Unexpected UTF-8 BOM". 사실 rdp_timeouts 와 oem_runtime_fixes 의 레지스트리 변경은 **실제 적용 성공**했고, 파싱만 실패해서 사용자가 "안 됐다" 고 본 것. `windows_exec.run_in_windows` 가 이제 `utf-8-sig` 로 읽어 BOM 자동 흡수.
- **`_apply_max_sessions` 가 자기 RDP 세션을 죽이던 문제.** payload 가 `Restart-Service -Force TermService` 호출했는데, TermService 가 바로 그 FreeRDP RemoteApp 세션 호스팅 중. 재기동 → 세션 강제 종료 → wrapper 가 결과 파일 쓰기 전 죽음 → 호스트는 `ERRINFO_RPC_INITIATED_DISCONNECT [0x00010001]` 봄. 레지스트리 쓰기는 됐을 수도 있지만 채널 실패로 잘못 분류. v0.1.9.5 는 in-script `Restart-Service` 제거; 레지스트리만 쓰면 충분, TermService 가 다음 자연 사이클 (다음 부팅 / 사용자 수동 `winpodx pod restart`) 때 새 값 반영.

### 변경 (아키텍처)
- **모든 host→Windows 명령 경로를 깨진 `podman exec ... powershell.exe` 에서 `windows_exec.run_in_windows` 로 마이그레이션**. 6개 함수가 0.1.0 ~ 0.1.9.4 동안 silent no-op 이었음 — `podman exec` 는 QEMU 호스팅하는 Linux 컨테이너만 도달, 그 안 Windows VM 에는 못 가서 `powershell.exe` 호출이 모두 `rc=127 executable file not found in $PATH` 로 실패하는데 헬퍼들이 warning 만 로그하고 return 했음. v0.1.9.5 는 모두 마이그레이션:
  - `provisioner._change_windows_password` (비밀번호 회전 — 수년간 silent fail)
  - `pod.recover_rdp_if_needed` (Bug B TermService 재기동 — 작동 안 했음; FreeRDP 도 죽은 RDP 리스너 인증 못하므로 컨테이너 재시작으로 대체)
  - `daemon.sync_windows_time` (w32tm)
  - `core.updates._exec_toggle` (Windows Update 활성화/비활성화/상태)
  - `cli/main._cmd_debloat` 와 `gui/main_window._on_debloat` (debloat.ps1 — `podman cp` + `podman exec` 둘 다 깨졌었음)
  - `core/discovery.discover_apps` (Bug A 의 stdin pipe "수정" 도 같은 깨진 경로; 이제 FreeRDP RemoteApp 로 진짜 적용)

### 추가
- **`winpodx pod sync-password`** CLI — 이전 릴리즈에서 누적된 비번 drift 복구. "마지막으로 작동한" 비번 (보통 초기 설치 시 또는 `compose.yml` 의 `PASSWORD` env var 값) 을 입력받아 FreeRDP 인증 → Windows 안에서 `net user` 실행 → 계정 비번을 현재 cfg.password 로 설정. 동기화 완료 후 비번 회전이 정상 작동.
- **migrate 자동 drift 감지.** `winpodx migrate` 가 "already current" 경로에서 작은 `Write-Output 'sync-check'` payload 를 FreeRDP 채널로 먼저 발사. auth/no-result-file 로 실패하면 "`winpodx pod sync-password` 실행" 안내 메시지 출력 → 그 이후 3개 apply 가 혼란스러운 채널 에러로 실패하는 것 방지.
- **Lint 테스트 `tests/test_no_broken_podman_exec.py`** — 향후 `src/winpodx/` 아래 (`windows_exec.py` 자체 제외) 에 `podman exec ... powershell.exe` 패턴 재도입 시 CI 실패. Windows-측 명령은 단일 채널로 강제.

## [0.1.9.4] - 2026-04-26

### 수정
- **Runtime apply 가 드디어 실제로 적용됨.** kernalix7 이 2026-04-26 에 v0.1.9.1 / v0.1.9.2 / v0.1.9.3 의 runtime apply 가 silently 실패하고 있다고 보고: `podman exec winpodx-windows ...\powershell.exe` 가 `rc=127 executable file not found in $PATH` 반환. 근본 원인: `podman exec` 는 QEMU 를 호스팅하는 **Linux 컨테이너 안**에서 명령을 실행하지, QEMU 안에서 도는 **Windows VM** 에선 안 돈다. Linux 컨테이너엔 `powershell.exe` 가 없음. 헬퍼들 (`_apply_max_sessions`, `_apply_rdp_timeouts`, `_apply_oem_runtime_fixes`, `_change_windows_password`) 이 모두 warning 만 로그하고 return 하는데, 공개 `apply_windows_runtime_fixes` 는 헬퍼가 raise 안 하니까 "ok" 로 보고. → 3개 릴리즈가 silent no-op 을 ship 했음. 3개 변경:
  1. **신규 `core/windows_exec.py`** — `run_in_windows(cfg, ps_payload)` 가 FreeRDP RemoteApp 으로 PowerShell 을 띄우고 기존 `\\tsclient\home` 리다이렉션으로 스크립트 파이핑. wrapper 가 `{rc, stdout, stderr}` JSON 을 같은 share 로 다시 씀. 호스트가 파싱해서 `WindowsExecResult` 반환. 채널 실패 (FreeRDP missing / auth fail / timeout / no result file) 는 `WindowsExecError` raise; 스크립트 non-zero rc 는 `WindowsExecResult.rc` 로 표면화.
  2. **`_apply_max_sessions`, `_apply_rdp_timeouts`, `_apply_oem_runtime_fixes` 재작성** — 각각 PS payload 빌드 → `run_in_windows` 호출 → `rc != 0` 시 `RuntimeError` raise 해서 실패가 진짜 전파됨.
  3. **`apply_windows_runtime_fixes` 정직한 보고** — `try/except` 구조 동일하지만, Windows VM 내부 rc가 실제로 fake `ok` 대신 `failed: rc=2 ...` 로 보고.

  비용: 호출당 ~5–10 초 (RDP handshake + auth + script + disconnect) + `-WindowStyle Hidden` 으로 최소화한 PowerShell 창 깜박임. 대신 기존 pod 에서 작동 (재생성 불필요) + rc 체크가 진짜 의미 있음.

  **주의사항**: `cfg.rdp.password` 가 Windows 게스트 실제 비밀번호와 일치해야 함. 이전 릴리즈에서 password rotation 이 같은 `podman exec` 원인으로 silently 실패해 왔다면, 첫 호출이 auth error 로 실패. 사용자가 `winpodx app run desktop` 으로 Windows 들어가서 `net user User <config-비밀번호>` 로 동기화 필요.

### 테스트
- `tests/test_windows_exec.py` 에 9개 신규 테스트 — FreeRDP missing / 비번 missing / timeout / no-result-file (auth fail) / happy path / non-zero rc propagation / FreeRDP `/app:program:` cmd 형태 검증 / flatpak 바이너리 splitting / unparseable JSON.
- `tests/test_provisioner.py` 재작성 — `subprocess.run` 대신 `windows_exec.run_in_windows` mock. 신규 테스트가 `rc != 0` 에서 `RuntimeError` raise + 채널 실패에서 `WindowsExecError` raise 검증.

## [0.1.9.3] - 2026-04-26

### 수정
- **Patch 버전 migrate 가 Windows-측 apply 를 건너뛰던 "already current" 트랩.** kernalix7 이 0.1.9.x 에서 0.1.9.2 로 업그레이드 후 `winpodx 0.1.9.2: already current. Nothing to migrate.` 만 보고 실제 Windows 게스트엔 v0.1.9.1 RDP-timeout / v0.1.9.2 OEM v7-baseline runtime 수정이 들어가지 않음. 원인: `_version_tuple(...)[:3]` 이 `0.1.9.1` 과 `0.1.9.2` 를 같은 `(0, 1, 9)` 튜플로 자르므로 `inst_cmp >= cur_cmp` 가 runtime apply 단계 **앞에서** early-return 시킴. 이제 "already current" 경로에서도 idempotent runtime apply 가 항상 실행됨.

### 추가
- **`winpodx pod apply-fixes`** 독립 CLI 명령. Idempotent — `_apply_max_sessions`, `_apply_rdp_timeouts`, `_apply_oem_runtime_fixes` 를 실행 중인 pod 에 호출하고 헬퍼별 OK/FAIL 테이블 출력. 성공 시 exit 0, pod 미실행/백엔드 미지원 시 2, 헬퍼 실패 시 3. 언제든 재실행 안전.
- **GUI Tools 페이지 "Apply Windows Fixes" 버튼.** 동일한 runtime apply 를 Qt GUI 에서 트리거 — worker thread 에서 헬퍼 호출, 기존 toast/info-label 채널로 성공/실패 표시. CLI 안 쓰고 GUI 만으로 적용 가능.
- **install.sh 가 매 설치 마지막에 `winpodx pod apply-fixes` 자동 호출.** migrate 위자드 다음 단계로 실행. `|| true` 로 실패 무해 — pod 안 켜져 있으면 silent skip. `curl | bash` 한 번이면 항상 최신 Windows-측 수정사항이 기존 게스트에 적용됨, migrate 의 버전 비교가 "진짜" 업그레이드를 봤는지와 무관.
- **공개 API `provisioner.apply_windows_runtime_fixes(cfg)`** — `{helper_name: "ok" | "failed: ..."}` 맵 반환. CLI / GUI / migrate 경로가 단일 진입점 공유.

## [0.1.9.2] - 2026-04-26

### 수정
- **v0.1.9 / v0.1.9.1 의 Windows-측 수정사항이 기존 게스트에 적용 안 되던 버그.** kernalix7 보고: "마이그레이션 잘 되는거 맞아? 윈도에 적용 안되는거같은데" — 사실이었음. install.bat (OEM 스크립트) 은 dockur 무인 설치 첫 부팅 시에만 도므로, 0.1.6 / 0.1.7 / 0.1.8 / 0.1.9 / 0.1.9.1 사용자는 컨테이너 재생성 없이 NIC power-save off (OEM v7), TermService failure-recovery (OEM v7), RDP timeout 비활성 + KeepAlive (OEM v8) 를 받을 수 없었음. 추가로, v0.1.9.1 의 `_apply_rdp_timeouts` runtime 헬퍼가 `provisioner.ensure_ready` 의 `check_rdp_port` early-return **뒤에** 와이어돼 있어서 이미 정상 동작 중인 포드에는 절대 도달하지 못했음.
  - `provisioner.ensure_ready`: 함수 상단에서 `pod_status` 한 번 probe 해서 idempotent runtime apply 들 (`_apply_max_sessions`, `_apply_rdp_timeouts`, 신규 `_apply_oem_runtime_fixes`) 을 RDP early-return **앞에서** 실행. cold-pod 경로에서는 pod 시작 후 재적용. 호출당 약 1.5s 오버헤드, 재실행은 모두 no-op.
  - 신규 `provisioner._apply_oem_runtime_fixes(cfg)`: OEM v7 baseline (NIC `Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false`, `sc.exe failure TermService` recovery 액션) 을 기존 게스트에 `podman exec powershell` 로 적용 — `discover_apps.ps1` 가 쓰는 stdin-pipe transport 재사용.
  - `winpodx migrate`: 0.1.9 boundary 를 넘는 업그레이드 감지 시 세 apply 헬퍼를 proactive 호출 (pod 상태 probe + stopped 시 interactive 시작 옵션). 헬퍼별 성공/실패 출력 — 컨테이너 재생성 없이 어떤 게 적용됐는지 사용자가 직접 확인 가능.

## [0.1.9.1] - 2026-04-26

### 수정
- **Apps "Refresh Apps" 버튼 누르면 GUI SEGV (pod 안 켜진 상태).** kernalix7 보고: `_on_refresh_failed` 가 queued-signal 콜백 프레임 안에서 `QMessageBox(self)` 를 바로 생성했는데 PySide6 + Qt 6.x 가 dialog 의 폰트 상속 경로 (`QApplication::font(parentWidget)` → `QMetaObject::className()`) 에서 부모 metaObject 가 콜백 중에 조회되며 SEGV. 이제 `QTimer.singleShot(0, ...)` 으로 dialog 생성을 다음 이벤트 루프 틱으로 미뤄서 signal handler 프레임이 먼저 풀림. Info 페이지의 첫 fetch 도 같은 이유로 `__init__` 에서 빠져나간 뒤 실행. Info worker 클래스를 모듈 레벨로 hoisting (refresh 마다 재정의되던 것), 재진입 busy guard 추가, worker + QThread 모두 정상 `deleteLater`.
- **호스트 suspend / 장기 유휴 후에도 RDP 세션이 사용 중에 끊기던 문제.** v0.1.9 Bug B 수정은 "RDP 도달 불가" 경로만 다뤘는데, Windows TermService 의 1시간 `MaxIdleTime` 기본값 등으로 활성 세션 자체가 종료될 수 있었음. install.bat (OEM v7 → v8) 와 새 `_apply_rdp_timeouts` provisioner 단계가 `MaxIdleTime=0`, `MaxDisconnectionTime=0`, `MaxConnectionTime=0`, `KeepAliveEnable=1` + `KeepAliveInterval=1` 을 `HKLM\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services` 와 `RDP-Tcp` WinStation 양쪽에 기록 + WinStation 에 `KeepAliveTimeout=1` (TCP keep-alive 1분 간격). 기존 0.1.x 게스트도 다음 `ensure_ready` 에서 자동 적용 — 컨테이너 재생성 불필요.

## [0.1.9] - 2026-04-25

### 변경
- **Discovery-first 리팩터.** `data/apps/` 아래 14개 번들 앱 프로필 (`word-o365`, `excel-o365`, ..., `notepad`, `cmd`, ...) 을 전부 제거. 이제 Linux 앱 메뉴는 `winpodx app refresh` 결과로만 채워지며, 첫 부팅 시 발견 트리가 비어 있을 때 `provisioner.ensure_ready` 가 자동 실행. 수동 재실행은 동일: CLI 의 `winpodx app refresh` 또는 GUI Apps 페이지의 "Refresh Apps" 버튼. `AppInfo.source` 에서 `"bundled"` enum 값 제거 — `"discovered"` 와 `"user"` 만 남음. 0.1.x &lt; 0.1.9 에서 업그레이드 시 `winpodx migrate` 가 기존 `~/.local/share/applications/winpodx-{14-bundled-slug}.desktop` 파일 정리 여부를 물음 (`--non-interactive` 에서는 자동 스킵).

### 추가
- **Info 페이지 (CLI + GUI).** 새 `core.info.gather_info(cfg)` 가 5섹션 스냅샷 반환 — System (winpodx 버전, OEM 번들 버전, rdprrap 버전, 배포판, 커널), Display, Dependencies, Pod (상태, 실행 시작 시각, RDP/VNC 도달성 probe, 활성 세션 수), Config (기존 budget 경고 포함). `winpodx info` 가 5개 섹션 모두 출력하도록 재작성. Qt 메인 윈도우에 5번째 탭 "Info" 추가 — 섹션당 카드 + "Refresh Info" 버튼이 `QThread` 로 `gather_info` 재실행. 모든 probe 가 하드 타임아웃되어 아픈 pod 가 패널을 멈추지 않음.

### 수정
- **Bug A: Windows 게스트 대상 `winpodx app refresh`.** v0.1.8 에서 `podman cp host:discover_apps.ps1 container:C:/winpodx-discover.ps1` 가 실패 — dockur/windows 는 QEMU 안에서 실제 Windows 게스트를 돌리는 Linux 컨테이너이고, C: 드라이브는 가상 디스크 안에 있어 `podman cp` 로는 도달 불가. 이제 스크립트 본문을 `podman exec -i container powershell -NoProfile -ExecutionPolicy Bypass -Command -` 의 stdin 으로 파이핑하므로 staging 단계 자체가 사라짐. 컨테이너 런타임 stderr 에 "no such container", "is not running" 등이 보이면 `kind="pod_not_running"` 으로 재분류 — cli 는 exit code 2 + "run `winpodx pod start --wait`" 힌트로 라우팅 유지.
- **Bug B: 호스트 suspend / 장기 유휴 후 RDP 도달 불가.** 증상: VNC 포트 8007 은 살아있는데 RDP 포트 3390 만 응답 없음 — Windows TermService 가 멈추거나 가상 NIC 가 절전으로 빠짐. 새 `core.pod.recover_rdp_if_needed(cfg)` 가 이 비대칭을 감지하고 `podman exec powershell Restart-Service -Force TermService; w32tm /resync /force` 실행 후 RDP 재 probe (최대 3회, 백오프). `provisioner.ensure_ready` 의 `_ensure_pod_running` 직후에 와이어. OEM 번들 6 → 7 — `install.bat` 에 예방 조치 추가: `Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false` 와 `sc.exe failure TermService reset=86400 actions=restart/5000/restart/5000/restart/5000` 로 Windows 자체 복구.

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
