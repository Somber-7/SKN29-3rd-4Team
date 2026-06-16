# 한자 Graph MCP 자연어 라우터 구현 및 검증

**작성일**: 2026-06-12  
**작업 범위**: `graph_server.py` 자연어 질의 라우터 추가, Neo4j Graph MCP Tool 검증, 한자 오행 산출물 정합성 보정  
**대상 파일**: `src/mcp/graph_server.py`

## 1. 작업 목적

Neo4j에 적재된 한자 그래프를 LangGraph 또는 MCP 호출 단계에서 더 쉽게 사용할 수 있도록 `graph_server.py`에 단일 진입 라우터를 추가했다.

기존에는 `check_graph_status`, `lookup_hanja`, `check_person_name_hanja`, `get_ohaeng_relations`, `recommend_hanja_by_ohaeng` 같은 개별 Tool을 직접 선택해야 했다. 이번 작업에서는 사용자의 자연어 질문을 받아 내부에서 적절한 Tool로 분기하는 `answer_graph_query(query: str, limit: int = 10)`를 추가했다.

이 방식은 `src/graph/naming_graph.py` 같은 팀원 작업 파일을 직접 수정하지 않고, Graph 조회 책임을 `src/mcp/graph_server.py` 안에 모아두기 위한 구조다.

## 2. 구현 내용

### 2.1. 자연어 라우터 추가

추가된 대표 함수는 다음과 같다.

| 함수 | 역할 |
| --- | --- |
| `_extract_profile_id` | `OHE-00001` 형식의 profile_id 추출 |
| `_extract_hanja_chars` | 질의 안의 한자 문자 추출 |
| `_extract_ohaeng` | `목/화/토/금/수` 또는 `木/火/土/金/水` 추출 |
| `_extract_strokes` | `8획` 같은 획수 조건 추출 |
| `_extract_hangul_sound` | 한글 음 조건 추출 |
| `_extract_meaning_keyword` | 뜻, 의미, 밝, 지혜 같은 의미 키워드 추출 |
| `_classify_graph_query` | 질의 유형을 Tool 이름과 파라미터로 분류 |
| `answer_graph_query` | 분류 결과에 따라 실제 Graph Tool 호출 |

### 2.2. 분기 규칙

현재 라우터는 다음 질의 유형을 처리한다.

| 질의 유형 | 호출 Tool |
| --- | --- |
| 그래프 상태, count, 적재 검증 | `check_graph_status` |
| profile_id, 한자, 한글 음 기준 조회 | `lookup_hanja` |
| 인명용 한자 허용 여부 | `check_person_name_hanja` |
| 오행 상생/상극 관계 | `get_ohaeng_relations` |
| 오행, 뜻, 획수, 음 조건 기반 추천 | `recommend_hanja_by_ohaeng` |

예시는 다음과 같다.

```text
Neo4j count
OHE-00730 한자 조회
牧 인명용 허용
목 오행 상생 상극
목 오행 한자 추천
```

## 3. 기존 구조와의 충돌 방지

이번 작업은 `graph_server.py` 내부에서만 Graph 조회 라우팅을 확장하는 방향으로 진행했다.

`src/graph/naming_graph.py`는 팀원이 LangGraph StateGraph 흐름을 구성하는 핵심 파일이므로 직접 수정하지 않았다. 따라서 이번 작업은 다음 구조를 유지한다.

1. `graph_server.py`는 Neo4j Graph 조회 Tool과 자연어 라우터를 제공한다.
2. `naming_graph.py`는 필요 시 나중에 `graph_server.py` Tool을 호출하도록 연결할 수 있다.
3. Graph 조회 로직 변경이 LangGraph 전체 상태 흐름에 직접 영향을 주지 않도록 분리한다.

## 4. 검증 순서

검증은 다음 순서로 진행했다.

### 4.1. 문법 검증

```powershell
C:\miniconda\envs\skn29-3rd\python.exe -m py_compile src\mcp\graph_server.py
```

결과: 정상 통과

### 4.2. 자체검증

```powershell
C:\miniconda\envs\skn29-3rd\python.exe -B src\mcp\graph_server.py --self-check
```

확인 결과:

| 항목 | 결과 |
| --- | --- |
| source_records | 2420 |
| Hanja 기대값 | 2420 |
| Sound 기대값 | 413 |
| Stroke 기대값 | 27 |
| Category:Ohaeng 기대값 | 5 |
| Law 기대값 | 1 |
| HAS_SOUND 기대값 | 2420 |
| HAS_STROKES 기대값 | 2420 |
| BELONGS_TO 기대값 | 4840 |
| PERMITTED_BY 기대값 | 2420 |
| GENERATES 기대값 | 5 |
| CONTROLS 기대값 | 5 |
| Tool 구조 검증 | OK |
| route_samples | OK |

자체검증은 Neo4j 서버에 실제 연결하지 않고 로컬 구조와 라우팅 규칙만 확인한다.

### 4.3. 실제 Neo4j Tool 호출 검증

`answer_graph_query`와 직접 Tool 호출을 함께 검증했다.

