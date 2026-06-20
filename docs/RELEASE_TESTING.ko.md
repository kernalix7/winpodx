<!-- SPDX-License-Identifier: MIT -->
# 릴리즈 테스트 체크리스트

[English](RELEASE_TESTING.md) | **한국어**

자동 테스트가 못 보는 회귀를 릴리즈에 실어 보내지 않기 위한 반복 가능한 점검.
winpodx 버그 대부분은 **게스트측** — Windows VM, FreeRDP/RAIL, OEM 스크립트,
설치 흐름 — 인데 Linux CI 러너의 `pytest`로는 이걸 실행할 수 없습니다. 이
체크리스트는 그 수동·실윈도우 부분을 기억에 의존하지 않고 명시화합니다.

> **핵심 규칙:** 게스트를 건드리는 변경(`config/oem/`, `scripts/windows/`,
> reverse-open shim, `compose` 포트/QEMU args, 에이전트, install.bat, discovery,
> RAIL 실행)은 **머지 전 실제 Windows 게스트로 스모크 필수** — `pytest`만으론
> 부족. CI를 통과하고 실윈도우 스모크 없이 머지돼 깨진 릴리즈가 여러 번
> 있었습니다(media_monitor #613/#638, 4445/`USER_PORTS` 포트 버그 #616).

## 언제 돌리나

- **태그 전** — 기능 체크리스트 해당 섹션 + 최소 1회 fresh 설치 스모크.
- **게스트측 변경 후, 머지 전** — 건드린 표면의 게스트측 스모크 (CI가 못 잡는 걸 잡는 게이트).
- **`install.sh` / `compose` / OEM 변경 후** — 깨끗한 머신의 설치 + 기존 머신 위 업그레이드 둘 다.

## 1. 자동 게이트 (CI — green 필수)

모든 PR에서 돌아감; floor이지 전부가 아님.

- [ ] `lint` — `ruff check` + `ruff format --check`
- [ ] `test (3.9 … 3.14)` — 지원 Python 전체 `pytest tests/ -v`
- [ ] `audit` — `pip-audit`
- [ ] `discover-apps-ps` — PowerShell discovery 스크립트 문법
- [ ] `verify_versions` — `pyproject.toml` ↔ `packaging/rpm/winpodx.spec` ↔ 설치 메타데이터 일치

로컬 pre-push (CI lint와 동일, 전체 트리 — per-file 아님):
`ruff check src/ tests/ && ruff format --check src/ tests/ && pytest tests/ -q`

## 2. 게스트측 스모크 (실윈도우 — CI 불가)

실제 설치본에서. 각 단계 후 `winpodx doctor`가 빠른 헬스 게이트.

### 설치 / 업데이트
- [ ] **Fresh 설치** 완주: `curl … install.sh | bash -s -- --main` → `Provisioning complete`
      (`[3/4]`/`[4/4]` hang 없음, `Invalid port` 없음, 에이전트 up).
- [ ] **기존 위 업데이트** (`--main` 재실행): compose 재생성, 멈춘 pod recreate+기동,
      apply-fixes 실행(`guest_share: ok` 등) — `Skipping` 없음.
- [ ] **`--ref <branch>`** 가 브랜치 **최신** 커밋 설치(`git -C ~/.local/bin/winpodx-app log -1`로
      실제 갱신 확인 — 재실행이 업데이트했다고 가정 금지).
- [ ] `apply_fixes: N/N fixes OK`(현재 7) + `discovery: N apps` + `reverse_open: ok`.

### 앱 / RDP / RAIL
- [ ] `winpodx app run desktop` — 풀 데스크탑 렌더.
- [ ] `winpodx app run <app>` — RAIL 창 표시(자체 창, 작업표시줄 항목), 로그온/잠금화면 아님,
      `Invalid appWindow` 깨짐 없음.
- [ ] `winpodx app refresh` — `/exec timed out` 없이 완료(느린/콜드 게스트 포함).
- [ ] 다중 앱 창 / 멀티세션(rdprrap) 동작.

### Reverse-open (#616) — KDE 호스트
- [ ] `\\tsclient\home` 아래 호스트 파일 → *연결 프로그램* Linux 앱 → 열림.
- [ ] **게스트-로컬 파일**(Windows 데스크탑 `C:\Users\…`) → *연결 프로그램* Linux 앱 →
      호스트에서 열림, 편집 저장됨. (kio-fuse 필요; `winpodx doctor`의 `guest_mount`.)

### 대시보드 / GUI / 트레이
- [ ] 대시보드 Pod / CPU / **RAM** / **Disk** 게이지 전부 숫자 표시(`n/a` 아님).
- [ ] Settings → **UI Language** 인터페이스 전환; **Idle Action**(Pause/Stop) 존재.
- [ ] 트레이 아이콘 표시; 서브메뉴(세션 / USB) KDE Plasma에서 열림.

### 전원 / idle / 장치 / 위장 / debloat
- [ ] Idle **Pause**(기본) suspend + 실행 시 자동 resume.
- [ ] Idle **Stop**(`pod.idle_action=stop`) pod 정지(RAM 해제); 다음 실행 콜드 부팅.
- [ ] `winpodx device` USB attach/detach(live hot-plug).
- [ ] 위장(`pod.disguise_level balanced|max`) 부팅 + RDP 렌더(#557 블랙스크린 없음).
- [ ] `winpodx debloat` + undo가 활성화/업데이트 안 깨고 실행.
- [ ] `winpodx rotate-password` 호스트 config ↔ 게스트 계정 동기 유지.

## 3. 플랫폼 / 채널 매트릭스

분기하는 표면을 spot-check; 매 릴리즈 전수는 불필요하되 주기적으로 돌아가며 커버.

| 축 | 커버 |
|------|-------|
| 설치 채널 | pip/curl · AppImage · AUR · RPM(Fedora/openSUSE/AlmaLinux) · `.deb`(Debian/Ubuntu) |
| 데스크탑 | KDE Plasma · GNOME (주의: reverse-open 게스트-디스크는 KDE/kio-fuse 전용) |
| 디스플레이 | Wayland(XWayland RAIL) · X11 |
| 백엔드 | Podman(기본) · Docker |

## 4. 릴리즈 sign-off

- [ ] `pyproject.toml` + `packaging/rpm/winpodx.spec` + `debian/changelog` 버전 범프
      (`python scripts/ci/verify_versions.py` → consistent).
- [ ] `CHANGELOG.md` **및** `docs/CHANGELOG.ko.md`: `[X.Y.Z] - <날짜>` +
      **### Contributors** 섹션(모든 외부 리포터/기여자 감사 — `gh issue view <N>` 작성자,
      maintainer 제외).
- [ ] `README.md` + `docs/README.ko.md` "active development" 줄 + 요약 갱신.
- [ ] 릴리즈 커밋 CI green.
- [ ] **양쪽 태그** push: `vX.Y.Z`(publish 워크플로: OBS / RHEL / deb / AUR / AppImage)
      **및** `REL-vX.Y.Z`(Release 워크플로 → CHANGELOG 섹션에서 GitHub 릴리즈 본문 생성).
- [ ] GitHub 릴리즈 게시 — **Contributors** 섹션 + 전체 자산(wheel, sdist, AppImage, RPM, deb).
- [ ] 수정된 이슈에 "shipped in vX.Y.Z" 코멘트; 리포터 미해결 질문 없는 건 닫기.
