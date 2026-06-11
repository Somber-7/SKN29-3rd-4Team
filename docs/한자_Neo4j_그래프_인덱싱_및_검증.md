# 한자 Neo4j 그래프 인덱싱 및 검증 작업 기록

**작성일**: 2026-06-11  
**작업 범위**: 한자 document/metadata JSON을 Neo4j 그래프 구조로 적재하고 관계 정합성 검증  
**기준 문서**: `docs/프로젝트_아이디어_작명.md`  
**실행 스크립트**: `src/graph/index_hanja_neo4j.py`

## 1. 작업 목적

프로젝트 설계 문서의 Graph DB 방향에 맞춰, 인명용 한자와 오행, 획수, 법적 허용 근거를 Neo4j 관계 그래프로 구성하는 것이 목적이다.

ChromaDB는 의미 기반 검색에 강하지만, 특정 한자가 어떤 오행에 속하는지, 인명용 한자 규정에 의해 허용되는지, 오행 간 상생/상극 관계가 어떻게 연결되는지 같은 명시적 관계 검증에는 Neo4j가 적합하다.

따라서 `data/processed/hanja_documents.json`을 기준 데이터로 삼아 Neo4j에 결정론적인 그래프 관계를 생성했다.

## 2. 입력 데이터 기준

| 구분 | 경로 |
| --- | --- |
| Neo4j 입력 기준 | `data/processed/hanja_documents.json` |
| 한자 정제 원본 | `data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json` |
| 오행 원천 표 | `data/raw/ohaeng/자원오행 발음오행구분표.xlsx` |
| 프로젝트 설계 문서 | `docs/프로젝트_아이디어_작명.md` |
| 진행 체크 문서 | `docs/진행_체크리스트.md` |

Raw 파일은 Neo4j 스크립트에서 직접 재처리하지 않았다. Raw 파일은 교차검증과 원천 확인 용도로 사용하고, 실제 적재는 전처리 완료된 `hanja_documents.json`을 기준으로 했다.

이유는 ChromaDB와 Neo4j가 같은 기준 데이터를 바라봐야 검색 결과와 그래프 검증 결과가 서로 어긋나지 않기 때문이다.

## 3. 그래프 스키마

### 3.1. 노드

| 노드 | 설명 | 최종 건수 |
| --- | --- | --- |
| `Hanja` | 인명용 한자 개별 항목 | 2420 |
| `Sound` | 한글 음 | 413 |
| `Stroke` | 획수 | 27 |
| `Category:Ohaeng` | 목/화/토/금/수 오행 범주 | 5 |
| `Law` | 인명용 한자 허용 근거 | 1 |

### 3.2. 관계

| 관계 | 설명 | 기대 건수 |
| --- | --- | --- |
| `HAS_SOUND` | 한자와 한글 음 연결 | 2420 |
| `HAS_STROKES` | 한자와 획수 연결 | 2420 |
| `BELONGS_TO {kind: 'sound_ohaeng'}` | 한자와 발음오행 연결 | 2420 |
| `BELONGS_TO {kind: 'resource_ohaeng'}` | 한자와 자원오행 연결 | 2420 |
| `PERMITTED_BY` | 한자와 인명용 허용 근거 연결 | 2420 |
| `GENERATES` | 오행 상생 관계 | 5 |
| `CONTROLS` | 오행 상극 관계 | 5 |

`BELONGS_TO` 전체 기대값은 발음오행 2,420건과 자원오행 2,420건을 합친 4,840건이다.

## 4. 스크립트 처리 방식

`src/graph/index_hanja_neo4j.py`는 다음 흐름으로 구성했다.

1. `.env`에서 Neo4j 연결 정보를 읽는다.
2. `data/processed/hanja_documents.json`을 로드한다.
3. `id`, `document`, `metadata` 구조를 검증한다.
4. 필수 metadata 필드와 오행 값 유효성을 확인한다.
5. dry-run 상태에서는 Neo4j에 쓰지 않고 적재 계획만 출력한다.
6. `--check-connection` 옵션으로 서버 연결만 검증할 수 있게 했다.
7. `--execute` 옵션이 있을 때만 실제 Neo4j에 적재한다.
8. dataset 값을 키에 포함하여 팀원이 만든 다른 그래프와 충돌하지 않게 했다.

기본 검증 명령:

```powershell
python src\graph\index_hanja_neo4j.py
```

Neo4j 연결 확인 명령:

```powershell
python src\graph\index_hanja_neo4j.py --check-connection
```

실제 적재 명령:

```powershell
python src\graph\index_hanja_neo4j.py --execute
```

## 5. 트러블슈팅

### 5.1. `BELONGS_TO` 3건 초과 문제

초기 검증에서 다음 상태가 확인되었다.

