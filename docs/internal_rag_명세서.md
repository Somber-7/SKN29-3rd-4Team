# internal_rag 구현 명세서

> 담당 파트: 4단계 LangGraph StateGraph — `internal_rag_node` / 5단계 MCP 서버 — `rag_server.py`
> 최종 업데이트: 2026-06-12

---

## 1. 담당 범위 요약

| 단계 | 파일 | 역할 |
|---|---|---|
| 4단계 — LangGraph StateGraph | `src/graph/naming_graph.py` | `internal_rag_node` — 질문 분석 후 ChromaDB 검색 실행 |
| 5단계 — MCP 서버 | `src/mcp/rag_server.py` | `search_rag()` Tool — ChromaDB 의미 검색 + 조건 필터 + 출처 포맷 |

---

## 2. 전체 처리 흐름

```
사용자 질문 입력
       ↓
llm_router_node  (naming_graph.py)
  LLM이 "internal_rag 필요" 판단
  → {"next": "internal_rag", "reason": "..."}
       ↓
internal_rag_node  (naming_graph.py)
  질문 키워드 분석 → 해당 컬렉션 선택
  → rag_server.search_rag(query, collection) 호출
       ↓
search_rag()  (rag_server.py)
  1. 컬렉션 유효성 검증
  2. hanja_col인 경우 획수/오행 조건 파싱 (_parse_hanja_conditions)
  3. ChromaDB 벡터 검색 (+ 조건 필터 적용)
  4. 결과 포맷팅 — 출처 태그 삽입
  → 포맷된 문자열 반환
       ↓
internal_rag_node
  결과를 state["context"]에 누적
  → llm_router_node로 복귀 (ReAct 루프)
       ↓
llm_router_node
  "정보 충분" 판단 시 generate 선택
       ↓
generate_node
  context + 질문 → LLM 최종 답변 생성
  출처 태그 [한자: 자원오행표 XX오행] 포함
```

---

## 3. internal_rag_node 상세

**파일**: `src/graph/naming_graph.py:158-178`

### 3-1. 컬렉션 선택 로직

질문 문자열에서 키워드를 감지해 검색 대상 컬렉션을 결정합니다.
복합 질문은 여러 컬렉션을 동시에 검색하고 결과를 모두 누적합니다.

| 감지 키워드 | 검색 컬렉션 |
|---|---|
| `수리`, `4격`, `원격`, `형격`, `이격`, `정격`, `운세` | `suri_col` |
| `오행`, `상생`, `상극`, `木`, `火`, `土`, `金`, `水` | `ohaeng_col` |
| `한자`, `획수`, `뜻`, `음`, `독음`, `추천` | `hanja_col` |
| `법령`, `조항`, `조문`, `출생신고`, `인명용` | `law_col` |
| `순우리말`, `우리말`, `이름 뜻`, `이름 추천` | `urimalsam_col` |
| 위 키워드 없음 (기본값) | `hanja_col` |

### 3-2. 상태(State) 갱신 항목

```python
return {
    **state,
    "context"    : 기존 context + "\n\n[internal_rag 결과]\n" + "\n\n".join(results),
    "next_action": "generate",       # llm_router가 덮어쓰므로 실질적으로 무의미 (→ 6절 제약 참조)
    "used_tools" : state.get("used_tools", []) + ["internal_rag"],
}
```

### 3-3. ReAct 루프에서의 위치

```
graph.add_edge("internal_rag", "llm_router")  # 실행 후 반드시 llm_router로 복귀
```

tool 노드 실행 후 llm_router로 돌아가기 때문에, LLM이 추가 Tool이 필요하다고 판단하면
`sql_db`, `external_api` 등을 이어서 실행할 수 있습니다 (다중 의도 처리).

---

## 4. rag_server.py 상세

**파일**: `src/mcp/rag_server.py`

### 4-1. 초기화 (모듈 로드 시 1회 실행)

