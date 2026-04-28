# 변경 이력

[English](../CHANGELOG.md) | **한국어**

이 프로젝트의 주요 변경 사항은 이 문서에 기록됩니다.

형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 기반으로 하며,
버전 정책은 [Semantic Versioning](https://semver.org/lang/ko/)을 지향합니다.

## [Unreleased]

## [0.2.1] - 2026-04-28

마이너 버전 (0.2.0.x → 0.2.1) — UX 개선 묶음: install 이 부분 완료 상태로 끝나도 다음 실행 시 자동 재개, GUI 로그가 winpodx 자체 로그를 실시간으로 표시, GUI 첫 실행 시 시스템 체크 안내.

### 추가
- **`utils.pending` 재개 시스템.** 새 `~/.config/winpodx/.pending_setup` 마커가 install.sh 가 못 끝낸 단계 (`wait_ready` / `migrate` / `discovery`) 추적. 다음 CLI 호출 (version/help/uninstall/config/info 외 모든 서브커맨드) 과 GUI 시작 시 마커 픽업해서 미완료 단계를 canonical 순서로 실행. 각 단계는 성공 시 마커에서 자체 제거; 빈 상태 되면 파일 삭제. 10개 단위 테스트가 순서, 멱등성, 부분 완료, "게스트 부팅 중 → 후속 단계 시도 안 함" 가드 커버.
- **GUI 첫 실행 Quick Start 다이얼로그.** 최초 launch 시 5-bullet 스냅샷 표시 — backend / FreeRDP / pod 상태 / RDP listener / 디스커버리된 앱 수 — 백그라운드 resume 진행 여부도 안내. dismiss 시 `~/.config/winpodx/.welcomed` 작성하여 재방문 사용자에게는 안 띄움.
- **GUI 로그 페이지가 winpodx 앱 로그 자동 tail.** Tools/Terminal 페이지로 이동하면 기본으로 `tail -F ~/.config/winpodx/winpodx.log` 스트림 시작 — 사용자가 내부 프로그램 로그 (apply / probe / refresh / pod 상태 전이) 를 기존 on-demand 컨테이너 로그 버튼과 함께 봄. 페이지 떠나면 streamer 자동 종료.

### 변경
- **install.sh wait-ready timeout 1800s → 3600s.** 예산을 1시간으로 늘려, 느린 하드웨어 신규 설치 (Windows ISO 다운로드 + Sysprep + OEM apply 첫 실행) 가 인라인으로 완료될 수 있게 함 (이전엔 timeout 후 resume 훅에 미룸). 1시간 초과 작업은 여전히 resume 훅이 picking up.
- **`pod.max_sessions` 기본값 10 → 25, `pod.ram_gb` 기본값 4 → 6.** 10은 실제 사용 (Office + Teams + Edge + 사이드 앱 몇 개 동시) 에 빡빡함. 새 RAM 기본값이 25 sessions 에서 session-budget 경고 안 띄움 (2.0 + 25 × 0.1 ≈ 4.5 GB 필요). 아래 tier auto-detect 가 머신별로 추가 조정.

### 추가 (보충)
- **Setup 의 호스트 스펙 auto-tier.** 새 `utils.specs.detect_host_specs` 가 `/proc/meminfo` + `os.cpu_count()` 읽고 `recommend_tier` 가 3개 preset 중 하나 매핑:

      호스트 RAM    호스트 CPU    티어    VM CPU   VM RAM
      ≥32 GB        ≥12 thr      상       8       12 GB
      16-32 GB       6-12 thr    중       4        6 GB
      <16 GB         <6 thr      하       2        4 GB

  두 축 모두 임계값 통과해야 상위 티어 — 64 GB / 4-core 호스트는 CPU 가 병목이라 "하" 받음. 대화형 setup 은 추천값을 기본으로 표시, 비대화형은 즉시 적용. 10개 단위 테스트가 양축-통과, 단축-부족, 임계 경계 커버.

### 수정 (보충)
- **앱 실행할 때마다 "Select a session to reconnect to" 다이얼로그 발생 — zombie disconnected 세션 누적 원인.** `install.bat` 와 `_apply_rdp_timeouts` 양쪽이 `MaxDisconnectionTime` 을 `0` 으로 설정. RDP 의미론에서 `0` = **timeout 없음** = disconnect 된 세션이 영원히 살아있음. 사용자가 FreeRDP 창 닫을 때마다 `Disc` 상태 세션 누적 → 다음 launch 시 Windows 가 그동안 쌓인 세션 리스트로 재연결 다이얼로그 띄움. rdprrap 멀티세션은 세션 동시 실행은 허용하지만 이 prompt 는 못 막음 — auto-logoff 만이 답. v0.2.1 에서 `30000` (30초) 으로 변경 — disconnect 후 30초 뒤 자동 logoff, 사용자가 앱 닫고 다시 열어도 zombie 누적 안 됨. `install.bat` (신규 컨테이너) + `_apply_rdp_timeouts` (런타임 apply 로 기존 컨테이너 패치) 양쪽 수정.
- **`_apply_max_sessions` 가 틀린 레지스트리 키에 씀.** 런타임 apply 가 `HKLM\...\Terminal Server\MaxInstanceCount` 에 썼지만 Windows 는 실제로 `HKLM\...\Terminal Server\WinStations\RDP-Tcp\MaxInstanceCount` 를 읽음. 결과: session-cap 도입 이후 모든 릴리스가 cfg 변경 시 silent no-op — `install.bat` 의 OEM 시점 값만 authoritative 였음. v0.2.1 이 올바른 subkey 에 쓰고 (`fSingleSessionPerUser` 는 Terminal Server root 에 있는 게 맞음, 그대로 유지), OEM 시점 install.bat 천장도 10 → 50 으로 상향해서 cfg 값이 [1, 50] clamp 안에서 install time 에 silent cap 안 되게.



### 수정
- **GUI Refresh 두 번째 SEGV 경로 — Python ref / Qt deleteLater race.** v0.2.0.10 이 QImage-워커스레드 크래시는 잡았지만 두 번째 SEGV 가 남아있었음: `_on_refresh_succeeded` 와 `_on_refresh_failed` 슬롯이 즉시 `self._refresh_worker = None` 실행. Python 의 ref drop 이 Qt 의 queued `worker.deleteLater()` 이벤트와 race — 둘 중 나중에 실행되는 쪽이 free 된 `QObject` 만나서 worker 스레드의 `~QObject()` 에서 크래시. 2026-04-28 코어덤프로 확인: 워커 스레드 2282062 의 top frame 이 `QObject::~QObject`, 메인 스레드 2281803 은 슬롯의 PySide6 `callPythonMetaMethod` 디스패치 중. 수정: `_refresh_worker` / `_refresh_thread` Python ref drop 을 `_cleanup_refresh_worker` 로만 옮김, `thread.finished` 에 바인딩되어 Qt 객체 둘 다 완전 해제된 후 실행. Worker `deleteLater` 는 워커 스레드 자체 이벤트 루프에서 정상 처리 — Python GC 간섭 없음.



### 수정
- **GUI Refresh 버튼 SEGV.** `_DiscoveryWorker.run()` (Qt 워커 스레드) 가 `persist_discovered` → `_validate_png_bytes` → `QImage.loadFromData` 호출. Wayland 의 Qt + libgallium / Mesa state 가 메인 스레드 외에서 QImage 만지면 race → `Signal: 11 (SEGV)` 코어 덤프. v0.2.0.10 에서 `_validate_png_bytes` 가 `threading.current_thread() is not threading.main_thread()` 일 때 stdlib 청크 워커로 단축 회귀. 워커도 여전히 CRC + 크기 캡 + IEND terminator 강제하므로 off-main-thread 호출자는 약간 느리지만 크래시 없는 경로.
- **install.sh wait-ready 600s → 1800s.** 신규 설치 (`uninstall --purge` 후 재설치) 는 ~7.5GB Windows ISO 다운로드 + 추출 + Sysprep + OEM apply + 최종 재부팅 = 첫 실행 15~30분. 600초 timeout 이 Windows VM 부팅 전에 발화 → `[FAIL] Timeout waiting for Windows ready (09:56)` 로 끝남. 1800초 예산이 일반적 환경에서 신규 설치 커버; 후속 설치는 캐시된 ISO 재사용해서 2~5분.
- **GUI Refresh 가 `.desktop` 엔트리 자동 설치** (`winpodx app refresh` CLI 와 parity). 기존엔 CLI 경로만 인라인 등록했고 GUI Refresh 는 discovered 트리만 갱신, `~/.local/share/applications/` 는 안 건드림. v0.2.0.10 의 `_DiscoveryWorker` 가 `_sync_desktop_entries` 호출 — `cli/app._register_desktop_entries` 의 워커-스레드-안전 형제 함수.

### 추가
- **첫 부팅 GUI 자동 디스커버리.** Pod 가 `running` 으로 전이 + 앱 리스트 비어있을 때, 메인 윈도가 2초 settle 후 Refresh 워커 자동 발화. install.sh 의 wait-ready 가 Sysprep 끝나기 전에 timeout 한 케이스 해결 — 사용자가 나중에 GUI 열면 pod 살아있는 거 확인 후 디스커버리가 알아서 발화.
- **GUI 실시간 로그 스트리밍.** Tools/Terminal 페이지에 4개 버튼 추가: `Live (pod)` 와 `Live (app)` 가 컨테이너 또는 `~/.config/winpodx/winpodx.log` 에 `tail -F` 걸어 새 라인을 패널로 스트리밍; `App log` 는 winpodx 자체 앱 로그 마지막 200줄 표시; `Stop tail` 은 활성 streamer 종료. 기존엔 pod 로그 100줄 one-shot 스냅샷만 있었음.



### 수정
- **두 번째 앱 실행 시 독립 윈도 대신 Windows "Select a session to reconnect to" 다이얼로그 발생.** Windows 기본값이 사용자당 동시 FreeRDP RemoteApp 세션 거부 → 첫 앱 이후의 모든 launch 가 기존 세션에 묻히거나 reconnect 다이얼로그 띄움. v0.2.0.9 에서 self-heal apply 체인에 `_apply_multi_session` 추가 — 게스트 안에서 `rdprrap-conf --enable` 호출해 termsrv.dll 패치 활성화 → launch 마다 독립 세션. 멱등 (이미 활성화돼있으면 no-op), 구 OEM 번들에 rdprrap-conf 없으면 best-effort skip.
- **앱이 Windows 에서 삭제됐는데 DE 메뉴에 `.desktop` 엔트리가 계속 남음.** v0.2.0.8 이 refresh 자동 설치는 추가했지만 사라진 앱의 엔트리 제거는 안 했음. v0.2.0.9 에서 refresh 진짜 양방향 동기화: `list_available_apps()` 에 없는 모든 `winpodx-*.desktop` 파일이 (해당 아이콘과 함께) 제거됨 → Windows 에서 Office 지우면 다음 refresh 때 launcher 에서도 Word/Excel/PowerPoint 사라짐. `~/.local/share/winpodx/data/apps/` 의 사용자 작성 엔트리는 보존.

### 변경
- **README 정보량 강화.** 상단에 `for-the-badge` 스타일 "Status: Beta" + "Latest release" 배지. 그 아래 표준 shields (license, Python, backend, language, tests, CI). 소셜 행 (stars, forks, watchers, unique visitors). 활동 행 (issues, PRs, last commit, code size). EN + KO 동기화.



### 수정
- **`winpodx app refresh` 가 앱 발견은 하지만 데스크톱 메뉴에는 등록 안 함.** refresh 경로는 `app.toml` + 아이콘을 `~/.local/share/winpodx/discovered/` 에 저장만 하고, 실제 `.desktop` 엔트리는 별도 `winpodx app install-all` 명령으로만 생성됐음 → 사용자가 "Discovered N app(s)" 메시지 본 후 DE 메뉴에 앱이 안 떠서 혼란. v0.2.0.8 부터 refresh 가 발견된 앱들의 .desktop 엔트리를 자동 설치 (best-effort, 실패는 warn 만 하고 refresh 자체는 계속) + 아이콘 캐시 갱신.
- **앱 실행할 때마다 PowerShell 창 깜빡임.** `ensure_ready` 의 self-heal apply 경로가 매 앱 실행마다 FreeRDP RemoteApp PowerShell payload 3개 발화. `-WindowStyle Hidden` 으로 작아져도 여전히 매번 눈에 띄게 깜빡임. apply 자체는 레지스트리 멱등이라 warm pod 에서 재실행해도 가시적 효과 없음 — 순수 노이즈. v0.2.0.8 부터 self-heal 성공 후 `~/.config/winpodx/.applies_stamp` 에 `<winpodx_version>:<container_StartedAt>` 기록 → 이후 launch 는 단축 회귀, pod 재시작 (TermService / NIC 설정 재적용 필요) 또는 winpodx 업그레이드 시에만 다시 발화.



### 수정
- **빠른 컨테이너에서 `pod wait-ready --logs` 가 `[container]` 라인 하나도 안 띄움.** 두 가지 문제: (1) tail 을 `--tail 0` 으로 시작했는데 이건 "지금부터의 로그만 표시" 의미. 하지만 dockur 는 Windows ISO 다운로드 / 부팅 단계 메시지를 wait-ready 실행 *전에* 이미 출력 → 사용자에게 아무것도 안 보임. (2) `stdout` 만 drain. dockur 는 진행 메시지를 stdout (다운로드 byte/s) 과 stderr (부팅 단계) 로 나눠 출력해서 절반이 사라짐. v0.2.0.7 에서 `--tail 100` 으로 최근 컨텍스트 즉시 표시 + stdout/stderr 둘 다 병렬 스레드로 drain.



### 수정
- **`wait_for_windows_responsive` 가 부팅 중 게스트에서 1초도 안 되어 무너져 `pod wait-ready` UX 가 통째로 망가짐.** 헬퍼가 RDP TCP 포트 열림은 제대로 대기했지만, 그 다음 FreeRDP RemoteApp probe 를 **단 한 번만** 발화. 한 번 실패하면 (부팅 중 게스트는 항상 rc=147 connection-reset 반환) 즉시 False return → 호출자가 넘긴 600초 timeout 이 무시됨. v0.2.0.6 에서 probe 를 retry loop 로 변경: 5-20초짜리 probe 를 deadline 까지 반복 (FreeRDP 프로세스 CPU 점유 막기 위해 3초 간격). 이제 `pod wait-ready --timeout 600` 이 진짜 10분까지 기다림 — phase 3 의 elapsed time 이 증가하는 게 보임.



### 추가
- **`winpodx pod wait-ready [--timeout SEC] [--logs]`** — Windows VM 첫 부팅 다단계 wait gate. 세 체크포인트를 elapsed time 과 함께 표시해서 사용자가 침묵 속 몇 분간 hang 대신 실제 진행 상황을 봄:
  - `[1/3] Container running` (~5초)
  - `[2/3] RDP port open` (보통 30-90초)
  - `[3/3] Windows ready (RemoteApp probes OK)` (첫 부팅 시 보통 2-8분)
  `--logs` 옵션 시 컨테이너 stdout 을 백그라운드 스레드로 tail 해서 `[container] ...` 라인으로 surfacing — Windows 가 실제로 뭐 하는지 (Sysprep, OEM apply 등) 보임. 블랙박스 → 가시성.

### 변경
- **`install.sh` 가 진짜 single-shot 으로 바뀜 — 설치 정말 끝났을 때 exit, 컨테이너 시작했다고 거짓말 안 함.** 새 흐름: `setup` → `pod wait-ready --logs` (최대 10분, progress + 컨테이너 로그) → `migrate` (게스트 ready 라 apply 깔끔히 통과) → `app refresh` (디스커버리 즉시 통과). 기존에는 Windows 가 아직 부팅 중인데 `Installation complete!` 표시 후, 사용자가 첫 앱 실행 시 또 기다리는 구조였음. CI / 비대화형 환경에서는 `WINPODX_NO_WAIT=1` 로 wait 우회, `WINPODX_NO_DISCOVERY=1` 로 디스커버리 우회.
- install.sh 의 중복 `winpodx pod apply-fixes` 호출 제거 — v0.1.9.3 이후 `migrate` 의 "always-apply" 경로가 이미 apply 를 돌리므로 한 번 더 부르면 wait 만 두 배가 됨.



### 수정
- **신규 `--purge` 설치마다 가짜 "cfg.password does not match Windows" 경고.** v0.1.9.5 가 추가한 `_probe_password_sync` (cfg/Windows 비밀번호 drift 사전 감지) 의 에러 분류기가 FreeRDP 에러 문자열에 `"no result file"` 또는 `"auth"` 가 들어있으면 drift 로 판정. 하지만 부팅 중 게스트 (모든 신규 설치가 거치는 상태) 는 FreeRDP 가 rc=147 `ERRCONNECT_CONNECT_TRANSPORT_FAILED` (connection reset) 반환 → host wrapper 가 `"No result file written"` 으로 감쌈 → 분류기가 `"no result file"` 매칭 → 가짜 drift 경고 발화. v0.2.0.4 가 두 방향으로 수정:
  1. probe 가 `wait_for_windows_responsive(timeout=180)` 로 먼저 대기. 게스트 미준비면 `(probe deferred — guest still booting; will retry on next ensure_ready)` 메시지로 skip.
  2. 분류기가 transport-level 실패 (`rc=131`, `rc=147`, `transport_failed`, `connection reset`) 와 실제 auth 실패 (`logon_failure`, `STATUS_LOGON_FAILURE` 등) 를 구분. 후자만 sync-password 경고 발화.



### 수정
- **Discovery 가 apply path 와 동일한 부팅 race 에 노출.** v0.2.0.1 이 `_apply_*` 와 `pod apply-fixes` 만 `wait_for_windows_responsive` 로 게이팅하고, `winpodx migrate` 의 "Run app discovery now?" 프롬프트와 `provisioner._auto_discover_if_empty` (첫 부팅 시 ensure_ready 가 발화) 는 probe 없이 FreeRDP RemoteApp 채널 호출. 신규 `--purge` 설치 시 QEMU 안 Windows VM 이 여전히 부팅 중인데 discovery 가 떠서 `ERRCONNECT_CONNECT_TRANSPORT_FAILED [0x0002000D]` (rc=147, connection reset) 으로 무너지고 사용자는 빈 앱 메뉴로 끝남. v0.2.0.3 이 두 discovery 호출 지점 모두에 동일 probe 적용 — wait 후 scan 또는 "Re-run later with: winpodx app refresh" 안내로 skip.
- **첫 부팅 timeout 90s → 180s.** 실제 환경의 신규 설치는 느린 하드웨어에서 Windows + RDP + activation 핸드셰이크에 90초 초과 가능. 세 개의 apply / discovery probe 의 wait 예산을 180초로 상향 — one-shot install 이 첫 시도에 apply round 까지 완료할 수 있게 함.



### 수정
- **`--purge` 신규 설치가 가짜 "0.1.7 -> X detected" 업그레이드 메시지 표시.** `winpodx setup` 이 `winpodx.toml` 만 저장하고 `installed_version.txt` 마커는 안 써서, `install.sh` 가 자동으로 이어 호출하는 `winpodx migrate` 가 "config 있고 marker 없음" 상태 보고 pre-tracker fallback (baseline 0.1.7 가정) 발동. 실제 마커 도입 전 업그레이드에서는 맞는 동작이지만, 신규 설치에서는 모든 마이그레이션 스텝을 불필요하게 재실행하면서 "What's new in 0.1.8 / 0.1.9 / ..." 안내까지 띄움. v0.2.0.2 에서 setup 이 마커가 없을 때만 현재 버전을 `installed_version.txt` 에 기록하도록 수정 — 신규 설치는 현재 버전으로 보고되어 마이그레이션 스텝 발화 안 함, 실제 업그레이드 흐름은 그대로 동작.



### 수정
- **차가운 컨테이너에서 apply cascade 가 무너짐.** v0.2.0 은 `pod_status` 가 `RUNNING` 이 되는 즉시 세 개 idempotent runtime apply (`max_sessions`, `rdp_timeouts`, `oem_runtime_fixes`) 를 발화. dockur Linux 컨테이너는 몇 초 안에 `RUNNING` 도달하지만, QEMU 안 Windows VM 은 RDP 리스너가 FreeRDP RemoteApp activation 받기까지 30~90초 더 필요. 그 윈도우 안에서 모든 apply 는:
  - 신규 설치 시: `ERRCONNECT_CONNECT_TRANSPORT_FAILED [0x0002000D]` (rc=147, RDP 소켓은 열렸지만 서버 미초기화 — connection reset by peer)
  - `winpodx pod restart` 시: `ERRCONNECT_ACTIVATION_TIMEOUT [0x0002001C]` (rc=131, FreeRDP 연결됐지만 activation 단계 미완료)
  로 무너짐. 각 apply 가 60초 timeout 풀로 대기 → cascade 3분 → 사용자가 앱 실행 시 Launch Error 다이얼로그 또는 `winpodx setup` → `winpodx migrate` 도중 "3 of 3 applies failed" 패닉 메시지로 표면화.
- 새 헬퍼 `wait_for_windows_responsive(cfg, timeout=90)`: `check_rdp_port` 폴링 후 20초 no-op `Write-Output 'ping'` 프로브로 FreeRDP RemoteApp 채널이 실제로 살아있는지 확인. 다음 경로의 precondition 으로 사용:
  - `ensure_ready()` warm-pod 경로 — 게스트가 미응답이면 self-heal apply 블록 통째로 skip.
  - `winpodx pod apply-fixes` CLI — 명시적 "Waiting for Windows guest to finish booting (up to 90s)…" 메시지로 hang 아님을 표시.
  - `winpodx migrate` apply 단계 — 같은 wait + 채널 실패 스택트레이스 3개 대신 "게스트 부팅 중; 나중에 apply-fixes 실행하거나 그냥 앱 실행해도 됨" 명확한 메시지.
- `_self_heal_apply()` (신규) — warm-pod ensure_ready apply 블록을 `WindowsExecError` swallow 로 감싸서 transient 채널 실패가 cascade 안 되고 warning 만 로그 후 같은 호출 내 추가 시도 중단. 다음 ensure_ready 가 이어받음.



### 수정
- **`oem_runtime_fixes` 가 `AllowComputerToTurnOffDevice` 파라미터 오류로 첫 적용 실패.** v0.1.9.5 가 runtime apply 를 FreeRDP RemoteApp PowerShell 로 넘겼지만 payload 는 여전히 `Set-NetAdapterPowerManagement -AllowComputerToTurnOffDevice $false` 호출. 이 cmdlet 은 enum 문자열 `'Disabled'` / `'Enabled'` 를 요구하고, QEMU 안 가상 NIC (virtio) 는 이 파라미터 자체가 노출 안 되는 경우 잦음. v0.2.0 은 `try/catch` 로 감싸고 enum 형태로 전환, 미지원 어댑터는 건너뜀 — NIC 토폴로지 무관 apply 성공.
- **migrate 의 password-drift 프로브가 20초에 timeout.** 차가운 pod 의 FreeRDP 첫 연결 (TLS + 인증 + RemoteApp 실행) 은 20초 자주 넘김. v0.2.0 은 프로브 예산 60초로 상향, cold-start 지연 때문에 실제 drift 가 가려지지 않게 함.

### 추가
- **Refresh 진행 상황 스트리밍.** 기존 `winpodx app refresh` 는 게스트 enumerator 가 Registry App Paths / Start Menu / UWP 패키지 / choco·scoop shim 을 30~90초 동안 도는 동안 침묵. v0.2.0 은 스트리밍 진행 채널 추가 — `windows_exec.run_in_windows` 가 `progress_callback` 받고, wrapper 가 `$Global:WinpodxProgressFile` + `Write-WinpodxProgress` 정의, `discover_apps.ps1` 가 소스별로 한 줄씩 출력. 호스트 CLI 는 stderr 로 `... Scanning Registry App Paths...` 식으로 표시 (JSON 출력은 그대로 깨끗).
- **`winpodx pod multi-session {on|off|status}`** — 번들 rdprrap 다중 세션 RDP 패치 런타임 토글. FreeRDP RemoteApp 로 Windows 게스트 안에서 `rdprrap-conf.exe` 호출하므로 패치 enable/disable/inspect 위해 컨테이너 재생성 불필요. `C:\OEM\rdprrap\rdprrap-conf.exe`, `C:\OEM\rdprrap-conf.exe`, `C:\Program Files\rdprrap\rdprrap-conf.exe` 순으로 탐색.
- **디스커버리 junk 필터.** Refresh 가 그동안 uninstaller (`unins000.exe`, "Uninstall …"), 재배포 패키지 (`vc_redist.x64.exe`, "Microsoft Visual C++ …"), 헬퍼 (`crashpad_handler.exe`), inbox 접근성 도구 (`narrator.exe`, `magnify.exe`, `osk.exe`), 시스템 plumbing (`ApplicationFrameHost.exe`, `RuntimeBroker.exe`), DisplayName 미해결 UWP fallback (예: `Microsoft.AAD.BrokerPlugin`) 을 모두 노출했음. v0.2.0 은 호스트측 denylist 패턴 + 실행 파일 basename 매칭 + UWP fallback 감지로 모두 drop. 디버깅 시 `WINPODX_DISCOVERY_INCLUDE_ALL=1` 로 우회 가능.
- **GUI 앱 아이콘.** 디스커버리한 앱이 launcher 의 grid 카드와 리스트 타일에서 실제 Windows 아이콘 (PNG / SVG) 으로 렌더링됨 — 기존 색상+첫글자 avatar 대신. 아이콘은 v0.1.8 부터 `~/.local/share/winpodx/data/discovered/<slug>/icon.{png,svg}` 에 저장되어 있었고, GUI 가 이제 `QPixmap` (PNG, smooth scaled) + `QSvgRenderer` (SVG, 모든 크기 crisp) 로 읽음. 아이콘 없는 앱은 letter avatar 로 fallback.

### 테스트
- 스트리밍 progress wrapper: Popen 기반 테스트가 progress-file 쓰기 인터리브된 3-poll lifecycle 시뮬레이션.
- Junk 필터: 11 가지 쓰레기 케이스 drop, 4 가지 실제 앱 보존, env-bypass 동작 검증.



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
