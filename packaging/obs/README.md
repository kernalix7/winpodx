# OBS 배포 가이드 (winpodx)

openSUSE Build Service에 winpodx를 올려서 Tumbleweed, Fedora, Leap용 RPM을
자동 빌드하고, GitHub Release에 첨부하는 절차입니다.

- 프로젝트: https://build.opensuse.org/package/show/home:Kernalix7/winpodx
- GitHub Actions 연동: 토큰(`OBS_TOKEN`) 한 개만 필요, 비밀번호 불필요

## 1. 초기 세팅 (최초 1회, 로컬)

### 1-1. osc 설치 & 로그인

```
sudo zypper install osc      # openSUSE
sudo dnf install osc         # Fedora
sudo apt install osc         # Ubuntu/Debian

osc ls home:Kernalix7
# 첫 실행 시 ~/.oscrc 생성 프롬프트: apiurl=https://api.opensuse.org, user=Kernalix7
```

### 1-2. 패키지 파일 업로드

OBS 는 RPM + AppImage 만 담당합니다. Debian / Ubuntu `.deb` 는
공용 OBS 의 debtransform 제약 때문에 GitHub Actions
(`.github/workflows/debs-publish.yml`) 에서 `dpkg-buildpackage` 로 빌드해
Release 에 직접 첨부합니다. `debian/` 디렉터리는 프로젝트 루트에 있고
Actions 가 그대로 사용합니다.

```
cd /tmp
osc checkout home:Kernalix7 winpodx
cd home:Kernalix7/winpodx

PRJ=~/Desktop/00_Personal_Project/00G_winpodx
cp $PRJ/packaging/rpm/winpodx.spec     .
cp $PRJ/packaging/obs/_service         .
cp $PRJ/packaging/appimage/winpodx.yml .

osc add winpodx.spec _service winpodx.yml
osc ci -m "winpodx: rpm + appimage recipes"
```

`osc ci` 가 `_service` 체인을 서버에서 실행 → GitHub `main` HEAD에서
tarball을 가져오고, `@PARENT_TAG@`로 버전을 자동 해석합니다.

### 1-3. 빌드 대상 배포판 추가

웹 UI 패키지 페이지의 Repositories 탭에서 Add repository. 전 배포판 커버:

- **RPM**: openSUSE Tumbleweed, Leap 15.6, Leap 16.0, Slowroll,
  Fedora 42, Fedora 43 (모두 x86_64 만, spec 이 `noarch`)
- **AppImage**: AppImage 전용 리포 (x86_64)

Debian_12 / Debian_13 / xUbuntu_25.04 / xUbuntu_25.10 / xUbuntu_26.04 리포는
전부 **비활성화** 하세요. GitHub Actions 에서 빌드합니다.

Factory ARM/PowerPC/RISCV/zSystems 는 noarch 라 중복이므로
꺼두는 걸 권장. RISCV 는 현재 상당수 패키지가 queue 에서 blocked 상태라
빌드가 몇 주씩 걸릴 수 있습니다.

### 1-3-1. 배포판별 주의사항

- **Leap 16.0**: `python311` 이 빠졌습니다. spec 이 `sle_version >= 160000`
  조건으로 `python313` 을 자동 사용합니다.
- **Fedora 42**: `python3-pluggy` / `python3-pluggy1.3` 선택 충돌이 있어
  spec 에서 `python3-pluggy` 를 명시적으로 pin 했습니다.
- **AppImage**: `packaging/appimage/winpodx.yml` 레시피. Qt6/PySide6 를
  번들링하지만, FreeRDP 와 Podman 은 호스트에 설치되어 있어야 동작합니다
  (RDP 서버/컨테이너가 없으면 어차피 실행 불가).

### 1-4. 첫 빌드 확인

```
osc results home:Kernalix7 winpodx
osc buildlog home:Kernalix7 winpodx openSUSE_Tumbleweed x86_64
```

모두 `succeeded` 확인.

## 2. GitHub Actions 연동

### 2-1. OBS 토큰 발급 (runservice 전용)

로컬에서 한 번만:

```
osc token --create --operation runservice home:Kernalix7 winpodx
```

출력된 토큰 문자열을 복사.

### 2-2. GitHub Secret 등록

리포 → Settings → Secrets and variables → Actions → New repository secret:

| 이름 | 값 |
|------|-----|
| `OBS_TOKEN` | 위에서 발급받은 토큰 |

비밀번호는 등록하지 않습니다. 토큰은 `runservice` 작업만 허용되어
다른 프로젝트나 계정 설정은 건드릴 수 없습니다.

## 3. 릴리즈 자동화 흐름

`.github/workflows/obs-publish.yml`이 담당합니다.

1. GitHub에서 새 태그(`v0.2.0`) 푸시 → `release.yml` 실행 → GitHub Release 생성
2. Release publish 이벤트로 `obs-publish.yml` 자동 시작
3. OBS `trigger/runservice` 호출 (`OBS_TOKEN`) → `_service`가 main 최신을 가져와
   `@PARENT_TAG@`로 `winpodx-0.2.0.tar.gz` 생성 후 빌드
4. 공용 API `/build/{project}/_result` 로 30초마다 상태 폴링 (최대 60분)
5. 빌드 성공 시 `download.opensuse.org`에서 RPM 직접 다운로드
6. `gh release upload` 로 같은 Release에 RPM 첨부

## 4. 최종 사용자 설치

openSUSE (zypper):

```
zypper addrepo https://download.opensuse.org/repositories/home:/Kernalix7/openSUSE_Tumbleweed/home:Kernalix7.repo
zypper refresh
zypper install winpodx
```

Fedora (dnf):

```
dnf config-manager --add-repo https://download.opensuse.org/repositories/home:/Kernalix7/Fedora_41/home:Kernalix7.repo
dnf install winpodx
```

저장소 URL은 OBS 패키지 페이지의 Download package 링크에서 확인 가능합니다.

## 5. 주의: main 브랜치 추적 방식

`_service`의 `<param name="revision">main</param>` 때문에 OBS는 태그가 아닌
main HEAD를 가져옵니다. 버전은 `@PARENT_TAG@`로 최신 도달 가능 태그가
들어갑니다. 태그 푸시 직후 main에 새 커밋이 들어가면 tarball 내용이
태그 시점과 달라질 수 있으니, 릴리즈는 태그 → Actions 트리거가 끝난 뒤에
다음 작업을 하세요.

태그 단위로 정확히 고정하고 싶다면 `_service`의 revision을 해당 태그로
수동 교체(`osc ci`)해야 하며, 이 경우 비밀번호 기반 인증이 필요합니다.