```
ChromaDB PersistentClient  →  data/chroma/  (SQLite 기반 벡터 DB)
임베딩 모델                →  jhgan/ko-sroberta-multitask  (로컬, sentence-transformers)
```

모듈이 임포트될 때 클라이언트와 임베딩 모델이 한 번만 초기화됩니다.
매 검색 호출마다 모델을 다시 로드하지 않습니다.

### 4-2. 등록된 컬렉션 (`_COLLECTIONS`)

| 컬렉션명 | 내용 | 인덱싱 문서 수 |
|---|---|---|
| `suri_col` | 81수리 획수별 운세 해설 | 81건 |
| `ohaeng_col` | 오행 조합(木木木~水水水) 운세 | 125건 |
| `hanja_col` | 한자별 뜻·획수·오행·인명용 여부 | 2,420건 |
| `law_col` | 가족관계등록법·대법원규칙 조문 | 248건 |
| `urimalsam_col` | 순우리말 이름 | 301건 |

### 4-3. `_parse_hanja_conditions()` — 조건 파싱 (`hanja_col` 전용)

**파일**: `src/mcp/rag_server.py:52-79`

벡터 유사도 검색만으로는 수치 조건("정확히 10획")을 걸 수 없습니다.
쿼리 문자열에서 조건을 자동 파싱해 ChromaDB `where` 필터로 변환합니다.

**감지 패턴**

| 조건 | 실제 정규식 | 예시 | 변환 결과 |
|---|---|---|---|
| 획수 | `(\d+)\s*획` | "10획짜리" | `{"strokes": 10}` |
| 오행 | `([木火土金水목화토금수])오행` | "목오행", "木오행" 모두 대응 | `{"resource_ohaeng": "목"}` |

> 오행 패턴은 한글(목화토금수)과 한자(木火土金水) 문자를 하나의 문자 클래스 `[...]`에 넣어 단일 정규식으로 처리합니다.
> 한자로 감지된 경우 `_HANJA_TO_OHAENG` 딕셔너리로 한글로 변환합니다.

**결합 방식**

```python
# 조건 1개
where = {"strokes": 10}

# 조건 2개 → ChromaDB $and 연산자로 결합
where = {"$and": [{"strokes": 10}, {"resource_ohaeng": "목"}]}
```

**반환값**: `(where_dict or None, 조건_설명_문자열)`

### 4-4. `search_rag()` — 핵심 검색 Tool

**파일**: `src/mcp/rag_server.py:86-180`

**시그니처**

```python
def search_rag(query: str, collection: str, n_results: int = 5) -> str
```

| 파라미터 | 설명 |
|---|---|
| `query` | 검색 질문 (자연어 그대로) |
| `collection` | 검색 대상 컬렉션 이름 |
| `n_results` | 반환 문서 수 (기본 5, 최대 10) |

**처리 단계**

```
1. collection 유효성 검증 (_COLLECTIONS에 없으면 오류 반환)
2. ChromaDB 컬렉션 객체 가져오기 (_get_collection)
   └─ 컬렉션 미존재 시 "인덱싱 필요" 메시지 반환
3. hanja_col인 경우: _parse_hanja_conditions() 호출 → where 조건 생성
4. col.query(query_texts=[query], n_results=n_results, [where=...])
   └─ where 조건이 None이면 where 파라미터 자체를 전달하지 않음
      → **({"where": where} if where else {}) 패턴 사용
5. 결과 포맷팅 (컬렉션별 분기)
```

**`where` 파라미터 조건부 전달 방식**

```python
# where=None을 넘기면 ChromaDB 오류 발생
# 조건이 있을 때만 where 키워드 인자를 삽입
**({"where": where} if where else {})
```

### 4-5. 출력 포맷 — `hanja_col`

`generate_node`의 `_GENERATE_SYSTEM`이 요구하는 `[한자: 자원오행표]` 형식과 일치시킵니다.

