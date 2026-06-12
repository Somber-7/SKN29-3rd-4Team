# 한자 ChromaDB 인덱싱 및 검증 작업 기록

**작성일**: 2026-06-11  
**작업 범위**: 한자 document/metadata JSON을 ChromaDB `hanja_col` 컬렉션으로 적재 및 검증  
**입력 기준 파일**: `data/processed/hanja_documents.json`  
**실행 스크립트**: `src/data/index_hanja_chroma.py`

## 1. 작업 목적

정제 및 문서화가 끝난 한자 데이터를 ChromaDB에 적재하여, 작명 QA 시스템에서 한자 뜻, 음, 획수, 발음오행, 자원오행 조건을 의미 기반으로 검색할 수 있도록 준비하는 것이 목적이다.

이 단계는 기존 `hanja_unicode_ohaeng_verified_corrected.json` 원본을 직접 수정하지 않고, 전처리 산출물인 `hanja_documents.json`을 ChromaDB 입력으로 사용하는 방식으로 진행했다.

## 2. 기준 데이터 및 컬렉션 구조

| 구분 | 값 |
| --- | --- |
| 입력 파일 | `data/processed/hanja_documents.json` |
| ChromaDB 저장 경로 | `data/chroma` |
| 컬렉션명 | `hanja_col` |
| 임베딩 모델 | `jhgan/ko-sroberta-multitask` |
| 배치 크기 | 500 |
| 검증 리포트 | `data/processed/hanja_chromadb_index_validation.json` |
| 에러 리포트 | `data/processed/hanja_chromadb_error_report.txt` |

입력 데이터는 다음 3개 필드를 가진 구조를 기준으로 했다.

| 필드 | 설명 |
| --- | --- |
| `id` | ChromaDB 문서 ID. `hanja_{profile_id}` 형식 |
| `document` | 임베딩 검색에 사용할 자연어 설명문 |
| `metadata` | 필터링과 원본 추적에 사용할 한자 속성 |

metadata에는 다음 필드가 포함된다.

```text
profile_id, hangul, hanja, unicode, sound_meaning,
strokes, sound_ohaeng, resource_ohaeng
```

## 3. 처리 과정

1. `data/processed/hanja_documents.json`을 읽어 리스트 구조인지 확인했다.
2. 각 항목이 `id`, `document`, `metadata` 구조를 갖는지 검증했다.
3. `metadata`에 한자 검색에 필요한 필수 필드가 모두 존재하는지 확인했다.
4. `id`와 `profile_id`가 `hanja_OHE-00001` 형식으로 일치하는지 확인했다.
5. `chromadb.PersistentClient`를 `data/chroma` 경로로 초기화했다.
6. 팀 공통 임베딩 모델인 `jhgan/ko-sroberta-multitask`를 사용했다.
7. 기존 `hanja_col` 컬렉션이 있으면 삭제 후 재생성했다.
8. 500건 단위로 총 2,420건을 배치 적재했다.
9. 적재 후 컬렉션 count와 대표 샘플 ID를 조회하여 저장 여부를 검증했다.

재실행 명령:

```powershell
python src\data\index_hanja_chroma.py
```

## 4. 트러블슈팅

### 4.1. 실행 환경 혼선

초기 검증 과정에서 `.venv` 환경과 프로젝트 기준 환경인 `skn29-3rd`가 혼재될 수 있는 상황이 있었다. 최종 검증은 프로젝트 작업 환경인 `skn29-3rd` 기준으로 진행하는 방향으로 정리했다.

이유는 ChromaDB, sentence-transformers, Neo4j driver 등 인덱싱 관련 패키지가 실행 환경마다 다르게 설치될 수 있기 때문이다. 동일한 스크립트라도 다른 Python 환경에서 실행하면 모듈 import 또는 모델 캐시 경로가 달라질 수 있다.

### 4.2. HuggingFace 접근 발생 가능성

