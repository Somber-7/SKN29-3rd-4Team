# 작명 QA 시스템: 데이터 처리 서버 (db_server.py) 명세서

이 문서는 Naming QA System의 핵심 워커(Worker) 노드 역할을 수행하는 **`db_server.py` (수리/오행 연산 MCP 서버)**의 아키텍처와 제공하는 툴(Tool)들에 대한 명세서입니다.

---

## 1. 서버 개요 (Overview)
`db_server.py`는 FastMCP 기반으로 구축된 데이터 처리 전용 마이크로서비스입니다. 
LangGraph 라우터나 LLM 에이전트가 작명(성명학)과 관련된 복잡한 연산을 수행할 때, 필요한 도구(함수)들을 호출하여 구조화된 결과값을 받아갈 수 있도록 설계되었습니다.

*   **주요 기능:** 81수리 4격 계산, 길수(吉數) 조합 역산, 오행 상생/상극 궁합 분석, 2016~2026 출생신고 이름 빈도 통계 제공.
*   **활용 데이터:** `81suri.json`, `yinyang.json`, `2016_2026상위_출생신고_이름_현황.xls`

---

## 2. 핵심 아키텍처 (Core Architecture)

### 2.1. JSON 분리 반환 포맷 (State-Centric Design)
LLM과의 안정적인 연동을 위해, 모든 툴은 단순 문자열이 아닌 **구조화된 JSON 문자열**을 반환합니다.

```json
{
  "status": "success",
  "data": {"surname_strokes": 11, "hanja": "崔"},
  "message": "최(崔)씨는 원획법 기준 11획입니다."
}
```
*   `data`: LangGraph의 State(`extracted_params`)에 저장될 순수 데이터.
*   `message`: LLM이 프롬프트로 읽어들일 자연어 메세지.

### 2.2. 안전한 예외 처리 데코레이터 (`@safe_json_tool`)
LLM이 잘못된 인자(예: 숫자가 들어갈 곳에 문자열)를 넘겨 파이썬 서버가 강제 종료(Crash)되는 것을 막기 위한 방패입니다. 
모든 툴에 부착되어 있으며, 파이썬 에러(`TypeError` 등)가 발생하면 이를 가로채어 시스템 붕괴를 막고 규격화된 에러 JSON을 반환합니다.

### 2.3. 기계적 에러 피드백 (System-Level Critique)
에러 발생 시 모델이 앵무새처럼 에러 문구를 사용자에게 따라 말하는 현상(Parroting)을 방지하기 위해, 친절함을 배제하고 강압적이고 기계적인 프롬프트 형태의 에러를 반환합니다.
*   *예시:* `[SYSTEM ERROR] TypeError: ... ACTION REQUIRED: Check your input parameters.`

---

## 3. 제공하는 도구 목록 (MCP Tools)

### 🧰 1. `get_surname_strokes` (성씨 획수 조회)
*   **역할:** LLM이 사용자 입력에서 추출한 '한글 성씨'를 '원획법 한자 획수'로 정확하게 변환해 주는 딕셔너리 도구. (환각 방지용 필수 툴)
*   **입력:** `surname` (예: "최씨")
*   **출력:** `11` (JSON 형식)

### 🧰 2. `find_lucky_strokes` (吉수 조합 역산)
*   **역할:** 확정된 성씨 획수를 기반으로, 81수리 4격(원/형/이/정격)이 모두 吉(길)하게 떨어지는 완벽한 획수 조합을 역산합니다.
*   **입력:** `surname_strokes` (반드시 `get_surname_strokes`를 거친 숫자여야 함)
*   **출력:** `{"first_char_strokes": 7, "second_char_strokes": 14}` 형태의 조합 리스트

### 🧰 3. `calculate_name_suri` (81수리 4격 연산)
*   **역할:** 성씨와 이름 두 글자의 획수가 모두 주어졌을 때, 4격의 값과 길흉(吉凶), 세부 운세 풀이를 제공합니다.
*   **입력:** `surname_strokes`, `first_char_strokes`, `second_char_strokes`
*   **출력:** 각 격의 점수, 종합 판정(吉/凶) 등

### 🧰 4. `lookup_ohaeng_combo` (오행 조합 운세)
*   **역할:** 세 글자의 한자 오행(木,火,土,金,水)을 입력받아 상생/상극 흐름을 분석하고 운세 풀이를 제공합니다.
*   **입력:** `element1`, `element2`, `element3`
*   **출력:** 오행 관계도 및 흐름 판정(전체 상생, 부분 상생 등)

### 🧰 5. `search_name_stats` (이름 빈도 통계)
*   **역할:** 통계청/대법원 기반 2016~2026년 출생신고 데이터를 엑셀에서 검색하여, 특정 이름이 얼마나 흔하게 쓰이는지 빈도/순위를 알려줍니다.
*   **입력:** `name` (예: "민준")
*   **출력:** 해당 이름의 순위, 출생 건수 등 (정확히 일치하는 이름만 반환)

---
