# 사용법

[English](USAGE.md) | **한국어**

CLI, GUI, 설정, 헬스 체크. 설치 후 필요한 모든 것.

## 앱 실행

```bash
winpodx app run word              # Word 실행
winpodx app run word ~/doc.docx   # 파일 열기
winpodx app run desktop           # 전체 Windows 데스크톱
```

또는 그냥 애플리케이션 메뉴에서 앱 아이콘 클릭 — WinPodX 가 pod 첫 부팅 시 발견된 모든 Windows 앱을 `.desktop` 엔트리로 등록함.

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

# Pod 라이프사이클 (컨테이너 상태만 — 게스트 작업은 `guest`, 디스크/설치는 `install`)
winpodx pod start --wait          # 시작 후 RDP 준비 대기
winpodx pod stop                  # 중지 (활성 세션 있으면 경고)
winpodx pod status                # 상태 + 세션 수
winpodx pod restart
winpodx pod recreate              # stop + remove + start (깨끗한 컨테이너)
winpodx pod wait-ready --logs     # Windows 첫 부팅 대기 + 진행 + 컨테이너 로그 (느린 ISO 다운로드 시 자동 연장)

# 게스트 작업 (0.6.0 에서 `pod <x>` → `guest <x>` 로 개명, 기존 이름도 0.6.x 동안 deprecation 경고 + 동작)
winpodx guest apply-fixes         # Windows 측 런타임 fix 재적용 (idempotent)
winpodx guest sync                # host 업데이트(agent / urlacl / rdprrap / 픽스)를 guest 에 푸시 — 재설치 없이
winpodx guest sync --force        # guest 버전 stamp 가 일치해도 재sync
winpodx guest sync-password       # 비밀번호 drift 복구 (cfg ↔ Windows)
winpodx guest multi-session on    # bundled rdprrap 멀티세션 RDP 토글
winpodx guest multi-session status
winpodx guest recover-oem         # dockur 첫 부팅 OEM 복사 실패 시 install.bat 를 수동 실행하는 noVNC PowerShell 단계 출력 (#287)

# 설치 / 디스크 작업 (0.6.0 에서 `pod install-* / pod grow-disk / pod disk-usage` → `install <x>` 로 개명)
winpodx install status            # 설치 진행 / 보류 단계 (#271 agent-first 설치)
winpodx install resume            # 미뤄진 설치 단계 재개
winpodx install disk-usage        # Windows C: 크기 / 여유 / 사용% + 자동확장 상태 (#318)
winpodx install grow-disk         # 자동확장 increment(기본 32G) 만큼 디스크 확장 + C: extend (#318)
winpodx install grow-disk 128G    # 절대 크기로 확장
winpodx install grow-disk --extend-only   # 기존 미할당 공간으로 C: 만 확장

# 전원 관리
winpodx power --suspend           # 컨테이너 pause (CPU 해제, 메모리 유지)
winpodx power --resume            # pause 된 컨테이너 resume

# 호스트 장치 패스스루 (USB / PCI → Windows 게스트, #286)
winpodx device list               # 호스트 USB / PCI 장치 + attach 상태 목록
winpodx device attach <id>        # 호스트 장치를 게스트에 attach (USB 는 live hot-plug; PCI 는 부팅 시 추가)
winpodx device detach <id>        # 게스트에서 장치 detach
winpodx device attach <id> --force   # PCI 장치의 게스트 재시작 안전 확인 건너뛰기

