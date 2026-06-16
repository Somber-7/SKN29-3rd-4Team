# Neo4j Graph MCP Server 구현 기록

**작성일**: 2026-06-11  
**작업 범위**: Neo4j 한자 그래프 조회용 MCP 서버 구현 및 자체검증  
**대상 파일**: `src/mcp/graph_server.py`

## 1. 작업 목적

Neo4j에 적재된 한자 그래프를 LangGraph에서 직접 사용할 수 있도록, 조회 전용 MCP 서버를 구현하는 것이 목적이다.

프로젝트 구조상 LangGraph의 `graph_db_node`는 Neo4j 연결 세부사항을 직접 알 필요가 없고, MCP Tool을 통해 필요한 조회만 호출하는 형태가 적합하다. 따라서 `src/mcp/graph_server.py`는 Neo4j 그래프를 읽기 전용 도구로 감싸는 역할을 한다.

## 2. 기준 데이터 및 연결 대상

| 구분 | 값 |
| --- | --- |
| Neo4j 입력 기준 | `data/processed/hanja_documents.json` |
| Neo4j 적재 스크립트 | `src/graph/index_hanja_neo4j.py` |
| MCP 서버 | `src/mcp/graph_server.py` |
| dataset | `hanja_profiles` |
| 설정 파일 | `.env` |

`.env`에서 사용하는 주요 값은 다음과 같다.

```text
NEO4J_URI
NEO4J_USER
NEO4J_PASSWORD
NEO4J_DATABASE
NEO4J_HANJA_DATASET
```

비밀번호와 같은 민감 정보는 문서나 출력에 남기지 않는 방향으로 처리했다.

## 3. 구현 원칙

`graph_server.py`는 다음 원칙으로 구현했다.

1. import 시점에는 Neo4j에 연결하지 않는다.
2. 실제 MCP Tool 호출 시점에만 Neo4j driver를 열고 닫는다.
3. `.env` 값은 함수 내부에서 다시 로드하여 최신 설정을 반영한다.
4. 조회 실패 시 사용자가 확인해야 할 항목을 메시지로 반환한다.
5. 한글 오행과 한자 오행 표기 모두 처리한다.
6. 서버 연결 전에도 `--self-check`로 로컬 구조 검증을 할 수 있게 한다.

이 방식은 팀원이 서버를 아직 띄우지 않았거나, Docker Neo4j 접속 정보가 확정되지 않은 상태에서도 코드 검증을 먼저 할 수 있게 해준다.

## 4. 제공 Tool 목록

| Tool | 역할 |
| --- | --- |
| `check_graph_status()` | Neo4j 노드/관계 수 검증 |
| `lookup_hanja()` | 한자, 한글 음, profile_id 기준 조회 |
| `check_person_name_hanja()` | 특정 한자의 인명용 허용 여부 확인 |
| `get_ohaeng_relations()` | 오행 상생/상극 관계 조회 |
| `recommend_hanja_by_ohaeng()` | 오행, 음, 뜻, 획수 조건 기반 한자 추천 |

## 5. Tool별 처리 내용

### 5.1. `check_graph_status`

Neo4j에 적재된 주요 노드와 관계 수를 확인한다. 기대값과 실제값을 비교하여 `OK` 또는 `확인 필요` 상태를 반환한다.

기대 관계 수는 다음과 같이 설정했다.

| 관계 | 기대값 |
| --- | --- |
| `HAS_SOUND` | 2420 |
| `HAS_STROKES` | 2420 |
| `BELONGS_TO` | 4840 |
| `PERMITTED_BY` | 2420 |
| `GENERATES` | 5 |
| `CONTROLS` | 5 |

### 5.2. `lookup_hanja`

`hanja`, `hangul`, `profile_id` 중 하나 이상의 조건으로 한자 노드를 조회한다.

예상 사용 예시는 다음과 같다.

```text
lookup_hanja(profile_id="OHE-00001")
lookup_hanja(hanja="加")
lookup_hanja(hangul="서")
```

### 5.3. `check_person_name_hanja`

특정 한자가 `PERMITTED_BY` 관계를 통해 인명용 한자 규정과 연결되어 있는지 확인한다.

응답에는 한자 속성과 허용 여부, 근거 Law 노드가 포함된다. 단, 실제 출생신고 가능 여부를 100% 보장하지 않는다는 면책 문구도 함께 반환하도록 했다.

### 5.4. `get_ohaeng_relations`

오행 하나를 입력하면 상생과 상극 관계를 조회한다.

입력은 다음 두 표기를 모두 허용한다.

