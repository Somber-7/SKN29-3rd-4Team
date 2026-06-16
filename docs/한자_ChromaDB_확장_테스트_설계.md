# 한자 ChromaDB 운영 후보군 비교 검증 설계

**작성일**: 2026-06-16  
**대상 범위**: 한자 ChromaDB 테스트 컬렉션, `naming_graph.py` 기반 작명 QA 비교 검증

## 1. 문서 목적

본 문서는 작명 QA 시스템에서 운영용 한자 검색 컬렉션을 어떤 범위로 구성할지 판단하기 위한 ChromaDB 비교 검증 설계를 정리한다.

현재 운영 기준 데이터는 `data/processed/hanja_documents.json` 2,420건이다. 반면 확장 후보 데이터인 `data/processed/hanja2_candidate_documents.json` 6,564건을 포함하면 테스트 기준 전체 한자 풀은 8,984건으로 확대된다.

검증의 핵심 목적은 다음과 같다.

1. 정제 완료 2,420건만 운영 기본값으로 사용하는 것이 타당한지 확인한다.
2. 확장 후보 6,564건을 포함했을 때 검색 폭이 넓어지는지 확인한다.
3. 확장 후보를 운영 DB에 바로 섞었을 때 발생할 수 있는 추천 품질, 설명 책임, 후보군 통제 리스크를 확인한다.
4. 기존 LangGraph 작명 파이프라인은 유지하고, 한자 ChromaDB만 테스트 컬렉션으로 전환해 비교한다.
5. 프로젝트 평가 시 “전체 한자 후보 중 왜 2,420건만 운영용으로 사용하는가”에 대한 근거 산출물을 확보한다.

## 2. 왜 2,420건을 운영 기본 hanja_col로 유지하는가

한자 작명 추천은 단순히 검색 가능한 한자 수가 많을수록 좋은 구조가 아니다. 추천 결과에는 한자, 음, 뜻, 획수, 발음오행, 자원오행이 함께 제시되어야 하며, 각 값이 일관되게 설명 가능해야 한다.

정제 완료 2,420건은 다음 조건을 만족하는 운영 기본 후보군이다.

| 항목 | 운영상 의미 |
| --- | --- |
| 유니코드 | 한자를 고유하게 식별하기 위한 기준값 |
| 뜻음 | 사용자에게 추천 이유를 설명하기 위한 핵심 문장 요소 |
| 획수 | 수리 및 작명 조건 판단에 필요한 수치값 |
| 발음오행 | 한글 음 기준 오행 판단에 필요한 값 |
| 자원오행 | 한자 자체의 오행 판단에 필요한 값 |
| 문서 구조 | ChromaDB와 LangGraph 파이프라인에서 바로 사용할 수 있는 `document + metadata` 구조 |

확장 후보 6,564건은 원본 범위에서 추가 확보한 한자이지만, 운영 기본 DB에 바로 포함하려면 후보군 사용 정책과 후처리 검증 책임이 필요하다. 따라서 본 테스트는 확장 후보를 배제하기 위한 목적이 아니라, 운영 기본값과 확장 검토 계층을 분리해야 하는지 판단하기 위한 검증이다.

## 3. 검증 범위와 제외 범위

이번 검증은 기존 운영 파이프라인을 변경하지 않는 것을 전제로 한다.

| 구분 | 처리 방식 |
| --- | --- |
| 운영 ChromaDB `data/chroma` | 수정하지 않음 |
| 기존 운영 컬렉션 `hanja_col` | 수정하지 않음 |
| `src/data/index_hanja_chroma.py` | 수정하지 않음 |
| `src/mcp/rag_server.py` | 수정하지 않음 |
| `src/graph/naming_graph.py` | 수정하지 않음 |
| `src/mcp/graph_server.py` | 수정하지 않음 |
| Neo4j | 이번 단계에서는 반영하지 않음 |
| 테스트 ChromaDB | `data/chroma_hanja_test`에 별도 생성 |
| 테스트 실행 | `tests` 폴더의 ipynb에서 수행 |

Neo4j는 세 방식 중 운영 채택 방향이 결정된 뒤 반영 여부를 판단한다. 현재 단계의 목적은 ChromaDB 후보 풀의 검색 품질과 작명 파이프라인 내 사용 가능성을 비교하는 것이다.

## 4. 테스트 ChromaDB 구조

테스트 전용 ChromaDB는 다음 경로를 사용한다.

```text
data/chroma_hanja_test/
```

이 경로는 운영 ChromaDB와 분리되어 있으므로 운영 데이터와 기존 `hanja_col`을 훼손하지 않고 비교 검증할 수 있다.

생성되는 테스트 컬렉션은 다음과 같다.