# 보안
winpodx rotate-password           # Windows RDP 비밀번호 회전 (host config + Windows guest 계정 둘 다)

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
winpodx setup                     # full 셋업: config + 컨테이너 + wait-ready + discovery + reverse-open
winpodx setup --customize         # wizard: backend / specs / edition / language / region / keyboard / timezone / tuning
winpodx setup-host                # 호스트 준비 wizard (kvm 그룹, /etc/subuid, kvm 모듈) pkexec 한 번 — AppImage 사용자
winpodx provision                 # pod 기동 후 체인 (wait-ready → apply-fixes → discovery → reverse-open) — install.sh / setup / migrate / GUI 가 모두 호출하는 단일 SoT (0.6.0 item B)
winpodx provision --retries N     # discovery 재시도 횟수 재정의 (기본 2 — 0.6.0 item M)
winpodx provision --require-agent # 게스트 agent 강제 게이트 (신규 설치용, #271)
winpodx migrate                   # 기존 guest in-place 업그레이드 (agent.ps1 / 스크립트 갱신, 픽스 재적용, 재discovery, reverse-open 갱신)
winpodx doctor                    # read-only 헬스 진단 + per-check fix 힌트 (deps / pod / RDP / agent / disk / config / install 상태)
winpodx doctor --json             # 같은 체크, machine-readable JSON Finding 배열
winpodx doctor --quick            # slow probe(컨테이너 헬스 / guest exec) 생략 — 가벼운 로컬 체크만 (< 1 s)
winpodx doctor --fix              # warn/fail finding 중 fixer 가진 것을 idempotent 자동 복구 (dead agent / stale lock / 누락 desktop entry / OEM 버전 drift)
winpodx autostart on|off|status   # 로그인 시 Windows pod 자동시작 (opt-in; 기본 꺼짐)
winpodx language                  # 현재 UI 언어 표시
winpodx language ko               # UI 언어 설정: auto | en | ko | zh | ja | de | fr | it (auto = 호스트 로케일)
# `winpodx info` 와 `winpodx check` 는 `winpodx doctor` 의 deprecated alias (0.6.x 동안 deprecation 경고와 함께 동작; 0.7.0 에서 제거).
winpodx gui                       # Qt6 메인 윈도 실행 (Dashboard / All apps / Devices / Settings / Tools / Terminal)
winpodx tray                      # Qt 시스템 트레이 아이콘 실행
winpodx config show               # 현재 config 표시
winpodx config set rdp.scale 140  # config 값 변경
winpodx config import             # 기존 winapps.conf import
```

## GUI

`winpodx gui` 로 실행. Qt6 메인 윈도는 Start-menu 스타일 셸 (#460-#471): 페이지당 한 줄인 좌측 세로 네비게이션 사이드바, 커맨드 바 역할도 하는 히어로 검색바, 자체 제작 SVG 아이콘 세트, 좁은/분수 배율 창에서 reflow 되고 화면에 맞춰지는 반응형 레이아웃. 페이지:

| 페이지 | 동작 |
|------|------|
| **Dashboard** | 홈 화면 — 라이브 Pod / RAM / CPU ring 게이지 + 디스크 사용량, 자동 회복 상태 카드, pinned / recent 워크스페이스 타일, 라이브 RDP 앱 세션을 세션별 종료 버튼과 함께 나열하는 "Running sessions" 스트립, reverse-open 토글 |
| **All apps** | 설치된 앱 프로필의 grid / list view (구 "Apps"), 검색 + 카테고리 필터, 앱별 실행 (3초 cooldown), Add / Edit / Delete 앱 프로필 다이얼로그 |
| **Devices** | USB / PCI 장치 패스스루용 2열 호스트 ↔ 게스트 mover (#286) — 왼쪽에서 호스트 장치 선택, 오른쪽에서 Windows 게스트에 attach (USB 는 live hot-plug; PCI 는 안전 확인과 함께 게스트 재시작 필요) |
| **Settings** | RDP (user / IP / port / scale / DPI / 비밀번호 회전 / 멀티모니터), Container (backend / CPU / RAM / idle timeout), 그리고 reverse-open 패널 (enable 토글, allowlist + denylist, 라이브 daemon 상태, refresh / start / stop 버튼) 한 화면에 |
| **Tools** | Suspend / Resume / Full Desktop 버튼, Clean Locks / Sync Time / Debloat, Grow Disk / Sync Guest, 그리고 원클릭 Windows Update **활성/비활성** 토글 |
| **Terminal** | 명령 allowlist 제한된 embedded 셸 (`podman`, `docker`, `winpodx`, `xfreerdp`, `systemctl`, `journalctl`, `ss`, `ip`, `ping`, ...) + 퀵 버튼 (Status / Logs / Inspect / RDP Test / Clear) |
| **Info** | 라이브 **Health** 카드 (pod / RDP / agent / OEM / disk / 비밀번호 age / 앱 수) + System / Display / Dependencies / Pod / Config 스냅샷 |

시스템 트레이 (`winpodx tray`) 는 가벼운 대안 — pod 컨트롤, 앱 런처 서브메뉴 (상위 20 + Full Desktop), USB 장치 스위처 (#300, 호스트 USB 장치를 게스트에 attach / detach), 유지보수 서브메뉴 (Clean Locks / Sync Time / Suspend), 라이브 RDP 앱 세션을 종료할 수 있는 running-sessions 서브메뉴, 선택적 idle-monitor 스레드.

### Tray 자동 spawn + UNRESPONSIVE 자동 회복 (v0.5.5)

v0.5.5 부터 tray 가 GUI 창과 pod 를 건드리는 모든 CLI 서브커맨드 (`setup` / `gui` / `tray` 제외) 에서 자동 spawn — `winpodx app run` 만 쓰는 사용자도 시스템 트레이 아이콘 + UNRESPONSIVE 자동 회복 드라이버 활용 가능. `$XDG_RUNTIME_DIR/winpodx/tray.lock` flock 으로 중복 인스턴스 차단.

트레이 컨텍스트 메뉴 최상단에 **Open Dashboard** (메인 GUI 창 원클릭). **Quit** 가 다이얼로그로 확인 후 `stop_pod` + `pkill -f 'winpodx gui'` + `app.quit` 실행 — 실수 클릭으로 pod ~30초 재시작 비용 발생 방지.

매 로그인 시 tray 자동 실행하려면 GUI → Settings → **"Launch WinPodX tray at login (system tray icon + idle-stall auto-recovery)"** 체크. 토글이 XDG autostart 스펙 통해 `~/.config/autostart/winpodx-tray.desktop` 작성/삭제; KDE / GNOME / XFCE / Cinnamon 모두 portable. 파일이 source of truth — GUI 안 띄우고 손으로 떼서 opt out 가능. 토글 즉시 적용, Save Settings 클릭 불필요.

tray 가 pod 상태 30초 주기로 감시. `RUNNING → UNRESPONSIVE` 전이 시 (컨테이너가 fresh boot 와 헷갈릴 수 없을 만큼 오래 살아있는데 RDP 포트 miss) 데스크톱 알림 발사 + 백그라운드 worker 가 agent 에게 Windows `TermService` cycle 요청. 회복 시 "Pod recovered" 알림; 실패 시 "needs manual restart" 알림이 `winpodx pod restart` 안내. `install.sh` 의 `[3/4]` / `[4/4]` Sysprep + OEM 재부팅 단계 진행 중에는 marker 파일 `~/.config/winpodx/.install_in_progress` 가 회복 경로 억제 — install 단계의 정당한 RDP 공백에 spurious 알림 발사 안 함.

## 호스트 장치 패스스루

호스트 USB 또는 (GPU 가 아닌) PCI 장치를 Windows 게스트로 패스스루 (#286). 세 가지 표면이 같은 백엔드를 구동:

* **CLI** — `winpodx device list` 가 각 호스트 장치 + attach 상태 표시; `winpodx device attach <id>` / `winpodx device detach <id>` 로 하나씩 넣고 뺌.
* **GUI Devices 페이지** — 2열 호스트 ↔ 게스트 mover (왼쪽에서 선택, 오른쪽에서 attach).
* **시스템 트레이** — 호스트 USB 장치 원클릭 attach / detach 용 USB 스위처 서브메뉴 (#300).

USB 장치는 live hot-plug (`cfg.pod.usb_live`, 기본 on) — 재시작 불필요. PCI 장치는 부팅 시 추가되고 게스트 재시작 후에만 보이므로 attach 가 안전 확인으로 보호됨; CLI 에서 `--force` 전달 (또는 GUI 에서 다이얼로그 확인) 으로 진행.

## bare-metal 호환 모드 (하이퍼바이저 숨김)

일부 소프트웨어는 하이퍼바이저가 감지되면 실행을 거부합니다 — 대표적으로 Nvidia 소비자 GPU 드라이버(KVM 감지 시 **code 43**, GPU 패스스루의 흔한 차단 원인)와 런치게이트 VM 체크가 있는 앱들. `cfg.pod.disguise_level` (#246) 이 KVM/QEMU 시그니처를 게스트에서 숨겨 물리 PC 처럼 보이게 합니다. **3단계, 기본값 `balanced`**:

| 레벨 | 동작 | 성능 |
|------|------|------|
| `off` | 숨김 없음 — 정직한 VM | 최고(호환성도) |
| `balanced` (기본) | CPUID 하이퍼바이저 비트 + KVM 시그니처 제거, 호스트 SMBIOS/DMI 미러링, 합성 센서 디스크립터, 물리 PC 같은 디스크 크기 광고 | 손실 없음 |
| `max` | `balanced` + **에뮬레이트 가상 하드웨어** — 디스크 → SATA(AHCI), 네트워크 → e1000(`MTU=1500`), GPU → std VGA, virtio-rng 제거 — 그리고 `HV=N`. virtio(`VEN_1AF4`)/QXL(`VEN_1B36`) PCI ID와 `vioscsi`/`viostor`/`netkvm` 드라이버 제거. (`MTU=1500`은 dockur가 e1000이 거부하는 `host_mtu=`를 안 붙이게 하려고 필수.) **wipe+재설치 필요**(부팅 디스크 컨트롤러가 바뀌어 기존 설치 부팅 불가) → max 전환 시 강력 확인 후 Windows 처음부터 재설치. | **대폭 느려짐** — 에뮬레이트 디스크+NIC는 virtio보다 throughput 훨씬 낮음, Hyper-V enlightenment도 꺼짐 |

```bash
winpodx config set pod.disguise_level off        # 정직한 VM
winpodx config set pod.disguise_level balanced   # 기본 — 무손실 숨김, 자동 적용
winpodx config set pod.disguise_level max        # 최대 숨김 (에뮬레이트 HW, 느려짐)
# off <-> balanced 는 자동 적용됨. max 전환은 가상 하드웨어가 바뀌어 파괴적 재설치 필요:
winpodx pod recreate --wipe-storage              # Windows 초기화 후 새 하드웨어로 재설치
```

GUI에서도 선택 가능: **Settings → Bare-metal compatibility**. 어느 쪽이든 `winpodx pod recreate` 후 적용됨 (QEMU `-cpu` 라인 + `HV` env + 디스크 크기를 바꾸므로; recreate는 Windows 디스크 유지).

**고급 — 패치된 QEMU 이미지 (`cfg.pod.disguise_image`):** 일부 VM 마커(ACPI OEM `BOCHS`, 디스크 모델 `QEMU HARDDISK`)는 QEMU에 컴파일된 문자열이라 커맨드라인 인자로 못 바꿉니다. `packaging/qemu-disguise/`가 그 문자열을 패치한 커스텀 dockur 이미지를 빌드하며, `disguise_image`에 지정하면 `max`에서 사용됩니다. winpodx는 패치 레시피만 배포(패치된 바이너리 없음). PCI 벤더 ID는 일부러 안 건드림(스푸핑하면 dockur virtio-serial이 깨짐). 그 디렉터리 README 참고.

**안티치트 우회 아님.** 캐주얼 탐지기와 VM 거부 앱(code 43, DRM/런치게이트)용 시그니처 레벨 숨김입니다. 커널 안티치트(EAC/BattlEye/Vanguard)는 **못 피합니다** — 하드웨어 attestation(TPM + Secure Boot)과 게스트가 위조 못 하는 VM-exit 타이밍에 의존하며, 온라인 게임 안티치트 우회는 게임 ToS 위반입니다.

디스크 확대는 게이트됨: dockur 디스크는 sparse 라 광고 크기를 키워도 호스트 공간을 즉시 먹지 않지만, 호스트 여유 공간이 충분할 때만(10 GiB / 10 % 예약 유지) 올립니다. 작은 호스트에선 그대로 두고 경고만 출력. 구버전 `disguise_hypervisor = false` 키도 계속 작동 — `off` 로 매핑됩니다.

> **안티치트 우회가 아닙니다.** 시그니처 레벨만이며 커널모드 안티치트(EAC / BattlEye / Vanguard)는 못 뚫습니다. 온라인 게임 안티치트 우회는 ToS 위반이고 밴 위험 — winpodx 는 그 용도를 지원하지 않습니다.

## 멀티모니터

멀티모니터 RAIL 기본 on (`cfg.rdp.multimon`, 기본 `"span"`): 원격 앱 창을 두 번째 모니터로 끌어도 입력이 계속 동작. 값:

| `cfg.rdp.multimon` | 효과 |
|---|---|
| `span` (기본) | RDP 세션을 모든 모니터에 걸쳐 span — 원격 앱 창이 어느 모니터에서든 인터랙티브 유지 |
| `multimon` | FreeRDP 의 개별 `/multimon` 모드 (모니터별 geometry) 사용 |
| `off` | 단일 모니터만 |

`winpodx config set rdp.multimon off` 또는 GUI Settings 페이지로 변경.

## 헬스 체크

`winpodx doctor` 가 GUI Health 카드가 쓰는 모든 프로브를 실행하고 각각 한 줄 결과 출력:

```
=== WinPodX doctor ===

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

