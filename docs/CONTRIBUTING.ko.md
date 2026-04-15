# winpodx 기여 가이드

[English](../CONTRIBUTING.md) | **한국어**

winpodx에 관심을 가져 주셔서 감사합니다! 이 가이드는 기여를 시작하는 데 도움을 드립니다.

## 사전 요구 사항

- Python 3.11+
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

## 보안

보안 취약점을 발견한 경우, [SECURITY.ko.md](SECURITY.ko.md)에 설명된 절차를 따라 주세요. **공개 이슈를 열지 마세요.**