| 관계 | 실제 | 기대 | 상태 |
| --- | --- | --- | --- |
| `HAS_SOUND` | 2420 | 2420 | OK |
| `HAS_STROKES` | 2420 | 2420 | OK |
| `BELONGS_TO` | 4843 | 4840 | 확인 필요 |
| `PERMITTED_BY` | 2420 | 2420 | OK |
| `GENERATES` | 5 | 5 | OK |
| `CONTROLS` | 5 | 5 | OK |

원인은 오행 데이터 3건을 수정한 뒤 Neo4j에 다시 적재하면서, 기존의 잘못된 `BELONGS_TO` 관계가 삭제되지 않고 남아 있었기 때문이다.

`MERGE`는 현재 값 기준의 새 관계를 만들 수는 있지만, 이전 값 기준의 관계를 자동 삭제하지 않는다. 따라서 데이터가 바뀐 뒤 재적재하면 한자 1건에 같은 종류의 오행 관계가 2개 이상 남을 수 있다.

### 5.2. 해결 방식

`UPSERT_HANJA_BATCH` Cypher에 현재 값과 다른 기존 오행 관계를 삭제하는 로직을 추가했다.

핵심 처리 방식은 다음과 같다.

```cypher
WITH h, row, sound_cat, resource_cat
OPTIONAL MATCH (h)-[old_sound_rel:BELONGS_TO {kind: 'sound_ohaeng'}]->(old_sound_cat:Category:Ohaeng {dataset: row.dataset})
WHERE old_sound_cat.name <> row.sound_ohaeng
WITH h, row, sound_cat, resource_cat, collect(old_sound_rel) AS old_sound_rels
FOREACH (rel IN old_sound_rels | DELETE rel)

WITH h, row, sound_cat, resource_cat
OPTIONAL MATCH (h)-[old_resource_rel:BELONGS_TO {kind: 'resource_ohaeng'}]->(old_resource_cat:Category:Ohaeng {dataset: row.dataset})
WHERE old_resource_cat.name <> row.resource_ohaeng
WITH h, row, sound_cat, resource_cat, collect(old_resource_rel) AS old_resource_rels
FOREACH (rel IN old_resource_rels | DELETE rel)
```

이 로직은 다음 목적을 가진다.

- 현재 발음오행과 다른 과거 발음오행 관계 삭제
- 현재 자원오행과 다른 과거 자원오행 관계 삭제
- 삭제 후 현재 값 기준 관계를 다시 `MERGE`
- 같은 스크립트를 여러 번 실행해도 관계가 누적되지 않게 유지

### 5.3. 처리 원칙

Neo4j 재적재 과정에서 다른 팀원이 만든 파일이나 폴더는 수정하지 않았다. 작업 범위는 다음으로 제한했다.

- `data/processed/hanja_documents.json` 기준 검증
- `src/graph/index_hanja_neo4j.py` 스크립트 보강
- Neo4j 서버에 적재된 stale relationship 정리
- ChromaDB 캐시나 Git 추적 대상과 충돌하지 않도록 산출물 분리

## 6. 최종 검증 결과

Neo4j 서버 적재 후 최종 기대 상태는 다음과 같이 정리했다.

| 항목 | 건수 |
| --- | --- |
| `Hanja` | 2420 |
| `Sound` | 413 |
| `Stroke` | 27 |
| `Category:Ohaeng` | 5 |
| `Law` | 1 |
| `HAS_SOUND` | 2420 |
| `HAS_STROKES` | 2420 |
| `BELONGS_TO` | 4840 |
| `PERMITTED_BY` | 2420 |
| `GENERATES` | 5 |
| `CONTROLS` | 5 |

추가 검증 항목은 다음과 같다.

| 검증 항목 | 결과 |
| --- | --- |
| 한자별 `BELONGS_TO` 관계 수 | 각 2건 |
| `Hanja` 속성과 metadata 비교 | mismatch 0 |
| 현재 값과 다른 stale relationship | 0 |
| 대표 수정 3건 조회 | 정상 |

## 7. 후속 사용 방향

이 Neo4j 그래프는 LangGraph의 `graph_db` 경로에서 사용한다.

주요 사용 시나리오는 다음과 같다.

1. 특정 한자가 인명용 한자인지 확인한다.
2. 특정 한자의 발음오행과 자원오행을 조회한다.
3. 목/화/토/금/수 오행의 상생/상극 관계를 탐색한다.
4. 사용자가 제시한 오행, 획수, 음, 뜻 조건에 맞는 한자 후보를 필터링한다.

다음 단계에서는 `src/mcp/graph_server.py`를 통해 이 Neo4j 그래프를 LangGraph `graph_db_node`에 연결하면 된다.