## Windows 비밀번호 변경

`winpodx rotate-password` 를 쓰세요 — 절대로 `winpodx setup` 으로 비밀번호 바꾸지 마세요. 이미 실행 중인 설치에 대해 두 명령의 효과는 완전히 다릅니다:

| 명령 | Host config (`winpodx.toml`) | Windows guest 계정 |
|---|---|---|
| `winpodx rotate-password` | 원자적 업데이트 (실패 시 rollback) | Windows-side 비밀번호 변경 메커니즘 통해 변경 |
| `winpodx setup` (재실행) | 그대로 보존 (v0.5.5 이상) | 변경 안 함 |
| `winpodx setup` (신규 설치, 기존 config 없음) | 생성/입력 받음 | 첫 부팅 시 dockur `USERNAME`/`PASSWORD` env var 로 적용 |

cores / RAM / `win_version` 만 바꾸려고 `winpodx setup` 재실행은 안전, 자격증명 건드리지 않음. v0.5.5 이전 릴리스에서는 wizard 가 매번 비밀번호를 reprompt 하고 `winpodx.toml` 을 조용히 덮어썼습니다 — 하지만 dockur 는 첫 부팅에만 password env var 를 적용하므로 호스트 config 와 Windows guest 계정이 desync 되어 다음 RDP launch 가 `LOGON_FAILED_BAD_PASSWORD` 로 실패했습니다.

