협업 시 코드 변경 이력을 쉽게 파악하고 관리하기 위한 커밋 메시지 규칙 적용
`제목(type)`, `본문(body)`, `꼬리말(footer)`로 구성 및 빈 행으로 구분

 **📌 1. 커밋 타입(Type):** 커밋의 목적을 나타내는 태그, 주로 영어 `소문자`로 작성

---

**`feat`**: 새로운 기능 추가
**`fix`**: 버그 수정
**`docs`**: 문서 수정 (README 등)
**`style`**: 코드 포맷팅, 세미콜론 누락 등 (코드 변경 없음)
**`refactor`**: 코드 리팩토링 (기능 추가/수정 제외)
**`test`**: 테스트 코드 추가/수정
**`chore`**: 빌드 업무 수정, 패키지 매니저 설정 등

- 예시
    
    ```python
    **feat**
    새로운 기능 추가
    git commit -m "feat: 사용자 로그인 API 추가"
    git commit -m "feat: 장애 로그 분석 에이전트 구현"
    git commit -m "feat: RAG 기반 문서 검색 기능 추가"
    ****
    ```
    
    ```python
    **fix** 
    버그 수정**(오타포함)**
    git commit -m "fix: JWT 토큰 만료 검증 오류 수정"
    git commit -m "fix: 로그 파싱 시 NullPointer 예외 해결"
    ```
    
    ```python
    **chore**
    설정 파일, 빌드, 의존성
    git commit -m "chore: requirements.txt 업데이트"
    git commit -m "chore: Docker 설정 추가"
    git commit -m "chore: GitHub Actions CI 구성"
    ```
    
    ```python
    
    **docs**
    문서 수정
    git commit -m "docs: README 실행 방법 추가"
    git commit -m "docs: API 명세서 업데이트"
    ```
    
    ```python
    **style**
    코드 스타일만 수정
    git commit -m "style: import 정렬 및 코드 포맷팅 적용"
    git commit -m "style: 불필요한 공백 제거"
    ```
    
    ```python
    **refactor**
    기능 변화 없이 구조 개선
    git commit -m "refactor: 서비스 계층 로직 분리"
    git commit -m "refactor: 에이전트 실행 로직 모듈화"
    ```
    
    ```python
    **test**
    테스트 추가/수정
    git commit -m "test: 로그인 API 단위 테스트 추가"
    git commit -m "test: RAG 검색 결과 검증 테스트 작성"
    ```
    

---

**📝 2. 커밋 제목 (Subject)**변경 사항에 대한 요약으로 50자 이내로 작성

**명령문 사용**: 과거형(‘수정함’) 대신 명령형(‘수정’)으로 작성

**마침표 생략**: 문장 끝에 마침표(.)나 느낌표(!)를 붙이지 않음

**예시**: `feat: 로그인 화면 UI 추가` 

**📖 3. 커밋 본문 (Body)**선택 사항이지만, 무엇을 왜 수정했는지 상세히 작성할 때 사용

제목과 빈 줄을 하나 둔 후 시작

어떻게(How) 보다는 무엇(What)과 왜(Why)를 작성

**🏷️ 4. 꼬리말 (Footer)**선택 사항으로, 이슈 트래커 ID나 참고 정보 등을 작성할 때 사용

**예시**: `Close #123` or `Resolves: #456`

**💡 표준 및 심화 가이드**더 자세한 규격과 자동화 도구에 대한 정보는 다음 문서를 참고하세요.
• 표준 스펙 확인: **Conventional Commits 공식 문서**
• 이모지 활용: Gitmoji 가이드

https://www.conventionalcommits.org/ko/v1.0.0/