| 구분 | 컬렉션 | 적재 데이터 | 건수 | 목적 |
| --- | --- | --- | ---: | --- |
| A 후보군 | `hanja_base_test_col` | `hanja_documents.json` | 2,420 | 정제 완료 운영 기본값 검증 |
| B 후보군 | `hanja_expanded_test_col` | `hanja_documents.json` + `hanja2_candidate_documents.json` | 8,984 | 확장 후보를 한 컬렉션에 통합했을 때의 효과와 리스크 검증 |
| C 후보군 | `hanja_base_test_col` + `hanja_candidate_test_col` | 정제 완료군과 후보군 분리 검색 | 2,420 + 6,564 | 운영 기본군과 확장 후보군을 분리 운용할 수 있는지 검증 |
| 후보군 단독 | `hanja_candidate_test_col` | `hanja2_candidate_documents.json` | 6,564 | Hybrid 병합 및 후보군 보조 검색용 |

Hybrid 방식은 별도 통합 컬렉션을 만들지 않는다. `hanja_base_test_col`과 `hanja_candidate_test_col`을 각각 검색한 뒤 결과를 병합한다.

## 5. 테스트 컬렉션 준비 방식

테스트 컬렉션은 로컬 전용 ChromaDB 경로인 `data/chroma_hanja_test`에 준비한다. 이 경로의 ChromaDB 바이너리 파일은 재생성 가능한 실험 산출물이므로 Git 커밋 대상에서 제외한다.

주요 처리 흐름은 다음과 같다.

1. `data/processed/hanja_documents.json`을 로드한다.
2. `data/processed/hanja2_candidate_documents.json`을 로드한다.
3. 두 JSON이 ChromaDB 적재 가능한 `id`, `document`, `metadata` 구조인지 검증한다.
4. 테스트 전용 `data/chroma_hanja_test`에 PersistentClient를 생성한다.
5. 테스트 컬렉션 3개를 재생성한다.
6. `jhgan/ko-sroberta-multitask` 임베딩 모델로 500건 단위 배치 적재한다.
7. 컬렉션별 count와 샘플 조회 결과를 검증한다.
8. 컬렉션별 적재 건수와 샘플 조회 결과를 확인한다.

2026-06-16 기준 적재 검증 결과는 다음과 같다.

| 컬렉션 | 예상 건수 | 실제 건수 | 상태 |
| --- | ---: | ---: | --- |
| `hanja_base_test_col` | 2,420 | 2,420 | 통과 |
| `hanja_candidate_test_col` | 6,564 | 6,564 | 통과 |
| `hanja_expanded_test_col` | 8,984 | 8,984 | 통과 |

## 6. 비교 검증 노트북

작명 QA 비교 검증은 다음 노트북에서 수행한다.

```text
tests/한자_ChromaDB_운영후보군_비교검증.ipynb
```

이 노트북은 신규 작명 파이프라인을 만들지 않는다. 기존 `src/graph/naming_graph.py`의 LangGraph 흐름과 내부 프롬프트를 그대로 사용한다. 단, 노트북 실행 범위에서만 `rag_server`의 `hanja_col` 조회 대상을 테스트 컬렉션으로 전환한다.

비교 노트북의 핵심 원칙은 다음과 같다.

| 항목 | 내용 |
| --- | --- |
| 파이프라인 | 기존 `naming_graph.py`의 `build_graph()` 사용 |
| 프롬프트 | 기존 `naming_graph.py` 내부 SystemMessage, HumanMessage 사용 |
| 한자 검색 | `hanja_col`만 테스트 ChromaDB 컬렉션으로 전환 |
| 기타 검색 | 한자 외 컬렉션은 기존 `rag_server.search_rag` 흐름 유지 |
| 실행 추적 | `app.stream(..., stream_mode="updates")`로 LangGraph 노드 흐름 확인 |
| 시각 검증 | 실행 노드 흐름, 사용자 QA, 검색 근거를 노트북 화면에 표시 |
| 산출물 | JSON, Markdown 보고서, CSV 요약 파일 저장 |

주요 설정값은 다음과 같다.

| 항목 | 값 |
| --- | --- |
| LLM 모델 | `gpt-5.4-mini` |
| 임베딩 모델 | `jhgan/ko-sroberta-multitask` |
| 테스트 질문 | `김(金)씨 성에 어울리는 여자 한자 이름 3개 추천해줘` |
| LLM 실행 옵션 | `RUN_LLM=True`일 때 실제 답변 생성 |
| 검색 근거 확인 | `RUN_LLM=False`일 때 ChromaDB 검색 근거만 확인 |

## 7. A/B/C 비교 방식

동일한 질문을 세 가지 DB 구성에 적용한다.

| 구분 | 내부 모드 | 검색 방식 | 운영상 해석 |
| --- | --- | --- | --- |
| A 후보군 | Baseline | `hanja_base_test_col` 검색 | 정제 완료 2,420건만 사용한 운영 기본 후보군 |
| B 후보군 | Expanded | `hanja_expanded_test_col` 검색 | 정제 완료군과 확장 후보군을 한 컬렉션에 합친 실험군 |
| C 후보군 | Hybrid | `hanja_base_test_col` 우선 검색 후 `hanja_candidate_test_col` 보조 병합 | 운영 기본군과 후보군을 분리 운용하는 검토군 |

Hybrid 병합 기준은 다음과 같다.