`SentenceTransformerEmbeddingFunction(model_name="jhgan/ko-sroberta-multitask")`를 사용할 때, 해당 모델이 로컬 캐시에 없으면 sentence-transformers가 HuggingFace에서 모델을 내려받으려고 시도할 수 있다.

따라서 이 접근은 별도 데이터를 외부에 업로드하려는 동작이 아니라, 임베딩 모델 파일을 로컬에 확보하기 위한 모델 로드 과정으로 판단했다. 다만 네트워크 접근이 부담되는 경우에는 모델 캐시가 준비된 환경에서 실행해야 한다.

### 4.3. ChromaDB 캐시 및 SQLite 잠금 가능성

ChromaDB는 `data/chroma` 내부에 `chroma.sqlite3` 및 관련 파일을 생성한다. 실패한 실행에서 일부 파일이 남거나 파일 잠금이 발생하면 이후 실행에서 충돌할 수 있다.

이를 위해 스크립트에는 실패 시 JSON 리포트 대신 사람이 바로 읽을 수 있는 `hanja_chromadb_error_report.txt`를 남기도록 했다. 에러 리포트에는 실패 단계, 입력 파일, ChromaDB 경로, 모델명, 예상 원인, traceback을 기록한다.

처리 원칙은 다음과 같이 정리했다.

- 입력 원본인 `hanja_documents.json`은 수정하지 않는다.
- 실패한 ChromaDB 부분 생성물과 캐시만 정리한다.
- 팀원이 만든 다른 파일이나 폴더는 삭제하지 않는다.
- 재실행 전 `data/chroma` 내부가 Git에 올라가지 않도록 관리한다.

### 4.4. Git 업로드 제외

ChromaDB 실제 DB 파일은 재생성 가능한 인덱싱 산출물이므로 Git 관리 대상이 아니다. Git에는 스크립트와 검증 문서만 남기고, `data/chroma/` 경로는 업로드하지 않는 방향으로 정리했다.

## 5. 검증 결과

`data/processed/hanja_chromadb_index_validation.json` 기준 검증 결과는 다음과 같다.

| 항목 | 값 |
| --- | --- |
| `status` | `ok` |
| `ok` | `true` |
| `collection` | `hanja_col` |
| `embedding_model` | `jhgan/ko-sroberta-multitask` |
| `batch_size` | 500 |
| `source_count` | 2420 |
| `collection_count` | 2420 |
| `issue_count` | 0 |
| 샘플 ID | `hanja_OHE-00001`, `hanja_OHE-02420` |

추가로 `hanja_documents.json` 기준 metadata 분포를 확인했다.

| 항목 | 값 |
| --- | --- |
| 전체 문서 수 | 2420 |
| 한글 음 종류 | 413 |
| 획수 종류 | 27 |
| 발음오행 분포 | 목 394, 화 323, 수 347, 금 754, 토 602 |
| 자원오행 분포 | 목 674, 수 395, 화 546, 금 429, 토 376 |

## 6. 최종 산출물

| 파일 또는 경로 | 설명 |
| --- | --- |
| `src/data/index_hanja_chroma.py` | 한자 ChromaDB 인덱싱 스크립트 |
| `data/processed/hanja_chromadb_index_validation.json` | 인덱싱 검증 리포트 |
| `data/processed/hanja_chromadb_error_report.txt` | 실패 시 생성되는 TXT 에러 리포트. 현재 성공 상태에서는 생성되지 않음 |
| `data/chroma` | ChromaDB 실제 저장 경로. Git 업로드 제외 대상 |

## 7. 후속 사용 방향

`hanja_col`은 LangGraph의 `internal_rag` 경로에서 한자 뜻, 오행, 획수 조건을 의미 기반으로 검색할 때 사용한다.

정확한 관계 검증이나 인명용 허용 여부 확인은 ChromaDB 단독으로 처리하지 않고, 같은 기준 데이터에서 생성한 Neo4j 그래프와 함께 사용한다. ChromaDB는 의미 검색, Neo4j는 관계 검증을 담당하는 구조로 분리한다.