### desync 된 비밀번호에서 복구 (v0.5.5 이전 lockout)

이전 릴리스에서 `winpodx setup` 돌리고 로그인 불가 상태라면:

1. **옛 비밀번호가 남아있다면 복원** (`winpodx.toml` 백업, 패스워드 매니저, shell history 등):
   ```bash
   winpodx config set rdp.password '<old-password>'
   winpodx pod start
   winpodx rotate-password
   ```
2. **그렇지 않으면** `winpodx uninstall --purge` + 재설치가 유일한 길. Windows 안의 모든 상태 (설치된 앱, 문서, 설정) 손실. 재설치 후 첫 단계로 `winpodx setup` 한 번 돌리고, 이후 비밀번호 변경은 절대 `setup` 으로 하지 말고 `rotate-password` 만 사용하세요.

## 성능 튜닝 프로파일

`cfg.pod.tuning_profile` 이 호스트에 대한 WinPodX 의 dockur compose 튜닝 적극성을 제어. 기본값 `"auto"` — WinPodX 가 compose 생성 시점에 호스트를 한 번 probe 하고 매칭되는 안전한 Windows-on-KVM 튜닝을 활성화. `winpodx doctor` 의 `[Tuning]` 블록에서 무엇이 감지되고 적용됐는지 확인 가능:

```
[Tuning]
  invtsc:        yes   (intel)
  io_uring:      yes   (kernel 6.18, need >= 5.6)
  hugepages:     no    (sysctl vm.nr_hugepages)
  dedicated:     yes
  nested_kvm:    yes   (/sys/module/kvm_*/parameters/nested)

  Profile: auto
    +invtsc:        yes
    io_uring aio:   yes
    hugepages:      no
    CPU pinning:    yes
    platform_tick:  yes
    no balloon:     yes
    hv-* + no-hpet: yes
    virtio-rng:     yes
    nested virt:    yes
    hv-evmcs:       yes
```

프로파일:

| `tuning_profile` | 동작 |
|---|---|
| `auto` (기본) | 호스트 capability 감지 + 호스트가 지원하는 모든 안전 튜닝 적용 (Hyper-V enlightenments, virtio-rng, `/sys/module/kvm_*/parameters/nested == Y` 시 nested-virt pass-through 포함). CPU pinning + no-balloon 은 `dedicated_host` (idle CPU + free RAM ≥ VM 할당의 2배) gate 통과 시에만 — 다른 호스트 워크로드 starve 방지. 대부분 사용자에게 권장. |
| `performance` | `auto` 와 동일하나 `dedicated_host` gate 우회: CPU pinning + no-balloon 이 호스트 현재 부하 무관하게 강제 on. 박스가 WinPodX 에 거의 dedicated 이고 다른 호스트 워크로드 희생해서라도 게스트 latency 최소화하고 싶을 때 사용. Hard-gated 항목 (`+invtsc`, `io_uring`) 은 여전히 capability 감지 존중 — `performance` 가 QEMU 가 거부할 CPU flag 나 kernel crash 일으킬 feature 를 강제할 수는 없음. |
| `safe` | 호스트 설정 무관한 Windows-guest-only 부분만 적용: `+invtsc` (지원 시), `platform_tick` BCD, Hyper-V enlightenments (`hv-relaxed`, `hv-vapic`, `hv-vpindex`, `hv-runtime`, `hv-synic`, `hv-reset`, `hv-frequencies`, `hv-reenlightenment`, `hv-tlbflush`, `hv-ipi`, `hv-spinlocks=0x1fff`, `hv-stimer`, `hv-stimer-direct`, `-no-hpet`), `virtio-rng`. 호스트 측 명시적 opt-in 필요한 nested-virt + `hv-evmcs` 는 제외. |
| `off` | 아무것도 적용 안 함; dockur 기본만 유지. 튜닝 간섭 디버깅 시 사용. |
| `manual` | `safe` 와 동일 shape; 향후 개별 knob override 용 예약. |