```
[검색 결과] '밝고 지혜로운 한자' — hanja_col (5건) [조건 필터: 획수 10획, 자원오행 목]
답변 작성 시 각 항목의 [한자: 자원오행표] 태그를 그대로 포함하세요.

[1] 俊(준) | 획수: 10획 | 자원오행: 목 | 발음오행: 목 | 뜻: 준걸, 뛰어난 | 인명용: 예
    [한자: 자원오행표 목오행]
[2] 哲(철) | 획수: 10획 | 자원오행: 목 | 발음오행: 목 | 뜻: 밝을 | 인명용: 예
    [한자: 자원오행표 목오행]
```

**출처 태그 삽입 이유**

`generate_node`는 `context` 전체를 LLM에게 전달합니다.
LLM은 context에 포함된 태그 예시를 참고해 답변에 동일한 형식으로 출처를 인용합니다.
`_GENERATE_SYSTEM` 프롬프트에도 동일 형식이 명시되어 있어 일관성이 유지됩니다.

### 4-6. 출력 포맷 — 나머지 컬렉션

```
[suri_col] '수리 운세' 검색 결과 3건

  [1] 유사도: 0.91
      메타: number: 16 | gilhyung: 大吉 | ...
      내용: 16수는 덕망이 높고 ...
      [출처: suri_col]
```

내부 관리용 필드(`type`, `collection`, `source`)는 출력에서 제외합니다.

### 4-7. `list_collections()` Tool

**파일**: `src/mcp/rag_server.py:187-210`

ChromaDB에 실제로 생성된 컬렉션 목록과 각 문서 수를 반환합니다.
인덱싱 완료 여부 점검용입니다.

---

## 5. 점검 이력 및 수정 사항

| 일자 | 파일 | 문제 | 수정 내용 |
|---|---|---|---|
| 2026-06-12 | `rag_server.py:69` | 한자(木火土金水) 오행 입력 시 `where` 필터 미적용 | 정규식을 `[木火土金水목화토금수]` 단일 문자 클래스로 통합 + 한글 변환 맵 추가 |
| 2026-06-12 | `rag_server.py:150` | `hanja_col` 루프에서 `doc`, `dist` 미사용 변수 | `for i, meta in enumerate(metadatas, 1)` 으로 단순화 |
| 2026-06-12 | `rag_server.py:163` | 출처 태그 형식이 `_GENERATE_SYSTEM` 요구 형식과 불일치 | `[출처: hanja_col / ...]` → `[한자: 자원오행표 XX오행]` 로 통일 |

---

## 6. 제약 사항 및 미적용 항목

| 항목 | 내용 | 사유 |
|---|---|---|
| `src/graph/naming_graph.py` 수정 | tool 노드 내 `"next_action": "generate"` 설정 — 무의미하나 동작에 영향 없음 | `src/graph/` 수정 금지 제약 |
| `graph_db_node` 연결 | Neo4j 미구현 — 플레이스홀더 반환 중 | Neo4j 담당 팀원 작업 완료 후 연결 |
| `urimalsam_col` 포맷 | 범용 포맷 적용 중 (hanja_col 전용 구조화 포맷 없음) | 메타데이터 스키마 미확정 |

---

## 7. 관련 파일 경로

| 파일 | 경로 |
|---|---|
| LangGraph StateGraph | `src/graph/naming_graph.py` |
| RAG MCP 서버 | `src/mcp/rag_server.py` |
| ChromaDB 저장 경로 | `data/chroma/` |
| hanja_col 원본 데이터 | `data/processed/hanja_documents.json` |
| suri_col 원본 데이터 | `data/processed/suri_documents.json` |
| ohaeng_col 원본 데이터 | `data/processed/ohaeng_documents.json` |
| law_col 원본 데이터 | `data/processed/law_articles.json` |
| urimalsam_col 원본 데이터 | `data/processed/urimalsam_names.json` |