| 검증 질의 | 기대 동작 | 결과 |
| --- | --- | --- |
| `Neo4j count` | `check_graph_status`로 분기 | OK |
| `OHE-00730 한자 조회` | `lookup_hanja`로 분기 | OK |
| `牧 인명용 허용` | `check_person_name_hanja`로 분기 | OK |
| `목 오행 상생 상극` | `get_ohaeng_relations`로 분기 | OK |
| `목 오행 한자 추천` | `recommend_hanja_by_ohaeng`로 분기 | OK |

실제 조회 결과도 정상이다.

| profile_id | hanja | sound_ohaeng | resource_ohaeng | 결과 |
| --- | --- | --- | --- | --- |
| `OHE-00730` | `牧` | `수` | `목` | OK |
| `OHE-00739` | `錨` | `수` | `수` | OK |
| `OHE-01598` | `逸` | `토` | `목` | OK |

## 5. 오행 데이터 정합성 보정

Graph Tool 검증 과정에서 최종 JSON은 정상이나 일부 CSV와 ChromaDB metadata에 과거 오타가 남아 있는 것을 확인했다.

기준 데이터:

| 파일 | 역할 |
| --- | --- |
| `data/processed/hanja_documents.json` | ChromaDB와 Neo4j 공통 기준 document/metadata |
| `data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json` | 한자 유니코드 오행 최종 JSON |

수정한 항목:

| 대상 | 수정 전 | 수정 후 |
| --- | --- | --- |
| `OHE-00730 / 牧 / U+7267` | `resource_ohaeng=모` | `resource_ohaeng=목` |
| `OHE-00739 / 錨 / U+9328` | `resource_ohaeng=슴` | `resource_ohaeng=수` |
| `OHE-01598 / 逸 / U+9038` | `resource_ohaeng=모` | `resource_ohaeng=목` |

반영 대상:

| 파일 또는 저장소 | 처리 |
| --- | --- |
| `data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.csv` | 3건 수정 |
| `data/processed/unihan_maping/csv_tables/profile_resource_ohaeng.csv` | 3건 수정 |
| `data/chroma/chroma.sqlite3::hanja_col` | 3건 metadata 수정 |

## 6. 오행값 최종 검증 결과

검증 대상:

```text
data/processed/hanja_documents.json
data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json
data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.csv
data/processed/unihan_maping/csv_tables/profile_resource_ohaeng.csv
data/processed/unihan_maping/csv_tables/profile_sound_ohaeng.csv
data/processed/unihan_maping/csv_tables/profile_unicode.csv
data/chroma/chroma.sqlite3::hanja_col
```

검증 결과:

| 검증 항목 | 결과 |
| --- | --- |
| 허용 오행 외 값 | 0건 |
| `모`, `슴`, `모모슴`, `김`, `?` 의심값 | 0건 |
| 최종 JSON과 CSV 불일치 | 0건 |
| 최종 JSON과 ChromaDB 불일치 | 0건 |
| 유니코드 형식 오류 | 0건 |
| 한자 문자와 유니코드 코드포인트 불일치 | 0건 |

최종 분포:

| field | 금 | 목 | 수 | 토 | 화 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `sound_ohaeng` | 754 | 394 | 347 | 602 | 323 |
| `resource_ohaeng` | 429 | 674 | 395 | 376 | 546 |

## 7. 트러블슈팅

### 7.1. PowerShell 파이프 한글 인코딩 문제

ChromaDB metadata를 직접 수정하는 과정에서 Python 코드를 PowerShell 파이프로 넘길 때 한글 리터럴이 `?`로 깨질 수 있음을 확인했다.

초기 업데이트 출력에서 `목`, `수`가 `?`로 표시되어 즉시 재확인했고, `chr(0xBAA9)`, `chr(0xC218)` 방식으로 유니코드 코드포인트를 사용해 다시 덮어썼다.

최종 확인 결과:

```text
hanja_OHE-00730 -> 목
hanja_OHE-00739 -> 수
hanja_OHE-01598 -> 목
```

### 7.2. ChromaDB Git 반영 주의

`data/chroma/chroma.sqlite3`는 로컬 ChromaDB 저장소이므로 일반적으로 Git에 올리지 않는 방향이 더 안전하다.

다만 현재 저장소에서는 해당 파일이 이미 Git 추적 대상에 포함되어 있어, 커밋 시 포함 여부를 명시적으로 결정해야 한다. 데이터베이스 파일을 원격에 올리지 않을 계획이라면 `git add` 대상에서 제외해야 한다.

## 8. 최종 판단

`graph_server.py` 자연어 라우터는 문법 검증, 자체검증, 실제 Neo4j Tool 호출 검증을 통과했다.

또한 한자 오행 CSV와 ChromaDB metadata도 최종 JSON 기준과 일치하도록 보정되었으며, `목/화/수/금/토` 외 값은 남아 있지 않다.

따라서 `graph_server.py`의 Graph MCP 라우터 작업은 다음 단계인 Git 커밋 전 검증 기준을 만족한 상태다.
