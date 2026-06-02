# 비교

[English](COMPARISON.md) | **한국어**

Linux 에서 Windows 앱을 실행하기 위한 다른 도구들과 WinPodX 의 비교.

## 왜 WinPodX인가?

Linux 에서 Windows 앱을 실행하는 기존 도구들은 각각 한계가 있습니다:

| | winapps | LinOffice | winboat | WinPodX |
|---|---|---|---|---|
| 핵심 기술 | RDP 가능한 Windows 호스트 (클라우드 / 물리 / 컨테이너) + FreeRDP | dockur + FreeRDP | dockur + FreeRDP | dockur (Podman) + FreeRDP + HTTP guest agent |
| 설정 | 수동 (셸 + 설정 파일 + RDP 테스트) | 원라인 스크립트 | 원클릭 GUI 설치 | **제로 설정** (첫 실행 시 자동) |
| 인터페이스 | CLI 만 | CLI 만 | Electron GUI | **Qt6 GUI + CLI + 트레이** |
| 앱 범위 | 모든 Windows 앱 | Office 전용 | 모든 Windows 앱 | 모든 Windows 앱 |
| 언어 | Shell (86%) | Shell + Python | TypeScript / Vue / Go | **Python (100%)** |
| 런타임 의존성 | curl, dialog, git, netcat | Podman, FreeRDP | Electron, Docker/Podman, FreeRDP | **Python 3.9+, FreeRDP, Podman** |
| 자동 suspend / resume | 없음 | 없음 | 문서화 안 됨 | **있음 (idle timeout)** |
| 비밀번호 회전 | 없음 | 없음 | 문서화 안 됨 | **있음 (7일, atomic)** |
| HiDPI 자동 감지 | 없음 | 없음 | 문서화 안 됨 | **GNOME, KDE, Sway, Hyprland, Cinnamon, xrdb** |
| 사운드 기본 | 없음 | 없음 | 있음 (FreeRDP) | 있음 (FreeRDP) |
| 프린터 리디렉션 기본 | 없음 | 없음 | 문서화 안 됨 | 있음 (FreeRDP) |
| USB 드라이브 자동 매핑 | 없음 | 없음 | 스마트카드 패스스루 | **드라이브 서브폴더 → 드라이브 레터, FileSystemWatcher** |
| Discovery (설치된 앱 자동 스캔) | 없음 | 없음 | 있음 | **있음 (Registry + Start Menu + UWP + choco/scoop)** |
| 멀티세션 RDP | 없음 | 없음 | 문서화 안 됨 | **있음 (bundled rdprrap, 최대 10)** |
| Reverse 파일 열기 (guest → host xdg-open) | 없음 | 없음 | 없음 | **있음 (Linux 앱이 Windows "Open with…" 메뉴에 노출)** |
| Windows 디스크 자동 확장 | 없음 | 없음 | 없음 | **있음 (idle, 호스트 여유 공간으로 bounded)** |
| 게스트 동기화 (재설치 없는 in-place 업데이트) | 없음 | 없음 | 없음 | **있음 (pod 시작 시 자동 + `sync-guest`)** |
| 다국어 UI | 영어 전용 | 영어 전용 | 영어 전용 | **있음 (7개 언어, 로케일 자동 감지)** |
| 오프라인 / 에어갭 설치 | 없음 | 없음 | 없음 | **있음 (`--source` + `--image-tar`)** |
| 라이선스 | MIT | AGPL-3.0 | MIT | MIT |

> winboat 가 스코프상 가장 가까운 peer 이고 영감을 준 프로젝트. 우리는 다른 조합에 집중 — Electron 대신 stdlib 지향 Python + Qt6, 더 깊은 자동 설정 (auto suspend, 7일 비밀번호 회전, 다중 DE HiDPI), reverse-open (Linux 앱이 Windows "Open with…" 메뉴에 기본 노출되는 유일한 프로젝트), 다국어 UI (7개 언어, 로케일 자동 감지), 채워질수록 스스로 커지는 자가 관리 Windows 디스크, 재설치 없이 실행 중인 게스트로 호스트 업데이트를 push 하는 in-place 게스트 동기화, 명시적 에어갭 설치 경로. 두 프로젝트 모두 dockur/windows 위에 빌드 — 그 생태계는 한 앱보다 크다.

## WinPodX vs Wine

**WinPodX 는 Wine 의 대체재가 아닙니다.** Wine 은 Windows API 호출을 변환; WinPodX 는 실제 Windows OS 를 컨테이너에서 실행. 둘은 다른 문제를 해결하고, 많은 사용자가 둘 다 설치합니다.

| 필요한 것... | 사용 |
|---|---|
| 구형 Win32 앱, 인디 게임, 가벼운 유틸리티 | **Wine / Bottles / Lutris** |
| GPU 가속 게임 / 3D 앱 (DirectX 9 – 12) | **Wine** — DXVK / VKD3D 가 거의 네이티브 프레임레이트 제공. WinPodX 는 기본적으로 GPU 패스스루 없음; QEMU CPU 렌더링은 훨씬 느림. (VFIO 통한 GPU 패스스루는 수동 BYO 설정 — 패키징 안 됨.) |
| Microsoft 365 with 완전한 Outlook + Teams + OneDrive 통합 | **WinPodX** |
| Adobe Creative Suite (Photoshop, Illustrator, Premiere, Lightroom) | WinPodX — 단 무거운 GPU 이펙트는 CPU 바운드 (위 GPU 행 참조) |
| 안티치트 게임 (Valorant, EAC, BattlEye) | **TBD** — 안티치트마다 VM 감지 정책 다름 (Vanguard 는 TPM 2.0 + hypervisor 없음 필요, EAC 는 대부분 VM 차단, VAC 는 관대). 시도 전 테스트 필수. |
| DRM 무거운 소프트웨어 / 하드웨어 동글 앱 | **WinPodX** |
| 커널 모드 드라이버 출시 앱 (일부 VPN, 보안 스위트) | **WinPodX** |
| 지역 인증서 사용 은행 / 세무 / 행정 도구 | **WinPodX** |
| Visual Studio, WinUI 3 / WinRT, Wine 이 따라잡지 못한 .NET 기능 | **WinPodX** |
| IE 전용 레거시 엔터프라이즈 웹 앱 | **WinPodX** |
| "대부분 동작" 이 허용 안 되는 모든 것 | **WinPodX** |

Wine 은 속도와 GPU 에서 (DXVK/VKD3D 변환이 깔끔하게 될 때) 이김. WinPodX 는 그 외 모든 곳에서 **100% Windows 기능 동등성** 으로 이김 — 모든 앱이 실제 Windows 커널 위에서 실행되고, FreeRDP RemoteApp 통해 Linux 데스크톱에 네이티브 윈도로 렌더링됨.
