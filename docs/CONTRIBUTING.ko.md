# WinPodX 기여 가이드

[English](../CONTRIBUTING.md) | **한국어**

WinPodX에 관심을 가져 주셔서 감사합니다! 이 가이드는 기여를 시작하는 데 도움을 드립니다.

## 사전 요구 사항

- Python 3.9+ (3.13 에서 개발; CI 는 3.9 / 3.10 / 3.11 / 3.12 / 3.13 매트릭스)
- FreeRDP 3+

## 빌드

```bash
git clone https://github.com/kernalix7/winpodx.git
cd winpodx
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 테스트

```bash
# 테스트 실행
pytest tests/ -v

# 린트
ruff check src/ tests/

# 포맷 검사
ruff format --check src/ tests/
```

## 워크플로우

1. 저장소를 **포크**합니다
2. **기능 브랜치**를 생성합니다 (`git checkout -b feat/my-feature`)
3. **Conventional Commits** 규칙에 따라 변경 사항을 작성합니다
4. **Pull Request**를 제출합니다

## PR 체크리스트

PR을 제출하기 전에 다음을 확인하세요:

- [ ] `pytest tests/ -v` 통과
- [ ] `ruff check src/ tests/` 오류 없음
- [ ] `ruff format --check src/ tests/` 통과
- [ ] 문서 업데이트 완료 (해당하는 경우)
- [ ] 하드코딩된 자격 증명 또는 비밀 정보 없음

## 커밋 규칙

이 프로젝트는 [Conventional Commits](https://www.conventionalcommits.org/)를 따릅니다:

| 접두사 | 용도 |
|--------|------|
| `feat` | 새로운 기능 |
| `fix` | 버그 수정 |
| `docs` | 문서 변경 |
| `refactor` | 코드 리팩토링 (기능 변경 없음) |
| `test` | 테스트 추가 또는 업데이트 |
| `chore` | 유지보수 작업 (CI, 의존성 등) |

### 예시

```
feat: add Wayland display detection
fix: resolve DPI scaling on multi-monitor setups
docs: update installation instructions
refactor: simplify backend abstraction layer
test: add unit tests for UNC path conversion
chore: update ruff to 0.8.x
```

### AI 툴 co-author 트레일러 금지

`Co-authored-by:` 트레일러에 AI 툴 / 코딩 에이전트 이름을 넣지 **마세요**. 다음 모두 해당:

- `Co-authored-by: Cursor <cursoragent@cursor.com>`
- `Co-authored-by: Claude <noreply@anthropic.com>` (및 다른 Anthropic 이메일)
- `Co-authored-by: Copilot <...>` (GitHub Copilot 모든 변종)
- `Co-authored-by: <기타 AI 툴 / 에이전트 정체성>`

패치를 작성한 건 당신입니다 — 정식 사람 author 는 당신. AI 툴이 얼마나 기여했든 이 repo 에서 co-author credit 받지 않습니다. 깜빡하고 트레일러가 들어갔다면 amend 요청하거나 (이미 머지된 PR 의 경우) 후속 PR 로 조정된 history-rewrite 를 제안합니다.

사람 co-author (예: 변경 사항을 함께 페어 프로그래밍한 동료) 는 환영 — 실제 사람 정체성 + 이메일 사용.

## 릴리스 노트 작성

`CHANGELOG.md` (그리고 `docs/CHANGELOG.ko.md`) 의 각 버전 섹션은 `### Highlights` 로 시작합니다 — 한 줄 헤드라인 + 3–6 개 스캔 가능한 bullet. 이것이 GitHub 릴리스 페이지 맨 위에 보이는 내용입니다. `release.yml` 이 해당 버전 섹션을 verbatim 으로 추출해서 릴리스 body 에 넣기 때문에, 섹션 맨 앞에 있는 게 릴리스 본문 맨 앞에 옴.

자세한 `### Added` / `### Changed` / `### Fixed` bullet 은 그 아래에. archeology 와 exhaustive tracking 용이지 first-read 용이 아님.

스켈레톤:

```markdown
## [X.Y.Z] - YYYY-MM-DD

### Highlights

**한 줄 헤드라인.** 필요시 1-2 문장 추가 컨텍스트.

- 가장 중요한 사용자 가시적 변경 (한 줄, 스캔 가능)
- 두 번째 중요한 변경
- (최대 3-6 bullets; prose 블록 금지)

### Added
- (자세한 bullet)

### Changed
- (자세한 bullet)

### Fixed
- (자세한 bullet)
```

릴리스 컷할 때 `REL-vX.Y.Z` marker tag 도 같이 푸시 — 이것이 `release.yml` 을 fire 시킴 (`wheel` + `sdist` 빌드, CHANGELOG 섹션 추출, GitHub 릴리스 body 갱신). REL- marker 없으면 버전 태그 (`vX.Y.Z`) 가 4개 패키징 워크플로우 (`obs-publish.yml`, `rhel-publish.yml`, `debs-publish.yml`, `aur-publish.yml`) 만 trigger 하고 `wheel` / `sdist` 없음 + 자동 추출 릴리스 body 없음.

```bash
git tag vX.Y.Z <commit>
git tag REL-vX.Y.Z vX.Y.Z^{}    # nested-tag 경고 피하려고 commit 으로 dereference
git push origin vX.Y.Z REL-vX.Y.Z
```

### Highlights 에서 기여자 표기

Highlights bullet 이 메인테이너 외부에서 온 작업 (외부 PR / 외부 버그
리포트 / feature request) 을 다룰 때, 인라인으로 기여자 크레딧을 표기.
컨벤션:

| 출처 | 접미사 |
|---|---|
| 외부 PR (다른 사람의 커밋) | `(by @username, #PR)` |
| 외부 이슈 / feature request (코드는 메인테이너 작성) | `(reported by @username, #issue)` |
| 둘 다 — 같은 사람의 외부 리포트 **+** 외부 PR | `(by @username, #PR / #issue)` |

GitHub 가 두 형식 모두 릴리스 페이지에 user 아바타 + 핸들로 자동
렌더링 — 추가 작업 없이 크레딧이 노출됨.

예시:

```markdown
### Highlights

- Atomic Fedora flavours (Silverblue / Kinoite / Bazzite) now ship via the
  OBS repo with `rpm-ostree install --apply-live`. (by @Zeik0s, #163)
- LTSC IoT and Win10 LTSC pickable from Settings or `--win-version`.
  (reported by @gabe39, #178)
- Dynamic Desktop Window Resolution — Full Desktop sessions now resize
  the FreeRDP client window automatically. (by @Zeik0s, #202)
- Ubuntu 26.04 build target + Wayland-friendly Recommends split.
  (by @juampe, #206)
```

위의 "AI tool co-author trailers 금지" 룰과는 무관: 그것은
기계 생성 attribution 금지. 사람 기여자는 자유롭고 명시적으로
크레딧.

## 보안

보안 취약점을 발견한 경우, [SECURITY.ko.md](SECURITY.ko.md)에 설명된 절차를 따라 주세요. **공개 이슈를 열지 마세요.**