1. 정제 완료 메인 데이터 검색 결과를 먼저 배치한다.
2. 확장 후보 데이터 검색 결과를 뒤에 배치한다.
3. `(hanja, hangul)` 기준 중복을 제거한다.
4. 병합된 검색 결과를 기존 `naming_graph.py` 파이프라인에 전달한다.

## 8. 판단 기준

본 검증은 외부 평가계획서 점수 산정이 아니라, 운영 DB 채택 여부를 판단하기 위한 내부 비교 기준을 사용한다.

| 기준 | 확인 내용 |
| --- | --- |
| 정제 신뢰도 | 추천에 사용되는 데이터가 정제 완료군인지, 후보군이 섞이는지 |
| 메타데이터 완성도 | 한자, 음, 뜻, 획수, 발음오행, 자원오행, 출처, 컬렉션 값이 채워져 있는지 |
| DB 근거성 | LLM 답변의 한자가 해당 DB 구성 안에 존재하는지 |
| 파이프라인 안정성 | `llm_router`, `internal_rag`, `generate` 등 기존 노드 흐름을 통과하는지 |
| 후보군 리스크 통제 | 확장 후보가 자동 추천 기본값에 섞일 때 설명 책임과 후처리 부담이 커지는지 |

이 기준에 따라 A/B/C 결과를 비교하고, 최종 보고서에서는 운영 적합성, 권고, 검색 근거, 후보군 근거, DB 미존재 한자, 상위 근거 미노출 한자를 함께 확인한다.

## 9. 저장 산출물

비교 노트북의 마지막 저장 셀은 다음 파일을 생성한다.

```text
tests/results/hanja_operational_candidate_comparison_{timestamp}_full.json
tests/results/hanja_operational_candidate_comparison_{timestamp}_report.md
tests/results/hanja_operational_candidate_comparison_{timestamp}_summary.csv
```

각 산출물의 역할은 다음과 같다.

| 산출물 | 역할 |
| --- | --- |
| `*_full.json` | 실행 설정, 모드별 결과, 검색 근거, trace, 운영 판단 근거를 모두 보관 |
| `*_report.md` | 프로젝트 평가 및 발표에 사용할 Markdown 보고서 |
| `*_summary.csv` | A/B/C 결과를 표 형태로 비교하기 위한 요약 파일 |

Markdown 보고서에는 다음 내용이 포함된다.

1. 핵심 결론
2. 왜 2,420개만 운영용 `hanja_col`로 쓰는지에 대한 설명
3. A/B/C 테스트 구성
4. 실행 결과 요약
5. 모드별 검색 근거와 사용자 QA 결과
6. 기존 LangGraph 파이프라인 trace
7. 반론 대응 문장

## 10. 기대 결론 구조

본 테스트의 목표는 무조건 확장 후보를 배제하는 것이 아니다. 운영 기본 DB와 확장 검토 DB의 역할을 분리할 수 있는지 확인하는 것이다.

현재 설계 기준에서 기대하는 결론 구조는 다음과 같다.

| 구분 | 기대 판단 |
| --- | --- |
| A 후보군 | 정제 완료 2,420건만 사용하므로 운영 기본값으로 가장 안정적 |
| B 후보군 | 검색 범위는 넓지만 후보군이 한 컬렉션에 섞여 자동 추천 통제 리스크가 증가 |
| C 후보군 | 후보군을 분리해 참고할 수 있어 검토용으로 유용하지만 운영 정책 추가 필요 |

따라서 기본 운영 방향은 A 후보군을 `hanja_col` 기준으로 유지하고, B/C 후보군은 확장 검토와 성능 비교를 위한 보조 근거로 사용하는 것이다.

프로젝트 평가 시 사용할 수 있는 핵심 설명은 다음과 같다.

> 전체 한자 후보가 약 9천 건 규모임에도 운영 DB를 2,420건으로 유지하는 이유는 단순 누락이 아니라 정제 기준 때문이다. 작명 추천은 한자 수보다 유니코드, 뜻음, 획수, 발음오행, 자원오행이 일관되게 검증된 데이터가 중요하다. 따라서 정제 완료 2,420건을 운영용 `hanja_col` 기본값으로 두고, 확장 후보군은 별도 후보 또는 검토 계층으로 유지하는 것이 안정적이다.

## 11. 후속 의사결정

테스트 결과 A 후보군이 가장 안정적으로 확인되면 기존 운영 `hanja_col`은 2,420건 기준을 유지한다.

B 또는 C 후보군을 실제 서비스 추천에 반영하려면 다음 사항을 추가로 결정해야 한다.

1. 확장 후보 한자의 운영 채택 기준
2. 후보군이 추천 결과에 포함될 때 사용자에게 표시할 설명 정책
3. 후보군 출처와 검수 책임 표기 방식
4. Neo4j 그래프 반영 여부
5. 기존 수리, 오행, 법령 검증 흐름과의 연결 방식

이번 단계에서는 ChromaDB 비교 검증까지만 수행하며, Neo4j 반영과 운영 파이프라인 변경은 후속 결정 이후 진행한다.
