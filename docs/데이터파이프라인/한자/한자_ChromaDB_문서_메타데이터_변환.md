# 한자 ChromaDB Document/Metadata 변환 작업 기록

## 현행 구현 기준 보완 (2026-06-16)

> 문서 상태: 운영 한자 2,420건의 ChromaDB 문서/메타데이터 변환 기록. 현재 산출물과 잘 일치한다.

이 변환 산출물은 Pipeline Server 내부 RAG 경로의 기준 데이터로 사용된다. Open WebUI에서 한자 조건 추천 질문이 들어오면 LangGraph `internal_rag` 경로가 `rag_server.py`를 통해 `data/chroma`의 `hanja_col`을 조회하며, 본 문서의 `hanja_documents.json`은 해당 컬렉션의 핵심 입력 데이터다.

| 항목 | 현재 기준 |
|---|---|
| 입력 | `data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json` 2,420건 |
| 출력 | `data/processed/hanja_documents.json` 2,420건 |
| 검증 | `data/processed/hanja_documents_validation.json` 기준 issues 0 |
| 사용처 | `rag_server.py`, `graph_server.py`, `index_hanja_neo4j.py` |
| 후속 주의 | 현재 Chroma `hanja_col` 전체 2,438건은 후속 성씨 보조 18건 추가 때문이며, 이 문서는 운영 한자 2,420건 변환 기록이다 |

**작성일**: 2026-06-11  
**작업 범위**: 정제된 한자 JSON을 ChromaDB 적재용 `document + metadata` 구조로 변환  
**입력 기준 파일**: `data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json`

## 1. 작업 목적

정제 완료된 한자 JSON을 원본 기준 데이터로 유지하면서, ChromaDB에 적재하기 좋은 문서형 데이터로 변환했다.

ChromaDB에서는 임베딩 검색에 사용할 자연어 `document`와 조건 검색/필터링에 사용할 구조화된 `metadata`가 함께 필요하다. 따라서 기존 한자 메타데이터를 그대로 보존하면서, 검색 문맥을 담은 한글 문장을 별도로 생성했다.

## 2. 참고한 기존 예시

기존 전처리 산출물 중 다음 파일의 구조를 참고했다.

- `data/processed/ohaeng_documents.json`
- `data/processed/suri_documents.json`

다만 한자 데이터는 한글 음, 한자, 유니코드, 뜻, 획수, 발음오행, 자원오행이 모두 중요하므로 각 값을 자연어 문장과 metadata 양쪽에 반영했다.

## 3. 생성 스크립트

변환 로직은 다음 파일로 작성했다.

- `src/preprocess/build_hanja_documents.py`

재실행 명령:

```powershell
python src\preprocess\build_hanja_documents.py
```

기본 입출력 경로:

| 구분 | 경로 |
| --- | --- |
| 입력 | `data/processed/unihan_maping/hanja_unicode_ohaeng_verified_corrected.json` |
| 출력 | `data/processed/hanja_documents.json` |
| 검증 리포트 | `data/processed/hanja_documents_validation.json` |

## 4. 변환 구조

각 항목은 다음 구조로 변환했다.

| 필드 | 설명 |
| --- | --- |
| `id` | ChromaDB 문서 ID. `hanja_{profile_id}` 형식 |
| `document` | 임베딩 검색에 사용할 자연어 문장 |
| `metadata` | 필터링과 원본 추적에 사용할 구조화 데이터 |

예시:

```json
{
  "id": "hanja_OHE-00001",
  "document": "한자 加(가)은 뜻이 '더할'인 인명용 한자이다. 유니코드는 U+52A0, 획수는 5획, 발음오행은 목, 자원오행은 목이다.",
  "metadata": {
    "type": "hanja",
    "collection": "hanja_profiles",
    "source": "hanja_unicode_ohaeng_verified_corrected.json",
    "profile_id": "OHE-00001",
    "hangul": "가",
    "hanja": "加",
    "unicode": "U+52A0",
    "sound_meaning": "더할",
    "strokes": 5,
    "sound_ohaeng": "목",
    "resource_ohaeng": "목",
    "is_person_name_hanja": true
  }
}
```

## 5. 처리 과정

1. 원본 JSON을 읽어 전체 데이터가 리스트 구조인지 확인했다.
2. 필수 필드 8개가 모든 행에 존재하는지 검증했다.
3. `profile_id` 중복 여부를 확인했다.
4. `profile_id`가 `OHE-00001` 형식의 순번 구조를 따르는지 확인했다.
5. 한자가 단일 문자로 유지되는지 확인했다.
6. `unicode` 값이 실제 한자 코드포인트와 일치하는지 검증했다.
7. `hangul`, `sound_meaning`, `strokes`, `sound_ohaeng`, `resource_ohaeng`의 기본 유효성을 확인했다.
8. 각 행을 ChromaDB용 `id`, `document`, `metadata` 구조로 변환했다.
9. 변환된 metadata가 원본 8개 필드와 일치하는지 다시 비교했다.
10. 검증 결과를 `hanja_documents_validation.json`에 저장했다.
11. 검증 문제가 없을 때만 `hanja_documents.json`을 저장했다.

## 6. 원본 보존 방식

입력 파일인 `hanja_unicode_ohaeng_verified_corrected.json`은 수정하지 않았다.  
변환 스크립트는 원본 JSON을 읽기만 하고, 별도 산출물인 `hanja_documents.json`과 `hanja_documents_validation.json`을 생성한다.

원본 파일 SHA256 해시:

```text
11CC718469B0F3255C76078125FB4366ABCB5C8E1648BDCEA9B9F70F679DB9C0
```

## 7. 최종 산출물

| 파일 | 설명 | 건수 |
| --- | --- | --- |
| `data/processed/hanja_documents.json` | ChromaDB 적재용 document/metadata JSON | 2420 |
| `data/processed/hanja_documents_validation.json` | 변환 검증 리포트 | 1 |
| `src/preprocess/build_hanja_documents.py` | 변환 및 검증 스크립트 | - |

## 8. 검증 결과

`hanja_documents_validation.json` 기준 검증 결과는 다음과 같다.

| 항목 | 값 |
| --- | --- |
| `status` | `ok` |
| `ok` | `true` |
| `source_count` | 2420 |
| `document_count` | 2420 |
| `issue_count` | 0 |

추가 확인 결과:

- metadata와 원본 8개 필드 비교 결과 mismatch: 0
- 첫 번째 문서 ID: `hanja_OHE-00001`
- 마지막 문서 ID: `hanja_OHE-02420`
- 원본 JSON 해시 변경 없음

## 9. 후속 사용 방향

이 산출물은 ChromaDB 실제 인덱싱 직전 단계의 입력 파일로 사용한다.

후속 인덱싱 단계에서는 `hanja_documents.json`의 각 항목을 다음처럼 매핑하면 된다.

| ChromaDB 입력 | 사용할 값 |
| --- | --- |
| `ids` | 각 항목의 `id` |
| `documents` | 각 항목의 `document` |
| `metadatas` | 각 항목의 `metadata` |

실제 ChromaDB 컬렉션 생성 및 적재 작업은 별도 인덱싱 단계에서 진행한다.