```text
목, 화, 토, 금, 수
木, 火, 土, 金, 水
```

### 5.5. `recommend_hanja_by_ohaeng`

다음 조건을 조합하여 인명용 한자 후보를 조회한다.

| 조건 | 설명 |
| --- | --- |
| `sound_ohaeng` | 발음오행 |
| `resource_ohaeng` | 자원오행 |
| `hangul` | 한글 음 |
| `meaning_keyword` | 뜻 키워드 |
| `strokes` | 획수 |
| `limit` | 최대 반환 건수 |

조건을 하나도 입력하지 않으면 전체 조회를 막고 오류 메시지를 반환하도록 했다. 이는 너무 넓은 조회로 불필요한 결과가 반환되는 것을 방지하기 위한 처리다.

## 6. 자체검증 방식

서버 연결 전 검증은 다음 명령으로 수행한다.

```powershell
python -B src\mcp\graph_server.py --self-check
```

자체검증에서 확인하는 항목은 다음과 같다.

1. `data/processed/hanja_documents.json` 존재 여부
2. 입력 데이터 총 2,420건 여부
3. 필수 metadata 필드 존재 여부
4. `sound_ohaeng`, `resource_ohaeng` 값이 `목/화/토/금/수` 중 하나인지 확인
5. 모듈 import 시점에 `GraphDatabase.driver`가 호출되지 않는지 확인
6. 필수 Tool 함수들이 모두 존재하는지 확인

이 자체검증은 Neo4j 서버에 실제 연결하지 않는다.

## 7. 트러블슈팅

### 7.1. 서버 연결 전 검증 필요

Neo4j 서버가 Docker 또는 외부 서버에서 실행되는 경우, 스크립트 import 단계에서 바로 연결을 시도하면 팀원 환경에서 실패할 수 있다.

이를 방지하기 위해 연결을 lazy 방식으로 처리했다. 즉, `graph_server.py`를 import하거나 `--self-check`를 실행하는 것만으로는 Neo4j 연결이 열리지 않는다.

### 7.2. 환경 변수 누락 대응

`NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` 중 하나라도 없으면 어떤 값이 누락되었는지만 알려주고, 실제 비밀번호는 출력하지 않도록 했다.

### 7.3. 관계 수 불일치 대응

초기 검증에서 `BELONGS_TO`가 4,843건으로 나타났고 기대값 4,840건과 달랐다. 이는 오행 데이터 수정 후 기존 관계가 남아 있었기 때문이다.

이 문제는 `src/graph/index_hanja_neo4j.py`에서 stale `BELONGS_TO` 관계를 정리하는 방식으로 해결했다. 이후 `check_graph_status()` 기준 기대값은 다음과 같이 유지한다.

```text
BELONGS_TO = 2420 * 2 = 4840
```

## 8. 최종 검증 기준

`graph_server.py`가 정상적으로 동작하기 위한 기준은 다음과 같다.

| 항목 | 기대값 |
| --- | --- |
| Hanja | 2420 |
| Sound | 413 |
| Stroke | 27 |
| Category:Ohaeng | 5 |
| Law | 1 |
| HAS_SOUND | 2420 |
| HAS_STROKES | 2420 |
| BELONGS_TO | 4840 |
| PERMITTED_BY | 2420 |
| GENERATES | 5 |
| CONTROLS | 5 |

대표 조회 대상은 다음 3건을 포함한다.

| profile_id | hangul | hanja | sound_ohaeng | resource_ohaeng |
| --- | --- | --- | --- | --- |
| `OHE-00730` | `목` | `牧` | `수` | `목` |
| `OHE-00739` | `묘` | `錨` | `수` | `수` |
| `OHE-01598` | `일` | `逸` | `토` | `목` |

## 9. 후속 사용 방향

다음 단계는 `src/graph/naming_graph.py`의 `graph_db_node`에서 `graph_server.py` Tool을 호출하도록 연결하는 것이다.

연결 후 기대 흐름은 다음과 같다.

1. 사용자가 한자, 오행, 인명용 여부 관련 질문을 입력한다.
2. LangGraph Router가 `graph_db` 경로로 분기한다.
3. `graph_db_node`가 `graph_server.py` Tool을 호출한다.
4. Neo4j에서 관계 기반 결과를 조회한다.
5. 최종 답변 생성 단계에서 출처 라벨과 면책 문구를 포함한다.

이 문서 기준으로 보면 `graph_server.py` 구현과 Neo4j 조회 도구 준비는 완료되었다. `naming_graph.py`의 `graph_db_node` LangGraph 연결 및 최종 답변 형식 정리도 완료되었다 (2026-06-16).