### 각 튜닝 설명

* **`+invtsc`** — invariant TSC 노출, Windows 가 HPET 대신 TSC 를 clock source 로 사용 (IRQ overhead 감소).
* **`hv-*` enlightenments + `-no-hpet`** (#245) — Windows 에게 paravirtualised hypervisor 환경임을 알림. spinlock / VM-exit overhead 모든 워크로드에서 감소; multi-vCPU 게스트에서 효과 큼. `hv-spinlocks=0x1fff` 은 upstream 권장 retry 한도.
* **`virtio-rng-pci` (`/dev/urandom` backed)** (#245) — Windows 엔트로피 풀 빠르게 채움, 첫 부팅 시 CryptoAPI / TLS handshake stall 방지.
* **`+vmx` / `+svm` nested virt** (#245) — `/sys/module/kvm_intel/parameters/nested` 또는 `kvm_amd` 가 `Y` 일때 자동 활성화. Windows 게스트 안에서 Hyper-V / WSL2 / Docker Desktop 실행 필수. 호스트 kernel 미 opt-in 시 무영향.
* **`hv-evmcs`** (#245) — Intel 전용 nested-VMCS 최적화, `+vmx` 와 페어. nested VM 미실행 시 overhead zero.
* **`io_uring` AIO** — kernel ≥ 5.6 디스크 I/O backend; 기존 thread 보다 latency 낮음.
* **Hugepages** — QEMU 메모리를 2 MB 페이지로 backing. 호스트 `vm.nr_hugepages` 예약 필요 (WinPodX 자동 예약 없음).
* **CPU pinning** — 호스트 idle CPU + RAM ≥ VM 할당의 2배일때 `dedicated` flag 세움, QEMU vCPU pinning 적용.

### One-shot override

`winpodx pod start --tuning {auto,safe,off,manual}` 이 컨테이너 실행 동안만 `cfg.pod.tuning_profile` 을 override. `winpodx.toml` 의 사용자 영구 설정은 그대로. A/B 테스트 시 `winpodx config set` round-trip 없이 왔다 갔다 가능.

### 호스트 사전 설정 필요 항목 (자동 적용 안 됨)

다음은 Linux 호스트에서 운영자가 사전 작업해야 WinPodX 가 활용 가능한 표준 Windows-on-KVM 튜닝. 호스트 미설정 시 `winpodx doctor` 의 `[Tuning]` 블록에 `no` 로 표시; 호스트 설정 후 다음 `cfg.pod.tuning_profile = auto` 실행 시 자동 `yes`.

* **Transparent hugepages / explicit hugepages.** `sysctl vm.nr_hugepages` 설정 (또는 `madvise` THP 사용) 으로 QEMU 프로세스 메모리 hugepage backing. WinPodX 가 `/proc/meminfo` 의 `HugePages_Total > 0` 감지 시 auto-apply, 미예약 시 skip.
* **CPU pinning.** WinPodX 가 현재 idle CPU + RAM 이 VM 할당량의 2배 이상일 때 호스트를 `dedicated` 로 flag. QEMU 스레드를 특정 코어에 `taskset` 또는 systemd `CPUAffinity=` 으로 pin 하는 건 운영자 책임; WinPodX 는 호스트 스케줄링 수정 안 함.
* **VFIO GPU passthrough.** RDP 기반 WinPodX 아키텍처에 scope 밖. (GPU 가 아닌 USB / PCI 장치 패스스루는 지원됨 — 아래 "호스트 장치 패스스루" 참고.) 베어메탈 GPU 성능 필요 시 직접 GPU 패스스루 Windows VM (예: libvirt / virt-manager) 을 띄우고 `manual` 백엔드로 그 RDP 엔드포인트에 WinPodX 를 연결.

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
multimon = "span"            # 멀티모니터 RAIL: span | multimon | off
extra_flags = ""             # 추가 FreeRDP 플래그 (allowlist); 예:
                             #   "+multitouch" — 터치스크린 / 스타일러스 / 펜을
                             #   Windows 앱에 패스스루 (#623)
                             #   "-gfx" — 레거시 GDI 경로 (RAIL 렌더 워크어라운드)

[pod]
backend = "podman"
win_version = "11"                               # 11 | 10 | ltsc11 | ltsc10 | iot11 | tiny11 | tiny10 | 2025 | 2022 | 2019 | 2016 — 커스텀 ISO 는 ARCHITECTURE.md 참고
keyboard = "en-US"                               # Windows 설치 로케일; FreeRDP 세션 레이아웃(/kbd:layout)으로도 매핑돼 비-US 키보드가 RemoteApp 창에서 동작 (#660)
cpu_cores = 4
ram_gb = 4
vnc_port = 8007
auto_start = false                               # opt-in 로그인 자동시작: 로그인 시 트레이가 pod 시작 (`winpodx autostart on|off|status` 로 토글)
idle_timeout = 0                                 # 자동 suspend 까지 초 (0 = 비활성)
boot_timeout = 300                               # 첫 부팅 unattended 설치 대기 초
image = "docker.io/dockurr/windows:latest"       # 컨테이너 이미지 (에어갭 미러용 override)
usb_live = true                                  # attach 된 USB 장치를 실행 중 게스트에 hot-plug (재시작 없이) — `winpodx device` 참고
# disguise_level = "balanced"                    # bare-metal 모드: off | balanced(기본, 무손실 숨김) | max(Hyper-V 끔, 느려짐) — Nvidia code-43 / VM 거부 앱; 안티치트 우회 아님 (#246)
disk_size = "64G"                                # dockur 에 전달하는 가상 디스크 크기 (`install grow-disk` 로 확장)
disk_autogrow = true                             # C: 가 임계 넘으면 자동 확장 (idle 일 때만)
disk_autogrow_threshold_pct = 80                 # 자동 확장 트리거 사용% (50-99)
disk_autogrow_target_free_pct = 30               # 확장 후 회복할 여유 비율 (고정 step 아님)
disk_autogrow_increment = "32G"                  # 확장 granularity / 최소 step
disk_max_size = ""                               # 선택적 상한; 빈값 = host 여유공간만이 한계
guest_autosync = true                            # host 업데이트 후 guest 아티팩트 자동 푸시 (재설치 없이)

[ui]
language = "auto"                                # UI 언어: auto | en | ko | zh | ja | de | fr | it (auto = 호스트 로케일, 영어로 폴백; `winpodx language` 또는 GUI 설정으로 변경)

[desktop]
mime_associations = true                         # 탐지된 앱이 파일 관리자 "다른 프로그램으로 열기"에 실제 파일 타입 제공 (#545); 기본 핸들러로 설정 안 함
full_app_scan = false                            # false = 시작메뉴 전용 검출(깨끗한 메뉴, 폴더 그룹화); true = 레지스트리 App Paths / Chocolatey / Scoop / 전체 UWP 도 스캔 (#581) — 시작메뉴 항목 없는 포터블 앱용

[reverse_open]
enabled = true                                   # v0.5.0 부터 기본 활성
allow = []                                       # 비어있으면 발견된 모든 앱
deny = []                                        # manifest 에서 제외할 앱

[logging]
level = "INFO"                                   # DEBUG | INFO | WARNING | ERROR | CRITICAL | RAW — RAW = DEBUG + pod 로그 (podman logs -f) 를 GUI Terminal 에 interleave
```

`winpodx config set <key> <value>` 또는 에디터로 직접 수정 — TOML 은 3.11+ 에서 stdlib (`tomli` on 3.9/3.10) 로 파싱.
